#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
بوت متجر إلكتروني متكامل - Telegram Stars
نظام دفع آمن ومتطور مع حماية شاملة ضد الاحتيال
"""

import os
import json
import sqlite3
import logging
import hashlib
import time
import csv
import io
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
from contextlib import contextmanager
from functools import wraps
from collections import defaultdict
import threading

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    LabeledPrice, InputFile
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, PreCheckoutQueryHandler, ConversationHandler,
    filters, ContextTypes
)
from telegram.error import TelegramError
import re

# ============================================================================
# إعدادات البوت
# ============================================================================

BOT_TOKEN = "8139340651:AAF1AClfbBTLiOsHCSh2tmQlltKwLyfcT5E"
ADMIN_IDS = [8049455831]  # يمكن إضافة المزيد من المشرفين
DATABASE_FILE = "store_database.db"
PROVIDER_TOKEN = ""  # Telegram Stars لا تحتاج provider token

# إعدادات الأمان
MAX_REQUESTS_PER_MINUTE = 20
MAX_FAILED_PAYMENTS = 5
MAINTENANCE_MODE = False

# ============================================================================
# إعداد نظام التسجيل
# ============================================================================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot_logs.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# نظام الحماية من التكرار
# ============================================================================

class RateLimiter:
    """نظام حماية من السبام والطلبات المتكررة"""
    
    def __init__(self):
        self.requests = defaultdict(list)
        self.lock = threading.Lock()
    
    def is_allowed(self, user_id: int, max_requests: int = MAX_REQUESTS_PER_MINUTE) -> bool:
        """التحقق من عدد الطلبات المسموح بها"""
        with self.lock:
            now = time.time()
            minute_ago = now - 60
            
            # تنظيف الطلبات القديمة
            self.requests[user_id] = [
                req_time for req_time in self.requests[user_id]
                if req_time > minute_ago
            ]
            
            # التحقق من الحد الأقصى
            if len(self.requests[user_id]) >= max_requests:
                return False
            
            self.requests[user_id].append(now)
            return True
    
    def reset_user(self, user_id: int):
        """إعادة تعيين عداد المستخدم"""
        with self.lock:
            self.requests[user_id] = []

rate_limiter = RateLimiter()

# ============================================================================
# نظام قاعدة البيانات
# ============================================================================

class DatabaseManager:
    """إدارة قاعدة البيانات مع دعم المعاملات الآمنة"""
    
    def __init__(self, db_file: str):
        self.db_file = db_file
        self.lock = threading.Lock()
        self._init_database()
    
    @contextmanager
    def get_connection(self):
        """الحصول على اتصال بقاعدة البيانات"""
        conn = sqlite3.connect(self.db_file, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"خطأ في قاعدة البيانات: {e}")
            raise
        finally:
            conn.close()
    
    def _init_database(self):
        """إنشاء جداول قاعدة البيانات"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # جدول المستخدمين
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    balance INTEGER DEFAULT 0,
                    total_spent INTEGER DEFAULT 0,
                    total_purchases INTEGER DEFAULT 0,
                    referral_code TEXT UNIQUE,
                    referred_by INTEGER,
                    join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_banned INTEGER DEFAULT 0,
                    ban_reason TEXT,
                    language TEXT DEFAULT 'ar'
                )
            """)
            
            # جدول الفئات
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT,
                    icon TEXT DEFAULT '📁',
                    is_active INTEGER DEFAULT 1,
                    display_order INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # جدول المنتجات
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category_id INTEGER,
                    name TEXT NOT NULL,
                    description TEXT,
                    price_stars INTEGER NOT NULL,
                    original_price INTEGER,
                    type TEXT NOT NULL,
                    content TEXT,
                    stock INTEGER DEFAULT -1,
                    sold_count INTEGER DEFAULT 0,
                    is_limited INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    auto_delivery INTEGER DEFAULT 1,
                    min_purchase INTEGER DEFAULT 1,
                    max_purchase INTEGER DEFAULT 1,
                    discount_percentage INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (category_id) REFERENCES categories(id)
                )
            """)
            
            # جدول الأكواد
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS codes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER NOT NULL,
                    code_value TEXT NOT NULL,
                    is_used INTEGER DEFAULT 0,
                    used_by INTEGER,
                    used_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
                )
            """)
            
            # جدول الطلبات
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    payment_id TEXT UNIQUE NOT NULL,
                    telegram_payment_charge_id TEXT UNIQUE,
                    price INTEGER NOT NULL,
                    quantity INTEGER DEFAULT 1,
                    status TEXT DEFAULT 'pending',
                    delivery_status TEXT DEFAULT 'pending',
                    delivered_content TEXT,
                    ip_address TEXT,
                    user_agent TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id),
                    FOREIGN KEY (product_id) REFERENCES products(id)
                )
            """)
            
            # جدول السجلات الأمنية
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS security_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    log_type TEXT NOT NULL,
                    user_id INTEGER,
                    action TEXT NOT NULL,
                    details TEXT,
                    ip_address TEXT,
                    severity TEXT DEFAULT 'info',
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # جدول الكوبونات
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS coupons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT UNIQUE NOT NULL,
                    discount_type TEXT NOT NULL,
                    discount_value INTEGER NOT NULL,
                    max_uses INTEGER DEFAULT -1,
                    used_count INTEGER DEFAULT 0,
                    valid_from TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    valid_until TIMESTAMP,
                    is_active INTEGER DEFAULT 1,
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # جدول استخدامات الكوبونات
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS coupon_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    coupon_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    order_id INTEGER,
                    discount_amount INTEGER NOT NULL,
                    used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (coupon_id) REFERENCES coupons(id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id),
                    FOREIGN KEY (order_id) REFERENCES orders(id)
                )
            """)
            
            # جدول الإعدادات
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # جدول البث
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS broadcasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_text TEXT NOT NULL,
                    sent_count INTEGER DEFAULT 0,
                    failed_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP
                )
            """)
            
            # إنشاء فهارس لتحسين الأداء
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_referral ON users(referred_by)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_active ON products(is_active)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_codes_product ON codes(product_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_codes_used ON codes(is_used)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_payment ON orders(payment_id)")
            
            # إدراج إعدادات افتراضية
            cursor.execute("""
                INSERT OR IGNORE INTO settings (key, value) VALUES
                ('referral_reward', '10'),
                ('minimum_withdrawal', '100'),
                ('welcome_message', 'مرحباً بك في متجرنا! 🛍'),
                ('support_username', '@support'),
                ('store_name', 'متجر النجوم ⭐'),
                ('terms_text', 'الشروط والأحكام...')
            """)
            
            # إدراج فئة افتراضية
            cursor.execute("""
                INSERT OR IGNORE INTO categories (id, name, description, icon)
                VALUES (1, 'عام', 'المنتجات العامة', '📦')
            """)
            
            conn.commit()
            logger.info("تم تهيئة قاعدة البيانات بنجاح")

db = DatabaseManager(DATABASE_FILE)

# ============================================================================
# وظائف مساعدة
# ============================================================================

def admin_only(func):
    """ديكوريتر للتحقق من صلاحيات المشرف"""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.callback_query.answer("⛔ غير مصرح لك بهذا الإجراء", show_alert=True)
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

def rate_limit(func):
    """ديكوريتر للحماية من السبام"""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        
        if not rate_limiter.is_allowed(user_id):
            if update.callback_query:
                await update.callback_query.answer(
                    "⚠️ الرجاء الانتظار قليلاً قبل المحاولة مرة أخرى",
                    show_alert=True
                )
            return
        
        return await func(update, context, *args, **kwargs)
    return wrapper

def maintenance_check(func):
    """التحقق من وضع الصيانة"""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if MAINTENANCE_MODE and update.effective_user.id not in ADMIN_IDS:
            text = "🔧 البوت حالياً في وضع الصيانة\nالرجاء المحاولة لاحقاً"
            if update.callback_query:
                await update.callback_query.answer(text, show_alert=True)
            else:
                await update.message.reply_text(text)
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

def log_security_event(log_type: str, user_id: int, action: str, details: str = None, severity: str = 'info'):
    """تسجيل الأحداث الأمنية"""
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO security_logs (log_type, user_id, action, details, severity)
                VALUES (?, ?, ?, ?, ?)
            """, (log_type, user_id, action, details, severity))
    except Exception as e:
        logger.error(f"خطأ في تسجيل الحدث الأمني: {e}")

def generate_referral_code(user_id: int) -> str:
    """توليد كود إحالة فريد"""
    hash_input = f"{user_id}{time.time()}"
    return hashlib.md5(hash_input.encode()).hexdigest()[:8].upper()

def format_price(stars: int) -> str:
    """تنسيق السعر"""
    return f"{stars:,} ⭐"

def get_user_info(user_id: int) -> Optional[Dict]:
    """الحصول على معلومات المستخدم"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def create_or_update_user(user_id: int, username: str = None, first_name: str = None, referred_by: int = None):
    """إنشاء أو تحديث مستخدم"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        
        # التحقق من وجود المستخدم
        cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        exists = cursor.fetchone()
        
        if not exists:
            referral_code = generate_referral_code(user_id)
            cursor.execute("""
                INSERT INTO users (user_id, username, first_name, referral_code, referred_by)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, username, first_name, referral_code, referred_by))
            
            # مكافأة الإحالة
            if referred_by:
                cursor.execute("SELECT value FROM settings WHERE key = 'referral_reward'")
                reward = int(cursor.fetchone()[0])
                cursor.execute("""
                    UPDATE users SET balance = balance + ?
                    WHERE user_id = ?
                """, (reward, referred_by))
                log_security_event('referral', referred_by, f'مكافأة إحالة {reward} نجمة')
        else:
            cursor.execute("""
                UPDATE users 
                SET username = ?, first_name = ?, last_activity = CURRENT_TIMESTAMP
                WHERE user_id = ?
            """, (username, first_name, user_id))

