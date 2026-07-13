"""
blueprints/audit.py — 操作紀錄（audit log）

log_action() 由各模組呼叫記錄敏感操作；絕不拋出例外影響主流程。
檢視端點僅超級管理員可用。
"""
from flask import Blueprint, request, jsonify, session

from auth import require_super
from db import get_db

bp = Blueprint('audit', __name__)


def init_audit_db():
    try:
        with get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id         SERIAL PRIMARY KEY,
                    actor      TEXT DEFAULT '',
                    actor_type TEXT DEFAULT 'admin',
                    action     TEXT NOT NULL,
                    target     TEXT DEFAULT '',
                    detail     TEXT DEFAULT '',
                    ip         TEXT DEFAULT '',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs (created_at DESC)")
    except Exception as e:
        print(f"[audit_init] {e}")


def log_action(action, target='', detail=''):
    """記錄一筆操作。失敗僅印 log，不影響主流程。"""
    try:
        if session.get('logged_in'):
            actor = session.get('admin_display_name') or session.get('admin_username') or '管理員'
            actor_type = 'admin'
        elif session.get('punch_staff_id'):
            actor = session.get('punch_staff_name') or f"員工#{session.get('punch_staff_id')}"
            actor_type = 'staff'
        else:
            actor, actor_type = '(未登入)', 'unknown'
        ip = (request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
              or request.remote_addr or '')
        with get_db() as conn:
            conn.execute(
                "INSERT INTO audit_logs (actor, actor_type, action, target, detail, ip) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (actor, actor_type, str(action)[:100], str(target)[:200], str(detail)[:1000], ip[:60]))
    except Exception as e:
        print(f"[audit] {e}")


@bp.route('/api/audit/logs', methods=['GET'])
@require_super
def api_audit_logs():
    q = (request.args.get('q') or '').strip()
    limit = min(int(request.args.get('limit', 200)), 500)
    conds, params = ['TRUE'], []
    if q:
        conds.append("(actor ILIKE %s OR action ILIKE %s OR target ILIKE %s OR detail ILIKE %s)")
        like = f'%{q}%'
        params += [like, like, like, like]
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM audit_logs WHERE {' AND '.join(conds)} "
            f"ORDER BY id DESC LIMIT {limit}", params).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['created_at'] = d['created_at'].isoformat()
        result.append(d)
    return jsonify(result)
