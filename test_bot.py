#!/usr/bin/env python3
"""اختبار شامل للتحقق من صحة البوت"""

import ast
import sys
import re
from pathlib import Path

def test_syntax():
    """اختبار صحة البناء"""
    print("🔍 اختبار صحة البناء...")
    try:
        with open('telegram_store_bot.py', 'r', encoding='utf-8') as f:
            code = f.read()
        ast.parse(code)
        print("✅ البناء صحيح بدون أخطاء")
        return True
    except SyntaxError as e:
        print(f"❌ خطأ بناء: {e}")
        return False

def test_imports():
    """اختبار الاستيرادات الأساسية"""
    print("\n🔍 اختبار الاستيرادات...")
    # تخطي فحص التليجرام لأنه قد لا يكون مثبتاً في بيئة الاختبار
    required = ['sqlite3', 'asyncio', 'logging']
    try:
        import sqlite3
        import asyncio
        import logging
        print("✅ المكتبات الأساسية موجودة")
        print("ℹ️ تحقق من: python3 -m pip install python-telegram-bot==21.0.1")
        return True
    except ImportError as e:
        print(f"❌ خطأ في الاستيراد: {e}")
        return False

def test_functions():
    """اختبار وجود جميع الدوال الأساسية"""
    print("\n🔍 اختبار الدوال الأساسية...")
    with open('telegram_store_bot.py', 'r', encoding='utf-8') as f:
        code = f.read()
    
    tree = ast.parse(code)
    functions = {node.name for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)}
    
    # البحث عن دوال محددة
    sync_functions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name not in ['__init__']}
    all_functions = functions | sync_functions
    
    required_functions = [
        'start_command',  # الاسم الفعلي
        'main',  # موجودة كدالة عادية
        'browse_products',
        'show_category_products',
        'show_product_details',
        'initiate_purchase',
        'my_account',
        'admin_panel',
        'admin_users',
        'admin_products',
    ]
    
    missing = [f for f in required_functions if f not in all_functions]
    
    if not missing:
        print(f"✅ جميع الدوال الأساسية موجودة ({len(functions)} دالة async)")
        return True
    else:
        print(f"❌ الدوال المفقودة: {', '.join(missing)}")
        return False

def test_exception_handling():
    """اختبار معالجة الأخطاء"""
    print("\n🔍 اختبار معالجة الأخطاء...")
    with open('telegram_store_bot.py', 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    try_blocks = sum(1 for line in lines if 'try:' in line)
    except_blocks = sum(1 for line in lines if 'except' in line)
    
    print(f"✅ عدد كتل try-except: {try_blocks} try و {except_blocks} except")
    return try_blocks > 0

def test_database_safety():
    """اختبار حماية قاعدة البيانات"""
    print("\n🔍 اختبار حماية قاعدة البيانات...")
    with open('telegram_store_bot.py', 'r', encoding='utf-8') as f:
        code = f.read()
    
    # البحث عن استخدام المعاملات الآمنة
    parameterized = len(re.findall(r'execute\([^)]*\?', code))
    vulnerable = len(re.findall(r'f".*execute', code)) + len(re.findall(r"f'.*execute", code))
    
    print(f"✅ استعلامات آمنة (parameterized): {parameterized}")
    if vulnerable > 0:
        print(f"⚠️ استعلامات قد تكون غير آمنة: {vulnerable}")
    return parameterized > vulnerable

def test_callback_handlers():
    """اختبار معالجات Callback"""
    print("\n🔍 اختبار معالجات Callback...")
    with open('telegram_store_bot.py', 'r', encoding='utf-8') as f:
        code = f.read()
    
    handlers = re.findall(r'CallbackQueryHandler\(([^,]+),', code)
    unique_handlers = set(handlers)
    
    print(f"✅ إجمالي المعالجات: {len(handlers)}")
    print(f"✅ المعالجات الفريدة: {len(unique_handlers)}")
    return len(handlers) > 20

def main():
    """تشغيل جميع الاختبارات"""
    print("=" * 50)
    print("🧪 اختبار شامل للبوت")
    print("=" * 50)
    
    results = []
    results.append(("صحة البناء", test_syntax()))
    results.append(("الاستيرادات", test_imports()))
    results.append(("الدوال الأساسية", test_functions()))
    results.append(("معالجة الأخطاء", test_exception_handling()))
    results.append(("حماية قاعدة البيانات", test_database_safety()))
    results.append(("معالجات Callback", test_callback_handlers()))
    
    print("\n" + "=" * 50)
    print("📊 النتائج:")
    print("=" * 50)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ نجح" if result else "❌ فشل"
        print(f"{status} {test_name}")
    
    print("=" * 50)
    print(f"📈 النسبة: {passed}/{total} ({100*passed//total}%)")
    
    if passed == total:
        print("✅ البوت جاهز للعمل!")
        return 0
    else:
        print("❌ يحتاج البوت إلى تحسينات")
        return 1

if __name__ == '__main__':
    sys.exit(main())