# ============================================================================
# معالجات الأوامر الأساسية
# ============================================================================

@maintenance_check
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج أمر /start"""
    user = update.effective_user
    
    # التحقق من رابط الإحالة
    referred_by = None
    if context.args:
        try:
            ref_code = context.args[0]
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id FROM users WHERE referral_code = ?", (ref_code,))
                row = cursor.fetchone()
                if row and row[0] != user.id:
                    referred_by = row[0]
        except Exception as e:
            logger.error(f"خطأ في معالجة رابط الإحالة: {e}")
    
    # إنشاء أو تحديث المستخدم
    create_or_update_user(user.id, user.username, user.first_name, referred_by)
    
    # رسالة الترحيب
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'welcome_message'")
        welcome_msg = cursor.fetchone()[0]
        cursor.execute("SELECT value FROM settings WHERE key = 'store_name'")
        store_name = cursor.fetchone()[0]
    
    text = f"""
✨ {welcome_msg}

مرحباً بك في *{store_name}* 

🛍 يمكنك تصفح منتجاتنا والشراء باستخدام نجوم تيليجرام ⭐

استخدم الأزرار أدناه للبدء:
"""
    
    keyboard = [
        [InlineKeyboardButton("🛍 تصفح المنتجات", callback_data="browse_products")],
        [
            InlineKeyboardButton("⭐ مشترياتي", callback_data="my_purchases"),
            InlineKeyboardButton("🧾 طلباتي", callback_data="my_orders")
        ],
        [
            InlineKeyboardButton("👤 حسابي", callback_data="my_account"),
            InlineKeyboardButton("ℹ️ المساعدة", callback_data="help")
        ]
    ]
    
    # إضافة لوحة المشرف
    if user.id in ADMIN_IDS:
        keyboard.append([InlineKeyboardButton("🔐 لوحة الإدارة", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# ============================================================================
# نظام تصفح المنتجات
# ============================================================================

@rate_limit
@maintenance_check
async def browse_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة الفئات"""
    query = update.callback_query
    await query.answer()
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.*, COUNT(p.id) as product_count
            FROM categories c
            LEFT JOIN products p ON c.id = p.category_id AND p.is_active = 1
            WHERE c.is_active = 1
            GROUP BY c.id
            ORDER BY c.display_order, c.name
        """)
        categories = cursor.fetchall()
    
    if not categories:
        await query.edit_message_text(
            "📭 لا توجد منتجات متاحة حالياً\nالرجاء المحاولة لاحقاً",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")
            ]])
        )
        return
    
    text = "🛍 *اختر الفئة:*\n\n"
    keyboard = []
    
    for cat in categories:
        product_count = cat['product_count']
        text += f"{cat['icon']} {cat['name']} - ({product_count} منتج)\n"
        keyboard.append([
            InlineKeyboardButton(
                f"{cat['icon']} {cat['name']} ({product_count})",
                callback_data=f"category_{cat['id']}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

@rate_limit
@maintenance_check
async def show_category_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض منتجات الفئة"""
    query = update.callback_query
    await query.answer()
    
    category_id = int(query.data.split('_')[1])
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        
        # الحصول على معلومات الفئة
        cursor.execute("SELECT * FROM categories WHERE id = ?", (category_id,))
        category = cursor.fetchone()
        
        # الحصول على المنتجات
        cursor.execute("""
            SELECT * FROM products
            WHERE category_id = ? AND is_active = 1
            ORDER BY display_order, name
        """, (category_id,))
        products = cursor.fetchall()
    
    if not products:
        await query.edit_message_text(
            f"📭 لا توجد منتجات في فئة *{category['name']}* حالياً",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="browse_products")
            ]]),
            parse_mode='Markdown'
        )
        return
    
    text = f"🛍 *{category['icon']} {category['name']}*\n\n"
    keyboard = []
    
    for product in products:
        # حساب السعر مع الخصم
        final_price = product['price_stars']
        if product['discount_percentage'] > 0:
            final_price = int(final_price * (100 - product['discount_percentage']) / 100)
        
        # أيقونة نوع المنتج
        type_icons = {
            'file': '📄',
            'image': '🖼',
            'text': '📝',
            'code': '🔑',
            'balance': '💰'
        }
        type_icon = type_icons.get(product['type'], '📦')
        
        # حالة المخزون
        stock_text = ""
        if product['is_limited']:
            stock_text = f" | المخزون: {product['stock']}"
            if product['stock'] <= 0:
                stock_text += " ❌"
        
        # نص الخصم
        discount_text = ""
        if product['discount_percentage'] > 0:
            discount_text = f" 🔥 خصم {product['discount_percentage']}%"
        
        product_text = f"{type_icon} {product['name']}\n"
        product_text += f"💰 {format_price(final_price)}"
        if product['discount_percentage'] > 0:
            product_text += f" ~~{format_price(product['price_stars'])}~~"
        product_text += stock_text + discount_text
        
        text += f"\n{product_text}\n"
        
        # زر المنتج
        button_text = f"{type_icon} {product['name']} - {format_price(final_price)}"
        if product['is_limited'] and product['stock'] <= 0:
            button_text += " ❌"
        
        keyboard.append([
            InlineKeyboardButton(button_text, callback_data=f"product_{product['id']}")
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="browse_products")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

@rate_limit
@maintenance_check
async def show_product_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض تفاصيل المنتج"""
    query = update.callback_query
    await query.answer()
    
    product_id = int(query.data.split('_')[1])
    user_id = update.effective_user.id
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.*, c.name as category_name
            FROM products p
            LEFT JOIN categories c ON p.category_id = c.id
            WHERE p.id = ?
        """, (product_id,))
        product = cursor.fetchone()
    
    if not product or not product['is_active']:
        await query.edit_message_text(
            "❌ المنتج غير متاح",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="browse_products")
            ]])
        )
        return
    
    # حساب السعر النهائي
    final_price = product['price_stars']
    if product['discount_percentage'] > 0:
        final_price = int(final_price * (100 - product['discount_percentage']) / 100)
    
    # أيقونة نوع المنتج
    type_icons = {
        'file': '📄 ملف',
        'image': '🖼 صورة',
        'text': '📝 نص',
        'code': '🔑 كود',
        'balance': '💰 رصيد'
    }
    type_name = type_icons.get(product['type'], '📦 منتج')
    
    # بناء رسالة التفاصيل
    text = f"""
🛍 *{product['name']}*

📁 الفئة: {product['category_name']}
📋 الوصف: {product['description'] or 'لا يوجد وصف'}

💰 السعر: {format_price(final_price)}
"""
    
    if product['discount_percentage'] > 0:
        text += f"🔥 خصم: {product['discount_percentage']}% (السعر الأصلي: {format_price(product['price_stars'])})\n"
    
    text += f"📦 النوع: {type_name}\n"
    
    # حالة المخزون
    if product['is_limited']:
        text += f"📊 المخزون: {product['stock']}\n"
        if product['stock'] <= 0:
            text += "⚠️ *نفد المخزون*\n"
    else:
        text += "♾️ المخزون: غير محدود\n"
    
    text += f"🎯 التوصيل: {'تلقائي ⚡' if product['auto_delivery'] else 'يدوي 🤝'}\n"
    text += f"📊 عدد المبيعات: {product['sold_count']}\n"
    
    # الأزرار
    keyboard = []
    
    # زر الشراء
    if product['is_limited'] and product['stock'] <= 0:
        keyboard.append([InlineKeyboardButton("❌ نفد المخزون", callback_data="out_of_stock")])
    else:
        keyboard.append([
            InlineKeyboardButton(
                f"⭐ شراء الآن - {format_price(final_price)}",
                callback_data=f"buy_{product_id}"
            )
        ])
    
    # زر الرجوع
    keyboard.append([
        InlineKeyboardButton("🔙 رجوع", callback_data=f"category_{product['category_id']}")
    ])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

