"""
auth.py — 認證裝飾器、登入失敗鎖定
"""
import time
from collections import defaultdict
from functools import wraps
from threading import Lock

from flask import session, jsonify, request, redirect, url_for

# ── 登入失敗鎖定（每個 worker 各自計數，門檻取保守值） ────────────
LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_SECONDS = 15 * 60

_login_failures = defaultdict(list)   # {key: [fail_ts, ...]}
_login_lock = Lock()


def _client_ip():
    return (request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
            or request.remote_addr or '')


def login_blocked(username: str) -> bool:
    """回傳該帳號+IP 是否因連續失敗被暫時鎖定"""
    key = f'{username}|{_client_ip()}'
    now = time.time()
    with _login_lock:
        fails = [t for t in _login_failures[key] if now - t < LOGIN_LOCK_SECONDS]
        _login_failures[key] = fails
        return len(fails) >= LOGIN_MAX_FAILURES


def record_login_failure(username: str):
    key = f'{username}|{_client_ip()}'
    with _login_lock:
        _login_failures[key].append(time.time())


def clear_login_failures(username: str):
    key = f'{username}|{_client_ip()}'
    with _login_lock:
        _login_failures.pop(key, None)


LOGIN_BLOCKED_MSG = '登入失敗次數過多，請 15 分鐘後再試'


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


def require_any_module(*modules):
    """管理員後台：具備任一指定模組權限即可（超級管理員免檢查）"""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get('logged_in'):
                return jsonify({'error': '請先登入'}), 401
            if session.get('admin_is_super'):
                return f(*args, **kwargs)
            perms = session.get('admin_permissions') or []
            if not any(m in perms for m in modules):
                return jsonify({'error': '無此功能的模組權限'}), 403
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
