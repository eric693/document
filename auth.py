"""
auth.py — 認證裝飾器
"""
from functools import wraps
from flask import session, jsonify, request, redirect, url_for


def login_required(f):
    """管理員後台：需要 session['logged_in']。API 路由回 401，頁面路由跳轉登入頁"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': '請先登入'}), 401
            return redirect(url_for('admin.admin_login'))
        return f(*args, **kwargs)
    return decorated


def require_module(module):
    """管理員後台：需要具備特定模組權限（超級管理員免檢查）"""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get('logged_in'):
                return jsonify({'error': '請先登入'}), 401
            if session.get('admin_is_super'):
                return f(*args, **kwargs)
            perms = session.get('admin_permissions') or []
            if module not in perms:
                return jsonify({'error': f'無「{module}」模組權限'}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


def require_super(f):
    """管理員後台：需要超級管理員"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return jsonify({'error': '請先登入'}), 401
        if not session.get('admin_is_super'):
            return jsonify({'error': '需要超級管理員權限'}), 403
        return f(*args, **kwargs)
    return decorated