# ============================================================================
# نظام الدفع
# ============================================================================

@rate_limit
@maintenance_check
async def initiate_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء عملية الشراء"""
    query = update.callback_query
    await query.answer()
    
    product_id = int(query.data.split('_')[1])
    user_id = update.effective_user.id
    
    # التحقق من حظر المستخدم
    user_info = get_user_info(user_id)
    if user_info and user_info['is_banned']:
        await query.answer("⛔ حسابك محظور ولا يمكنك الشراء", show_alert=True)
        return
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        
        # قفل المنتج للتحقق من المخزون (حماية من Race Condition)
        cursor.execute("BEGIN EXCLUSIVE")
        
        cursor.execute("SELECT * FROM products WHERE id = ? AND is_active = 1", (product_id,))
        product = cursor.fetchone()
        
        if not product:
            await query.answer("❌ المنتج غير متاح", show_alert=True)
            return
        
        # التحقق من المخزون
        if product['is_limited'] and product['stock'] <= 0:
            await query.answer("❌ نفد المخزون", show_alert=True)
            return
        
        # حساب السعر النهائي
        final_price = product['price_stars']
        if product['discount_percentage'] > 0:
            final_price = int(final_price * (100 - product['discount_percentage']) / 100)
        
        # إنشاء فاتورة Telegram Stars
        title = product['name']
        description = product['description'] or f"شراء {product['name']}"
        payload = f"product_{product_id}_{user_id}_{int(time.time())}"
        
        prices = [LabeledPrice(label=product['name'], amount=final_price)]
        
        try:
            # إرسال الفاتورة
            await context.bot.send_invoice(
                chat_id=user_id,
                title=title,
                description=description,
                payload=payload,
                provider_token="",  # Telegram Stars لا تحتاج provider token
                currency="XTR",  # عملة Telegram Stars
                prices=prices,
                start_parameter=f"product_{product_id}"
            )
            
            await query.answer("✅ تم إرسال الفاتورة إليك", show_alert=True)
            log_security_event('payment', user_id, f'بدء شراء المنتج {product_id}')
            
        except Exception as e:
            logger.error(f"خطأ في إرسال الفاتورة: {e}")
            await query.answer("❌ حدث خطأ، الرجاء المحاولة لاحقاً", show_alert=True)

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التحقق قبل الدفع"""
    query = update.pre_checkout_query
    
    try:
        # استخراج معلومات المنتج من payload
        payload_parts = query.invoice_payload.split('_')
        product_id = int(payload_parts[1])
        user_id = int(payload_parts[2])
        
        # التحقق من صحة المستخدم
        if user_id != query.from_user.id:
            await query.answer(ok=False, error_message="❌ خطأ في التحقق من الهوية")
            log_security_event('fraud', query.from_user.id, 'محاولة دفع بهوية مزورة', severity='critical')
            return
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            # قفل المنتج
            cursor.execute("BEGIN EXCLUSIVE")
            
            cursor.execute("SELECT * FROM products WHERE id = ? AND is_active = 1", (product_id,))
            product = cursor.fetchone()
            
            if not product:
                await query.answer(ok=False, error_message="❌ المنتج غير متاح")
                return
            
            # التحقق من المخزون
            if product['is_limited'] and product['stock'] <= 0:
                await query.answer(ok=False, error_message="❌ نفد المخزون")
                return
            
            # حساب السعر المتوقع
            expected_price = product['price_stars']
            if product['discount_percentage'] > 0:
                expected_price = int(expected_price * (100 - product['discount_percentage']) / 100)
            
            # التحقق من السعر
            if query.total_amount != expected_price:
                await query.answer(ok=False, error_message="❌ خطأ في السعر")
                log_security_event('fraud', user_id, f'محاولة تلاعب بالسعر للمنتج {product_id}', severity='critical')
                return
        
        # الموافقة على الدفع
        await query.answer(ok=True)
        
    except Exception as e:
        logger.error(f"خطأ في precheckout: {e}")
        await query.answer(ok=False, error_message="❌ حدث خطأ، الرجاء المحاولة لاحقاً")

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الدفع الناجح"""
    payment = update.message.successful_payment
    user_id = update.effective_user.id
    
    try:
        # استخراج معلومات المنتج
        payload_parts = payment.invoice_payload.split('_')
        product_id = int(payload_parts[1])
        expected_user_id = int(payload_parts[2])
        
        # التحقق الأمني
        if user_id != expected_user_id:
            log_security_event('fraud', user_id, 'محاولة احتيال في الدفع', severity='critical')
            await update.message.reply_text("❌ حدث خطأ في التحقق من الدفع")
            return
        
        # التحقق من حظر المستخدم
        user_info = get_user_info(user_id)
        if user_info and user_info['is_banned']:
            log_security_event('fraud', user_id, 'محاولة شراء من حساب محظور', severity='high')
            await update.message.reply_text("⛔ حسابك محظور ولا يمكنك الشراء")
            return
        
        payment_id = payment.telegram_payment_charge_id
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            # قفل حصري لمنع التكرار
            cursor.execute("BEGIN EXCLUSIVE")
            
            # التحقق من عدم معالجة الدفع مسبقاً (حماية من Double Spending)
            cursor.execute("""
                SELECT id FROM orders 
                WHERE telegram_payment_charge_id = ?
            """, (payment_id,))
            
            if cursor.fetchone():
                logger.warning(f"محاولة دفع مكرر: {payment_id}")
                await update.message.reply_text("⚠️ تمت معالجة هذا الدفع مسبقاً")
                return
            
            # الحصول على المنتج
            cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
            product = cursor.fetchone()
            
            if not product:
                await update.message.reply_text("❌ المنتج غير موجود")
                return
            
            # التحقق من المخزون وتحديثه بشكل ذري
            if product['is_limited']:
                if product['stock'] <= 0:
                    await update.message.reply_text("❌ نفد المخزون")
                    return
                
                cursor.execute("""
                    UPDATE products 
                    SET stock = stock - 1, sold_count = sold_count + 1
                    WHERE id = ? AND stock > 0
                """, (product_id,))
                
                if cursor.rowcount == 0:
                    await update.message.reply_text("❌ نفد المخزون أثناء المعالجة")
                    return
            else:
                cursor.execute("""
                    UPDATE products 
                    SET sold_count = sold_count + 1
                    WHERE id = ?
                """, (product_id,))
            
            # إنشاء الطلب
            cursor.execute("""
                INSERT INTO orders (
                    user_id, product_id, payment_id, 
                    telegram_payment_charge_id, price, status
                ) VALUES (?, ?, ?, ?, ?, 'completed')
            """, (user_id, product_id, payment.invoice_payload, payment_id, payment.total_amount))
            
            order_id = cursor.lastrowid
            
            # تحديث إحصائيات المستخدم
            cursor.execute("""
                UPDATE users 
                SET total_spent = total_spent + ?,
                    total_purchases = total_purchases + 1
                WHERE user_id = ?
            """, (payment.total_amount, user_id))
            
            # توصيل المنتج
            delivered_content = None
            delivery_message = ""
            
            if product['auto_delivery']:
                if product['type'] == 'text':
                    delivered_content = product['content']
                    delivery_message = f"📝 المحتوى:\n\n{delivered_content}"
                    
                elif product['type'] == 'code':
                    # الحصول على كود غير مستخدم
                    cursor.execute("""
                        SELECT id, code_value FROM codes
                        WHERE product_id = ? AND is_used = 0
                        LIMIT 1
                    """, (product_id,))
                    
                    code_row = cursor.fetchone()
                    if code_row:
                        code_id = code_row['id']
                        code_value = code_row['code_value']
                        
                        # تحديد الكود كمستخدم
                        cursor.execute("""
                            UPDATE codes 
                            SET is_used = 1, used_by = ?, used_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (user_id, code_id))
                        
                        delivered_content = code_value
                        delivery_message = f"🔑 الكود الخاص بك:\n\n`{code_value}`"
                    else:
                        delivery_message = "⚠️ نفدت الأكواد، سيتم التواصل معك قريباً"
                        cursor.execute("""
                            UPDATE orders SET delivery_status = 'failed'
                            WHERE id = ?
                        """, (order_id,))
                
                elif product['type'] == 'balance':
                    balance_amount = int(product['content'])
                    cursor.execute("""
                        UPDATE users SET balance = balance + ?
                        WHERE user_id = ?
                    """, (balance_amount, user_id))
                    
                    delivered_content = str(balance_amount)
                    delivery_message = f"💰 تم إضافة {balance_amount} نجمة إلى رصيدك"
                
                elif product['type'] in ['file', 'image']:
                    delivery_message = "📦 سيتم إرسال الملف إليك الآن..."
                
                # تحديث حالة التوصيل
                if delivered_content:
                    cursor.execute("""
                        UPDATE orders 
                        SET delivery_status = 'delivered', 
                            delivered_content = ?,
                            completed_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (delivered_content, order_id))
            
            conn.commit()
            
            # إرسال رسالة النجاح
            success_text = f"""
✅ *تمت عملية الشراء بنجاح!*

🛍 المنتج: {product['name']}
💰 المبلغ: {format_price(payment.total_amount)}
📅 التاريخ: {datetime.now().strftime('%Y-%m-%d %H:%M')}
🔖 رقم الطلب: #{order_id}

{delivery_message}

شكراً لك على الشراء! 🎉
"""
            
            keyboard = [[InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")]]
            
            await update.message.reply_text(
                success_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            
            # إرسال الملف إذا كان المنتج ملف أو صورة
            if product['auto_delivery'] and product['type'] in ['file', 'image']:
                try:
                    if product['content']:
                        if product['type'] == 'file':
                            await update.message.reply_document(
                                document=product['content'],
                                caption=f"📄 {product['name']}"
                            )
                        elif product['type'] == 'image':
                            await update.message.reply_photo(
                                photo=product['content'],
                                caption=f"🖼 {product['name']}"
                            )
                        
                        cursor.execute("""
                            UPDATE orders 
                            SET delivery_status = 'delivered',
                                completed_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (order_id,))
                        conn.commit()
                except Exception as e:
                    logger.error(f"خطأ في إرسال الملف: {e}")
            
            # تسجيل الحدث
            log_security_event('purchase', user_id, f'شراء ناجح للمنتج {product_id} - الطلب {order_id}')
            
    except Exception as e:
        logger.error(f"خطأ في معالجة الدفع: {e}")
        await update.message.reply_text(
            "❌ حدث خطأ في معالجة الدفع، الرجاء التواصل مع الدعم"
        )
        log_security_event('error', user_id, f'خطأ في معالجة الدفع: {str(e)}', severity='high')

# ============================================================================
# حساب المستخدم
# ============================================================================

@rate_limit
@maintenance_check
async def my_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض معلومات الحساب"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    user_info = get_user_info(user_id)
    
    if not user_info:
        await query.edit_message_text("❌ خطأ في تحميل معلومات الحساب")
        return
    
    # حساب الإحصائيات
    with db.get_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT COUNT(*) as referral_count
            FROM users
            WHERE referred_by = ?
        """, (user_id,))
        referral_count = cursor.fetchone()['referral_count']
    
    text = f"""
👤 *معلومات الحساب*

🆔 المعرف: `{user_id}`
👤 الاسم: {user_info['first_name'] or 'غير محدد'}
📱 اليوزر: @{user_info['username'] or 'غير محدد'}

💰 الرصيد: {format_price(user_info['balance'])}
💳 إجمالي المشتريات: {format_price(user_info['total_spent'])}
🛍 عدد المشتريات: {user_info['total_purchases']}

👥 عدد الإحالات: {referral_count}
🔗 كود الإحالة: `{user_info['referral_code']}`

📅 تاريخ الانضمام: {user_info['join_date'][:10]}

🔗 رابط الإحالة:
`https://t.me/{context.bot.username}?start={user_info['referral_code']}`
"""
    
    keyboard = [
        [InlineKeyboardButton("👥 إحالاتي", callback_data="my_referrals")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
    ]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

@rate_limit
@maintenance_check
async def my_purchases(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض المشتريات"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT o.*, p.name as product_name, p.type
            FROM orders o
            JOIN products p ON o.product_id = p.id
            WHERE o.user_id = ? AND o.status = 'completed'
            ORDER BY o.created_at DESC
            LIMIT 10
        """, (user_id,))
        purchases = cursor.fetchall()
    
    if not purchases:
        text = "📭 ليس لديك مشتريات حتى الآن"
        keyboard = [[InlineKeyboardButton("🛍 تصفح المنتجات", callback_data="browse_products")]]
    else:
        text = "⭐ *مشترياتي الأخيرة:*\n\n"
        
        for purchase in purchases:
            status_emoji = "✅" if purchase['delivery_status'] == 'delivered' else "⏳"
            text += f"{status_emoji} {purchase['product_name']}\n"
            text += f"💰 {format_price(purchase['price'])} | 📅 {purchase['created_at'][:10]}\n"
            text += f"🔖 الطلب #{purchase['id']}\n\n"
        
        keyboard = [
            [InlineKeyboardButton("🧾 جميع الطلبات", callback_data="my_orders")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
        ]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

@rate_limit
@maintenance_check
async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض جميع الطلبات"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT o.*, p.name as product_name
            FROM orders o
            JOIN products p ON o.product_id = p.id
            WHERE o.user_id = ?
            ORDER BY o.created_at DESC
            LIMIT 20
        """, (user_id,))
        orders = cursor.fetchall()
    
    if not orders:
        text = "📭 ليس لديك طلبات حتى الآن"
        keyboard = [[InlineKeyboardButton("🛍 تصفح المنتجات", callback_data="browse_products")]]
    else:
        text = "🧾 *طلباتي:*\n\n"
        keyboard = []
        
        for order in orders:
            status_emoji = {
                'pending': '⏳',
                'completed': '✅',
                'failed': '❌',
                'refunded': '🔄'
            }.get(order['status'], '❓')
            
            delivery_emoji = {
                'pending': '📦',
                'delivered': '✅',
                'failed': '❌'
            }.get(order['delivery_status'], '❓')
            
            text += f"🔖 طلب #{order['id']}\n"
            text += f"📦 {order['product_name']}\n"
            text += f"💰 {format_price(order['price'])}\n"
            text += f"{status_emoji} الحالة | {delivery_emoji} التوصيل\n"
            text += f"📅 {order['created_at'][:16]}\n\n"
            
            keyboard.append([
                InlineKeyboardButton(
                    f"📋 طلب #{order['id']}",
                    callback_data=f"order_details_{order['id']}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

# ============================================================================
# لوحة الإدارة
# ============================================================================

@admin_only
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لوحة التحكم الرئيسية للمشرف"""
    query = update.callback_query
    await query.answer()
    
    # إحصائيات سريعة
    with db.get_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) as count FROM users")
        total_users = cursor.fetchone()['count']
        
        cursor.execute("SELECT COUNT(*) as count FROM products WHERE is_active = 1")
        active_products = cursor.fetchone()['count']
        
        cursor.execute("SELECT COUNT(*) as count FROM orders WHERE status = 'completed'")
        total_orders = cursor.fetchone()['count']
        
        cursor.execute("SELECT COALESCE(SUM(price), 0) as total FROM orders WHERE status = 'completed'")
        total_revenue = cursor.fetchone()['total']
        
        cursor.execute("""
            SELECT COUNT(*) as count FROM users 
            WHERE last_activity >= datetime('now', '-24 hours')
        """)
        active_24h = cursor.fetchone()['count']
    
    text = f"""
🔐 *لوحة الإدارة*

📊 *الإحصائيات السريعة:*

👥 المستخدمين: {total_users:,}
📦 المنتجات النشطة: {active_products}
🧾 إجمالي الطلبات: {total_orders:,}
💰 إجمالي الإيرادات: {format_price(total_revenue)}
🔥 نشط خلال 24 ساعة: {active_24h}

اختر العملية المطلوبة:
"""
    
    keyboard = [
        [
            InlineKeyboardButton("📦 إدارة المنتجات", callback_data="admin_products"),
            InlineKeyboardButton("📁 الفئات", callback_data="admin_categories")
        ],
        [
            InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats"),
            InlineKeyboardButton("👥 المستخدمين", callback_data="admin_users")
        ],
        [
            InlineKeyboardButton("🧾 الطلبات", callback_data="admin_orders"),
            InlineKeyboardButton("🎟 الكوبونات", callback_data="admin_coupons")
        ],
        [
            InlineKeyboardButton("📢 البث", callback_data="admin_broadcast"),
            InlineKeyboardButton("⚙️ الإعدادات", callback_data="admin_settings")
        ],
        [
            InlineKeyboardButton("🔒 السجلات الأمنية", callback_data="admin_security_logs"),
            InlineKeyboardButton("💾 النسخ الاحتياطي", callback_data="admin_backup")
        ],
        [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
    ]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

@admin_only
async def admin_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إدارة المنتجات"""
    query = update.callback_query
    await query.answer()
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.*, c.name as category_name
            FROM products p
            LEFT JOIN categories c ON p.category_id = c.id
            ORDER BY p.is_active DESC, p.created_at DESC
            LIMIT 20
        """)
        products = cursor.fetchall()
    
    text = "📦 *إدارة المنتجات*\n\n"
    keyboard = [[InlineKeyboardButton("➕ إضافة منتج جديد", callback_data="admin_add_product")]]
    
    for product in products:
        status = "✅" if product['is_active'] else "❌"
        stock_text = f"المخزون: {product['stock']}" if product['is_limited'] else "∞"
        
        text += f"{status} {product['name']}\n"
        text += f"💰 {format_price(product['price_stars'])} | {stock_text}\n"
        text += f"📁 {product['category_name'] or 'بدون فئة'}\n\n"
        
        keyboard.append([
            InlineKeyboardButton(
                f"{status} {product['name'][:20]}...",
                callback_data=f"admin_edit_product_{product['id']}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

@admin_only
async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إدارة المستخدمين"""
    query = update.callback_query
    await query.answer()
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, first_name, username, total_purchases, balance, is_banned
            FROM users
            ORDER BY join_date DESC
            LIMIT 20
        """)
        users = cursor.fetchall()
    
    text = "👥 *إدارة المستخدمين*\n\n"
    keyboard = []
    
    for user in users:
        status = "🔒" if user['is_banned'] else "✅"
        text += f"{status} {user['first_name']} (@{user['username'] or 'N/A'})\n"
        text += f"🛍 المشتريات: {user['total_purchases']} | 💰 الرصيد: {format_price(user['balance'])}\n\n"
        
        keyboard.append([
            InlineKeyboardButton(
                f"{status} {user['first_name'][:15]}...",
                callback_data=f"admin_user_details_{user['user_id']}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

@admin_only
async def admin_user_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض تفاصيل المستخدم"""
    query = update.callback_query
    await query.answer()
    
    try:
        user_id = int(query.data.split('_')[-1])
        user_info = get_user_info(user_id)
        
        if not user_info:
            await query.answer("المستخدم غير موجود", show_alert=True)
            return
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) as count FROM users WHERE referred_by = ?
            """, (user_id,))
            referral_count = cursor.fetchone()['count']
        
        text = f"""
👤 *تفاصيل المستخدم*

🆔 المعرف: {user_id}
👤 الاسم: {user_info['first_name']}
📱 اليوزر: @{user_info['username'] or 'N/A'}

💰 الرصيد: {format_price(user_info['balance'])}
💳 إجمالي الإنفاق: {format_price(user_info['total_spent'])}
🛍 عدد المشتريات: {user_info['total_purchases']}
👥 الإحالات: {referral_count}

🔒 الحالة: {'محظور ⛔' if user_info['is_banned'] else 'نشط ✅'}
{'سبب الحظر: ' + (user_info['ban_reason'] or 'N/A') if user_info['is_banned'] else ''}

📅 تاريخ الانضمام: {user_info['join_date'][:10]}
"""
        
        keyboard = []
        if user_info['is_banned']:
            keyboard.append([InlineKeyboardButton("🔓 فك الحظر", callback_data=f"admin_unban_user_{user_id}")])
        else:
            keyboard.append([InlineKeyboardButton("🔒 حظر المستخدم", callback_data=f"admin_ban_user_{user_id}")])
        
        keyboard.extend([
            [InlineKeyboardButton("💰 إضافة رصيد", callback_data=f"admin_add_balance_{user_id}")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_users")]
        ])
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"خطأ في عرض تفاصيل المستخدم: {e}")
        await query.answer("حدث خطأ", show_alert=True)

@admin_only
async def admin_ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حظر المستخدم"""
    query = update.callback_query
    user_id = int(query.data.split('_')[-1])
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users SET is_banned = 1
            WHERE user_id = ?
        """, (user_id,))
    
    await query.answer("تم حظر المستخدم ✅")
    await admin_user_details(update, context)

@admin_only
async def admin_unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فك حظر المستخدم"""
    query = update.callback_query
    user_id = int(query.data.split('_')[-1])
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users SET is_banned = 0, ban_reason = NULL
            WHERE user_id = ?
        """, (user_id,))
    
    await query.answer("تم فك الحظر ✅")
    await admin_user_details(update, context)

@admin_only
async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض جميع الطلبات"""
    query = update.callback_query
    await query.answer()
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT o.id, o.user_id, o.status, o.price, p.name, u.first_name
            FROM orders o
            JOIN products p ON o.product_id = p.id
            JOIN users u ON o.user_id = u.user_id
            ORDER BY o.created_at DESC
            LIMIT 20
        """)
        orders = cursor.fetchall()
    
    if not orders:
        text = "📭 لا توجد طلبات"
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]]
    else:
        text = "🧾 *الطلبات:*\n\n"
        keyboard = []
        
        for order in orders:
            status_emoji = {
                'pending': '⏳',
                'completed': '✅',
                'failed': '❌',
                'refunded': '🔄'
            }.get(order['status'], '❓')
            
            text += f"#{order['id']} {status_emoji} {order['name']}\n"
            text += f"👤 {order['first_name']} | 💰 {format_price(order['price'])}\n\n"
            
            keyboard.append([
                InlineKeyboardButton(
                    f"طلب #{order['id']}",
                    callback_data=f"admin_order_details_{order['id']}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

@admin_only
async def admin_order_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تفاصيل الطلب"""
    query = update.callback_query
    await query.answer()
    
    try:
        order_id = int(query.data.split('_')[-1])
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT o.*, p.name, u.first_name, u.username
                FROM orders o
                JOIN products p ON o.product_id = p.id
                JOIN users u ON o.user_id = u.user_id
                WHERE o.id = ?
            """, (order_id,))
            order = cursor.fetchone()
        
        if not order:
            await query.answer("الطلب غير موجود", show_alert=True)
            return
        
        text = f"""
🧾 *تفاصيل الطلب #{order_id}*

👤 المستخدم: {order['first_name']} (@{order['username']})
📦 المنتج: {order['name']}
💰 المبلغ: {format_price(order['price'])}

📊 الحالة: {order['status']}
📮 حالة التوصيل: {order['delivery_status']}

📅 تاريخ الطلب: {order['created_at']}
"""
        
        if order['delivered_content']:
            text += f"\n📝 المحتوى المسلّم:\n```\n{order['delivered_content'][:500]}\n```"
        
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="admin_orders")]]
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"خطأ في عرض تفاصيل الطلب: {e}")
        await query.answer("حدث خطأ", show_alert=True)

@admin_only
async def admin_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إدارة الفئات"""
    query = update.callback_query
    await query.answer()
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM categories
            ORDER BY display_order, name
        """)
        categories = cursor.fetchall()
    
    text = "📁 *إدارة الفئات*\n\n"
    keyboard = [[InlineKeyboardButton("➕ إضافة فئة جديدة", callback_data="admin_add_category")]]
    
    for cat in categories:
        status = "✅" if cat['is_active'] else "❌"
        text += f"{status} {cat['icon']} {cat['name']}\n"
        text += f"📝 {cat['description'] or 'بدون وصف'}\n\n"
        
        keyboard.append([
            InlineKeyboardButton(
                f"{cat['icon']} {cat['name'][:20]}...",
                callback_data=f"admin_edit_category_{cat['id']}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

@admin_only
async def admin_coupons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إدارة الكوبونات"""
    query = update.callback_query
    await query.answer()
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM coupons
            ORDER BY created_at DESC
            LIMIT 20
        """)
        coupons = cursor.fetchall()
    
    if not coupons:
        text = "🎟 لا توجد كوبونات"
        keyboard = [[InlineKeyboardButton("➕ إضافة كوبون", callback_data="admin_add_coupon")]]
    else:
        text = "🎟 *الكوبونات:*\n\n"
        keyboard = [[InlineKeyboardButton("➕ إضافة كوبون", callback_data="admin_add_coupon")]]
        
        for coupon in coupons:
            status = "✅" if coupon['is_active'] else "❌"
            text += f"{status} {coupon['code']}\n"
            text += f"💰 {coupon['discount_value']}{'%' if coupon['discount_type'] == 'percentage' else ' نجمة'} | الاستخدام: {coupon['used_count']}/{coupon['max_uses'] if coupon['max_uses'] > 0 else '∞'}\n\n"
            
            keyboard.append([
                InlineKeyboardButton(
                    f"{coupon['code']}",
                    callback_data=f"admin_coupon_details_{coupon['id']}"
                )
            ])
    
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

@admin_only
async def admin_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إضافة منتج جديد"""
    query = update.callback_query
    await query.answer()
    
    context.user_data['adding_product'] = {}
    
    text = """
📦 *إضافة منتج جديد*

الرجاء إرسال بيانات المنتج بالصيغة التالية:

الاسم | الوصف | السعر (نجوم) | النوع (text/code/file/image/balance) | المحتوى

مثال:
كورس البرمجة | تعلم البرمجة من الصفر | 50 | text | محتوى الكورس هنا

الأنواع المتاحة:
- text: نص عادي
- code: أكواد
- file: ملف
- image: صورة
- balance: رصيد
"""
    
    context.user_data['admin_adding_product'] = True
    await query.edit_message_text(text)

@admin_only
async def admin_edit_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تعديل المنتج"""
    query = update.callback_query
    await query.answer()
    
    try:
        product_id = int(query.data.split('_')[-1])
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
            product = cursor.fetchone()
        
        if not product:
            await query.answer("المنتج غير موجود", show_alert=True)
            return
        
        text = f"""
📦 *تعديل المنتج*

📋 الاسم: {product['name']}
💰 السعر: {format_price(product['price_stars'])}
📝 الوصف: {product['description'] or 'بدون'}
📊 المبيعات: {product['sold_count']}

اختر ما تريد تعديله:
"""
        
        keyboard = [
            [InlineKeyboardButton("📝 تعديل الاسم", callback_data=f"admin_edit_product_name_{product_id}")],
            [InlineKeyboardButton("💰 تعديل السعر", callback_data=f"admin_edit_product_price_{product_id}")],
            [InlineKeyboardButton("📊 تعديل المخزون", callback_data=f"admin_edit_product_stock_{product_id}")],
            [InlineKeyboardButton("🔄 تفعيل/تعطيل", callback_data=f"admin_toggle_product_{product_id}")],
            [InlineKeyboardButton("🗑 حذف", callback_data=f"admin_delete_product_{product_id}")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_products")]
        ]
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"خطأ في تعديل المنتج: {e}")
        await query.answer("حدث خطأ", show_alert=True)

@admin_only
async def admin_toggle_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تفعيل/تعطيل المنتج"""
    query = update.callback_query
    product_id = int(query.data.split('_')[-1])
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_active FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()
        
        new_status = 0 if product['is_active'] else 1
        cursor.execute("""
            UPDATE products SET is_active = ?
            WHERE id = ?
        """, (new_status, product_id))
    
    status = "مفعل ✅" if new_status else "معطل ❌"
    await query.answer(f"تم تحديث حالة المنتج - {status}")
    await admin_edit_product(update, context)

@admin_only
async def admin_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف المنتج"""
    query = update.callback_query
    product_id = int(query.data.split('_')[-1])
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
    
    await query.answer("✅ تم حذف المنتج")
    await admin_products(update, context)

@admin_only
async def admin_add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إضافة فئة جديدة"""
    query = update.callback_query
    await query.answer()
    
    context.user_data['admin_adding_category'] = True
    
    text = """
📁 *إضافة فئة جديدة*

الرجاء إرسال بيانات الفئة:

الاسم | الوصف | الأيقونة

مثال:
الكورسات | كورسات تعليمية | 📚
"""
    
    await query.edit_message_text(text)

@admin_only
async def admin_edit_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تعديل الفئة"""
    query = update.callback_query
    await query.answer()
    
    try:
        category_id = int(query.data.split('_')[-1])
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM categories WHERE id = ?", (category_id,))
            category = cursor.fetchone()
        
        if not category:
            await query.answer("الفئة غير موجودة", show_alert=True)
            return
        
        text = f"""
📁 *تعديل الفئة*

{category['icon']} {category['name']}
"""
        
        keyboard = [
            [InlineKeyboardButton("📝 تعديل الاسم", callback_data=f"admin_edit_cat_name_{category_id}")],
            [InlineKeyboardButton("🔄 تفعيل/تعطيل", callback_data=f"admin_toggle_cat_{category_id}")],
            [InlineKeyboardButton("🗑 حذف", callback_data=f"admin_delete_cat_{category_id}")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_categories")]
        ]
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"خطأ في تعديل الفئة: {e}")
        await query.answer("حدث خطأ", show_alert=True)

@admin_only
async def admin_toggle_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تفعيل/تعطيل الفئة"""
    query = update.callback_query
    category_id = int(query.data.split('_')[-1])
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_active FROM categories WHERE id = ?", (category_id,))
        category = cursor.fetchone()
        
        new_status = 0 if category['is_active'] else 1
        cursor.execute("""
            UPDATE categories SET is_active = ?
            WHERE id = ?
        """, (new_status, category_id))
    
    await query.answer("✅ تم التحديث")
    await admin_categories(update, context)

@admin_only
async def admin_delete_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف الفئة"""
    query = update.callback_query
    category_id = int(query.data.split('_')[-1])
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    
    await query.answer("✅ تم حذف الفئة")
    await admin_categories(update, context)

@admin_only
async def admin_add_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إضافة كوبون"""
    query = update.callback_query
    await query.answer()
    
    context.user_data['admin_adding_coupon'] = True
    
    text = """
🎟 *إضافة كوبون جديد*

الرجاء إرسال بيانات الكوبون:

الكود | نوع الخصم (fixed/percentage) | القيمة | عدد الاستخدامات (-1 لغير محدود)

مثال:
SAVE50 | fixed | 50 | -1
WELCOME20 | percentage | 20 | 100
"""
    
    await query.edit_message_text(text)

@admin_only
async def admin_coupon_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تفاصيل الكوبون"""
    query = update.callback_query
    await query.answer()
    
    try:
        coupon_id = int(query.data.split('_')[-1])
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM coupons WHERE id = ?", (coupon_id,))
            coupon = cursor.fetchone()
        
        if not coupon:
            await query.answer("الكوبون غير موجود", show_alert=True)
            return
        
        discount_text = f"{coupon['discount_value']}{'%' if coupon['discount_type'] == 'percentage' else ' نجمة'}"
        
        text = f"""
🎟 *تفاصيل الكوبون*

💾 الكود: {coupon['code']}
💰 الخصم: {discount_text}
🔢 الاستخدام: {coupon['used_count']}/{coupon['max_uses'] if coupon['max_uses'] > 0 else '∞'}
{"✅ مفعل" if coupon['is_active'] else "❌ معطل"}
"""
        
        keyboard = [
            [InlineKeyboardButton("🔄 تفعيل/تعطيل", callback_data=f"admin_toggle_coupon_{coupon_id}")],
            [InlineKeyboardButton("🗑 حذف", callback_data=f"admin_delete_coupon_{coupon_id}")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_coupons")]
        ]
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"خطأ في عرض تفاصيل الكوبون: {e}")
        await query.answer("حدث خطأ", show_alert=True)

@admin_only
async def admin_toggle_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تفعيل/تعطيل الكوبون"""
    query = update.callback_query
    coupon_id = int(query.data.split('_')[-1])
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_active FROM coupons WHERE id = ?", (coupon_id,))
        coupon = cursor.fetchone()
        
        new_status = 0 if coupon['is_active'] else 1
        cursor.execute("""
            UPDATE coupons SET is_active = ?
            WHERE id = ?
        """, (new_status, coupon_id))
    
    await query.answer("✅ تم التحديث")
    await admin_coupon_details(update, context)

@admin_only
async def admin_delete_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف الكوبون"""
    query = update.callback_query
    coupon_id = int(query.data.split('_')[-1])
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM coupons WHERE id = ?", (coupon_id,))
    
    await query.answer("✅ تم حذف الكوبون")
    await admin_coupons(update, context)

@admin_only
async def admin_edit_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تعديل إعداد"""
    query = update.callback_query
    await query.answer()
    
    try:
        setting_key = query.data.split('_', 3)[-1]
        context.user_data['editing_setting'] = setting_key
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = ?", (setting_key,))
            result = cursor.fetchone()
            current_value = result['value'] if result else 'N/A'
        
        text = f"✏️ *تعديل الإعداد*\n\n🔹 {setting_key}\nالقيمة الحالية: {current_value}\n\nالرجاء إرسال القيمة الجديدة:"
        
        await query.edit_message_text(text)
    except Exception as e:
        logger.error(f"خطأ في تعديل الإعداد: {e}")
        await query.answer("حدث خطأ", show_alert=True)



@admin_only
async def admin_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إدارة الإعدادات"""
    query = update.callback_query
    await query.answer()
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings")
        settings = cursor.fetchall()
    
    text = "⚙️ *الإعدادات:*\n\n"
    keyboard = []
    
    for setting in settings:
        text += f"🔹 {setting['key']}: {setting['value']}\n"
        keyboard.append([
            InlineKeyboardButton(
                f"✏️ {setting['key']}",
                callback_data=f"admin_edit_setting_{setting['key']}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

@admin_only
async def admin_security_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """السجلات الأمنية"""
    query = update.callback_query
    await query.answer()
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM security_logs
            ORDER BY timestamp DESC
            LIMIT 20
        """)
        logs = cursor.fetchall()
    
    text = "🔒 *السجلات الأمنية:*\n\n"
    keyboard = []
    
    for log in logs:
        severity_emoji = {
            'info': 'ℹ️',
            'warning': '⚠️',
            'high': '🔴',
            'critical': '🚨'
        }.get(log['severity'], '❓')
        
        text += f"{severity_emoji} {log['log_type']} - {log['action']}\n"
        text += f"👤 المستخدم: {log['user_id'] or 'N/A'} | 📅 {log['timestamp'][:16]}\n\n"
    
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

@admin_only
async def admin_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نسخة احتياطية من قاعدة البيانات"""
    query = update.callback_query
    
    try:
        backup_file = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        
        import shutil
        shutil.copy(DATABASE_FILE, backup_file)
        
        with open(backup_file, 'rb') as f:
            await context.bot.send_document(
                chat_id=update.effective_user.id,
                document=f,
                filename=backup_file,
                caption="💾 نسخة احتياطية من قاعدة البيانات"
            )
        
        os.remove(backup_file)
        await query.answer("✅ تم إرسال النسخة الاحتياطية")
    except Exception as e:
        logger.error(f"خطأ في النسخ الاحتياطي: {e}")
        await query.answer("❌ حدث خطأ في النسخ الاحتياطي", show_alert=True)

@rate_limit
async def my_referrals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض الإحالات"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, first_name, join_date, total_purchases
            FROM users
            WHERE referred_by = ?
            ORDER BY join_date DESC
        """, (user_id,))
        referrals = cursor.fetchall()
    
    if not referrals:
        text = "👥 ليس لديك إحالات حتى الآن\n\nشارك كود الإحالة الخاص بك مع أصدقائك!"
        keyboard = [[InlineKeyboardButton("👤 حسابي", callback_data="my_account")]]
    else:
        text = f"👥 *إحالاتي ({len(referrals)}):*\n\n"
        
        total_earned = 0
        for ref in referrals:
            text += f"✅ {ref['first_name']}\n"
            text += f"📅 {ref['join_date'][:10]} | 🛍 {ref['total_purchases']} مشتريات\n\n"
            
            with db.get_connection() as conn2:
                cursor2 = conn2.cursor()
                cursor2.execute("SELECT value FROM settings WHERE key = 'referral_reward'")
                reward = int(cursor2.fetchone()[0])
                total_earned += reward
        
        text += f"\n💰 إجمالي الأرباح: {format_price(total_earned)}"
        keyboard = [[InlineKeyboardButton("👤 حسابي", callback_data="my_account")]]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

@rate_limit
async def order_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تفاصيل الطلب"""
    query = update.callback_query
    await query.answer()
    
    try:
        order_id = int(query.data.split('_')[-1])
        user_id = update.effective_user.id
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT o.*, p.name, p.type
                FROM orders o
                JOIN products p ON o.product_id = p.id
                WHERE o.id = ? AND o.user_id = ?
            """, (order_id, user_id))
            order = cursor.fetchone()
        
        if not order:
            await query.answer("الطلب غير موجود", show_alert=True)
            return
        
        status_text = {
            'pending': '⏳ قيد المعالجة',
            'completed': '✅ مكتمل',
            'failed': '❌ فشل',
            'refunded': '🔄 مسترجع'
        }.get(order['status'], '❓')
        
        delivery_text = {
            'pending': '📦 قيد التسليم',
            'delivered': '✅ تم التسليم',
            'failed': '❌ فشل التسليم'
        }.get(order['delivery_status'], '❓')
        
        text = f"""
🧾 *تفاصيل الطلب #{order_id}*

📦 المنتج: {order['name']}
💰 المبلغ: {format_price(order['price'])}

📊 الحالة: {status_text}
📮 التوصيل: {delivery_text}

📅 التاريخ: {order['created_at'][:16]}
"""
        
        if order['delivered_content'] and order['type'] == 'code':
            text += f"\n🔑 الكود:\n```\n{order['delivered_content']}\n```"
        elif order['delivered_content'] and order['type'] == 'text':
            text += f"\n📝 المحتوى:\n```\n{order['delivered_content'][:300]}\n```"
        elif order['delivered_content'] and order['type'] == 'balance':
            text += f"\n💰 تمت إضافة {order['delivered_content']} نجمة"
        
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="my_orders")]]
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"خطأ في عرض تفاصيل الطلب: {e}")
        await query.answer("حدث خطأ", show_alert=True)



async def broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """البث الفعلي للرسالة"""
    if not update.effective_user or update.effective_user.id not in ADMIN_IDS:
        return
    
    if not context.user_data.get('admin_broadcast_mode'):
        return
    
    message_text = update.message.text
    
    if message_text.lower() == "إلغاء":
        context.user_data['admin_broadcast_mode'] = False
        await update.message.reply_text("✅ تم الإلغاء")
        return
    
    # إرسال الرسالة للجميع
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT user_id FROM users WHERE is_banned = 0")
        users = cursor.fetchall()
    
    success_count = 0
    failed_count = 0
    
    for user in users:
        try:
            await context.bot.send_message(
                chat_id=user['user_id'],
                text=f"📢 *رسالة من الإدارة:*\n\n{message_text}",
                parse_mode='Markdown'
            )
            success_count += 1
        except:
            failed_count += 1
    
    await update.message.reply_text(
        f"✅ تم إرسال الرسالة\n\n📊 النتائج:\n✅ نجاح: {success_count}\n❌ فشل: {failed_count}"
    )
    context.user_data['admin_broadcast_mode'] = False

async def save_setting_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حفظ قيمة الإعداد"""
    if not update.effective_user or update.effective_user.id not in ADMIN_IDS:
        return
    
    if not context.user_data.get('editing_setting'):
        return
    
    setting_key = context.user_data['editing_setting']
    setting_value = update.message.text
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE settings 
            SET value = ?
            WHERE key = ?
        """, (setting_value, setting_key))
    
    await update.message.reply_text(f"✅ تم حفظ الإعداد: {setting_key}")
    context.user_data['editing_setting'] = None
    
    # إعادة توجيه للإعدادات
    keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="admin_settings")]]
    await update.message.reply_text(
        "اختر الإجراء:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_product_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة بيانات المنتج الجديد"""
    if not update.effective_user or update.effective_user.id not in ADMIN_IDS:
        return
    
    if not context.user_data.get('admin_adding_product'):
        return
    
    message_text = update.message.text
    
    if message_text.lower() == "إلغاء":
        context.user_data['admin_adding_product'] = False
        await update.message.reply_text("✅ تم الإلغاء")
        return
    
    try:
        # تحليل البيانات
        parts = [p.strip() for p in message_text.split('|')]
        if len(parts) < 5:
            await update.message.reply_text("❌ الرجاء إرسال جميع البيانات بالصيغة الصحيحة")
            return
        
        name, description, price_str, product_type, content = parts[0], parts[1], parts[2], parts[3], parts[4]
        
        try:
            price = int(price_str)
        except ValueError:
            await update.message.reply_text("❌ السعر يجب أن يكون رقماً")
            return
        
        # إضافة المنتج
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO products (
                    category_id, name, description, price_stars,
                    type, content, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, 1)
            """, (1, name, description, price, product_type, content))
            
            product_id = cursor.lastrowid
        
        await update.message.reply_text(
            f"✅ تم إضافة المنتج!\n\n🆔 المعرف: {product_id}\n📝 الاسم: {name}\n💰 السعر: {price}"
        )
        context.user_data['admin_adding_product'] = False
        
    except Exception as e:
        logger.error(f"خطأ في إضافة المنتج: {e}")
        await update.message.reply_text(f"❌ حدث خطأ: {str(e)}")

async def handle_category_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة بيانات الفئة الجديدة"""
    if not update.effective_user or update.effective_user.id not in ADMIN_IDS:
        return
    
    if not context.user_data.get('admin_adding_category'):
        return
    
    message_text = update.message.text
    
    if message_text.lower() == "إلغاء":
        context.user_data['admin_adding_category'] = False
        await update.message.reply_text("✅ تم الإلغاء")
        return
    
    try:
        parts = [p.strip() for p in message_text.split('|')]
        if len(parts) < 3:
            await update.message.reply_text("❌ الرجاء إرسال جميع البيانات بالصيغة الصحيحة")
            return
        
        name, description, icon = parts[0], parts[1], parts[2]
        
        # إضافة الفئة
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO categories (name, description, icon, is_active)
                VALUES (?, ?, ?, 1)
            """, (name, description, icon))
            
            category_id = cursor.lastrowid
        
        await update.message.reply_text(
            f"✅ تم إضافة الفئة!\n\n🆔 المعرف: {category_id}\n📁 الاسم: {name}"
        )
        context.user_data['admin_adding_category'] = False
        
    except Exception as e:
        logger.error(f"خطأ في إضافة الفئة: {e}")
        await update.message.reply_text(f"❌ حدث خطأ: {str(e)}")

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الرسائل النصية"""
    user_id = update.effective_user.id
    
    # معالجة بث الرسائل (للمشرفين فقط)
    if user_id in ADMIN_IDS and context.user_data.get('admin_broadcast_mode'):
        await broadcast_message(update, context)
        return
    
    # معالجة تعديل الإعدادات
    if user_id in ADMIN_IDS and context.user_data.get('editing_setting'):
        await save_setting_value(update, context)
        return
    
    # معالجة إدخال بيانات المنتج الجديد
    if user_id in ADMIN_IDS and context.user_data.get('admin_adding_product'):
        await handle_product_data(update, context)
        return
    
    # معالجة إدخال بيانات الفئة الجديدة
    if user_id in ADMIN_IDS and context.user_data.get('admin_adding_category'):
        await handle_category_data(update, context)
        return
    
    # معالجة إدخال بيانات الكوبون الجديد
    if user_id in ADMIN_IDS and context.user_data.get('admin_adding_coupon'):
        await handle_coupon_data(update, context)
        return
    
    # معالجة افتراضية
    await update.message.reply_text(
        "👋 مرحباً! استخدم الأزرار أدناه للتنقل.\n\n",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")
        ]])
    )

# ============================================================================
# معالجات Callback محسّنة مع إصلاحات
# ============================================================================

async def out_of_stock_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج المنتجات المنتهية"""
    query = update.callback_query
    await query.answer("❌ هذا المنتج نفد المخزون", show_alert=True)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'support_username'")
        support = cursor.fetchone()[0]
    
    text = f"""
ℹ️ *المساعدة والدعم*

مرحباً بك في قسم المساعدة!

🛍 *كيفية الشراء:*
1. اختر "تصفح المنتجات"
2. اختر الفئة المطلوبة
3. اختر المنتج
4. اضغط على "شراء الآن"
5. أكمل عملية الدفع بالنجوم ⭐

💰 *طرق الدفع:*
نقبل فقط الدفع بواسطة نجوم تيليجرام ⭐

🎁 *نظام الإحالة:*
احصل على مكافآت عند دعوة أصدقائك!
استخدم كود الإحالة من حسابك

📞 *التواصل مع الدعم:*
للمساعدة تواصل معنا: {support}

❓ *أسئلة شائعة:*
• متى أستلم المنتج؟ فوراً بعد الدفع
• هل يمكن الاسترجاع؟ حسب سياسة المتجر
• كيف أستخدم الكوبونات؟ سيتم تفعيلها قريباً
"""
    
    keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]]
    
    if query:
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الرجوع للقائمة الرئيسية"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'store_name'")
        store_name = cursor.fetchone()[0]
    
    text = f"""
🏠 *القائمة الرئيسية*

مرحباً {user.first_name}! 👋

أهلاً بك في *{store_name}*

اختر من القائمة أدناه:
"""
    
    keyboard = [
        [InlineKeyboardButton("🛍 تصفح المنتجات", callback_data="browse_products")],
        [
            InlineKeyboardButton("⭐ مشترياتي", callback_data="my_purchases"),
            InlineKeyboardButton("🧾 طلباتي", callback_data="my_orders")
        ],
        [
            InlineKeyboardButton("👤 حسابي", callback_data="my_account"),
            InlineKeyboardButton("ℹ️ المساعدة", callback_data="help")
        ]
    ]
    
    if user.id in ADMIN_IDS:
        keyboard.append([InlineKeyboardButton("🔐 لوحة الإدارة", callback_data="admin_panel")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

# ============================================================================
# معالج الأخطاء
# ============================================================================

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الأخطاء"""
    logger.error(f"حدث خطأ: {context.error}")
    
    try:
        if update and update.effective_user:
            user_id = update.effective_user.id
            log_security_event('error', user_id, f'خطأ في البوت: {str(context.error)}', severity='high')
            
            if update.callback_query:
                await update.callback_query.answer(
                    "❌ حدث خطأ، الرجاء المحاولة لاحقاً",
                    show_alert=True
                )
            elif update.message:
                await update.message.reply_text(
                    "❌ حدث خطأ، الرجاء المحاولة لاحقاً"
                )
    except Exception as e:
        logger.error(f"خطأ في معالج الأخطاء: {e}")

# ============================================================================
# التطبيق الرئيسي
# ============================================================================

def main():
    """تشغيل البوت"""
    logger.info("بدء تشغيل البوت...")
    
    # إنشاء التطبيق
    application = Application.builder().token(BOT_TOKEN).build()
    
    # معالجات الأوامر
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    
    # معالجات Callback - الأساسية
    application.add_handler(CallbackQueryHandler(main_menu_handler, pattern="^main_menu$"))
    application.add_handler(CallbackQueryHandler(browse_products, pattern="^browse_products$"))
    application.add_handler(CallbackQueryHandler(show_category_products, pattern="^category_"))
    application.add_handler(CallbackQueryHandler(show_product_details, pattern="^product_"))
    application.add_handler(CallbackQueryHandler(initiate_purchase, pattern="^buy_"))
    application.add_handler(CallbackQueryHandler(out_of_stock_handler, pattern="^out_of_stock$"))
    
    # معالجات الحساب والمشتريات
    application.add_handler(CallbackQueryHandler(my_account, pattern="^my_account$"))
    application.add_handler(CallbackQueryHandler(my_purchases, pattern="^my_purchases$"))
    application.add_handler(CallbackQueryHandler(my_orders, pattern="^my_orders$"))
    application.add_handler(CallbackQueryHandler(my_referrals, pattern="^my_referrals$"))
    application.add_handler(CallbackQueryHandler(order_details, pattern="^order_details_"))
    application.add_handler(CallbackQueryHandler(help_command, pattern="^help$"))
    
    # معالجات لوحة الإدارة - اللوحة الرئيسية
    application.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    
    # معالجات لوحة الإدارة - المنتجات والفئات
    application.add_handler(CallbackQueryHandler(admin_products, pattern="^admin_products$"))
    application.add_handler(CallbackQueryHandler(admin_add_product, pattern="^admin_add_product$"))
    application.add_handler(CallbackQueryHandler(admin_edit_product, pattern="^admin_edit_product_"))
    application.add_handler(CallbackQueryHandler(admin_toggle_product, pattern="^admin_toggle_product_"))
    application.add_handler(CallbackQueryHandler(admin_delete_product, pattern="^admin_delete_product_"))
    
    application.add_handler(CallbackQueryHandler(admin_categories, pattern="^admin_categories$"))
    application.add_handler(CallbackQueryHandler(admin_add_category, pattern="^admin_add_category$"))
    application.add_handler(CallbackQueryHandler(admin_edit_category, pattern="^admin_edit_category_"))
    application.add_handler(CallbackQueryHandler(admin_toggle_category, pattern="^admin_toggle_cat_"))
    application.add_handler(CallbackQueryHandler(admin_delete_category, pattern="^admin_delete_cat_"))
    
    application.add_handler(CallbackQueryHandler(admin_stats, pattern="^admin_stats$"))
    
    # معالجات لوحة الإدارة - المستخدمين
    application.add_handler(CallbackQueryHandler(admin_users, pattern="^admin_users$"))
    application.add_handler(CallbackQueryHandler(admin_user_details, pattern="^admin_user_details_"))
    application.add_handler(CallbackQueryHandler(admin_ban_user, pattern="^admin_ban_user_"))
    application.add_handler(CallbackQueryHandler(admin_unban_user, pattern="^admin_unban_user_"))
    
    # معالجات لوحة الإدارة - الطلبات والكوبونات
    application.add_handler(CallbackQueryHandler(admin_orders, pattern="^admin_orders$"))
    application.add_handler(CallbackQueryHandler(admin_order_details, pattern="^admin_order_details_"))
    application.add_handler(CallbackQueryHandler(admin_coupons, pattern="^admin_coupons$"))
    application.add_handler(CallbackQueryHandler(admin_add_coupon, pattern="^admin_add_coupon$"))
    application.add_handler(CallbackQueryHandler(admin_coupon_details, pattern="^admin_coupon_details_"))
    application.add_handler(CallbackQueryHandler(admin_toggle_coupon, pattern="^admin_toggle_coupon_"))
    application.add_handler(CallbackQueryHandler(admin_delete_coupon, pattern="^admin_delete_coupon_"))
    
    # معالجات لوحة الإدارة - الإعدادات والآخر
    application.add_handler(CallbackQueryHandler(admin_broadcast, pattern="^admin_broadcast$"))
    application.add_handler(CallbackQueryHandler(admin_settings, pattern="^admin_settings$"))
    application.add_handler(CallbackQueryHandler(admin_edit_setting, pattern="^admin_edit_setting_"))
    application.add_handler(CallbackQueryHandler(admin_security_logs, pattern="^admin_security_logs$"))
    application.add_handler(CallbackQueryHandler(admin_backup, pattern="^admin_backup$"))
    
    # معالجات الدفع
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    
    # معالج الرسائل النصية (يجب أن يكون في النهاية)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
    
    # معالج الأخطاء
    application.add_error_handler(error_handler)
    
    # تشغيل البوت
    logger.info("✅ البوت يعمل الآن!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
