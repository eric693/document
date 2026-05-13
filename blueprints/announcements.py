"""
blueprints/announcements.py — 公告管理模組
"""
from flask import Blueprint, session, request, jsonify

from auth import require_module
from db import get_db

bp = Blueprint('announcements', __name__)


# ─── DB init ─────────────────────────────────────────────────────────────────

def init_announcement_db():
    try:
        with get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS announcements (
                    id          SERIAL PRIMARY KEY,
                    title       TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    category    TEXT DEFAULT 'general',
                    priority    TEXT DEFAULT 'normal',
                    is_pinned   BOOLEAN DEFAULT FALSE,
                    visible_to  TEXT DEFAULT 'all',
                    published_at TIMESTAMPTZ DEFAULT NOW(),
                    expires_at  TIMESTAMPTZ,
                    author      TEXT DEFAULT '管理員',
                    active      BOOLEAN DEFAULT TRUE,
                    view_count  INT DEFAULT 0,
                    created_at  TIMESTAMPTZ DEFAULT NOW(),
                    updated_at  TIMESTAMPTZ DEFAULT NOW()
                )
            """)
    except Exception as e:
        print(f"[announcement_init] {e}")


# ─── Row helper ──────────────────────────────────────────────────────────────

def ann_row(row):
    if not row: return None
    d = dict(row)
    if d.get('published_at'): d['published_at'] = d['published_at'].isoformat()
    if d.get('expires_at'):   d['expires_at']   = d['expires_at'].isoformat()
    if d.get('created_at'):   d['created_at']   = d['created_at'].isoformat()
    if d.get('updated_at'):   d['updated_at']   = d['updated_at'].isoformat()
    return d


# ─── LINE broadcast helper ───────────────────────────────────────────────────

def _broadcast_announcement_line(title, content):
    """廣播公告到所有有綁定 LINE 的員工"""
    try:
        from linebot import LineBotApi
        from linebot.models import TextSendMessage
        with get_db() as conn:
            cfg = conn.execute("SELECT * FROM line_punch_config WHERE id=1").fetchone()
            if not cfg or not cfg.get('enabled') or not cfg.get('channel_access_token'):
                return
            staff_rows = conn.execute(
                "SELECT line_user_id FROM punch_staff WHERE line_user_id IS NOT NULL AND active=TRUE"
            ).fetchall()
        api = LineBotApi(cfg['channel_access_token'])
        msg = f"[公告] {title}\n{content}"
        for s in staff_rows:
            try:
                api.push_message(s['line_user_id'], TextSendMessage(text=msg))
            except Exception:
                pass
    except Exception as e:
        print(f"[broadcast_ann] {e}")


# ─── Admin: CRUD ─────────────────────────────────────────────────────────────

@bp.route('/api/announcements', methods=['GET'])
@require_module('ann')
def api_ann_list_admin():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM announcements
            ORDER BY is_pinned DESC, published_at DESC
            LIMIT 200
        """).fetchall()
    return jsonify([ann_row(r) for r in rows])


@bp.route('/api/announcements', methods=['POST'])
@require_module('ann')
def api_ann_create():
    b = request.get_json(force=True)
    if not b.get('title','').strip():
        return jsonify({'error': '請填寫公告標題'}), 400
    if not b.get('content','').strip():
        return jsonify({'error': '請填寫公告內容'}), 400
    expires = b.get('expires_at') or None
    with get_db() as conn:
        row = conn.execute("""
            INSERT INTO announcements
              (title, content, category, priority, is_pinned,
               visible_to, expires_at, author, active)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
        """, (b['title'].strip(), b['content'].strip(),
              b.get('category','general'), b.get('priority','normal'),
              bool(b.get('is_pinned', False)), b.get('visible_to','all'),
              expires, b.get('author','管理員').strip(),
              bool(b.get('active', True)))).fetchone()
    if row and row['active']:
        _broadcast_announcement_line(row['title'], row['content'])
    return jsonify(ann_row(row)), 201


@bp.route('/api/announcements/<int:aid>', methods=['PUT'])
@require_module('ann')
def api_ann_update(aid):
    b = request.get_json(force=True)
    if not b.get('title','').strip():
        return jsonify({'error': '請填寫公告標題'}), 400
    expires = b.get('expires_at') or None
    with get_db() as conn:
        row = conn.execute("""
            UPDATE announcements SET
              title=%s, content=%s, category=%s, priority=%s,
              is_pinned=%s, visible_to=%s, expires_at=%s,
              author=%s, active=%s, updated_at=NOW()
            WHERE id=%s RETURNING *
        """, (b['title'].strip(), b.get('content','').strip(),
              b.get('category','general'), b.get('priority','normal'),
              bool(b.get('is_pinned', False)), b.get('visible_to','all'),
              expires, b.get('author','管理員').strip(),
              bool(b.get('active', True)), aid)).fetchone()
    return jsonify(ann_row(row)) if row else ('', 404)


@bp.route('/api/announcements/<int:aid>', methods=['DELETE'])
@require_module('ann')
def api_ann_delete(aid):
    with get_db() as conn:
        conn.execute("DELETE FROM announcements WHERE id=%s", (aid,))
    return jsonify({'deleted': aid})


@bp.route('/api/announcements/<int:aid>/pin', methods=['POST'])
@require_module('ann')
def api_ann_toggle_pin(aid):
    with get_db() as conn:
        row = conn.execute(
            "UPDATE announcements SET is_pinned=NOT is_pinned, updated_at=NOW() WHERE id=%s RETURNING *",
            (aid,)
        ).fetchone()
    return jsonify(ann_row(row)) if row else ('', 404)


# ─── Public: employee reads ──────────────────────────────────────────────────

@bp.route('/api/announcements/public', methods=['GET'])
def api_ann_public():
    """員工端讀取有效公告"""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM announcements
            WHERE active = TRUE
              AND (expires_at IS NULL OR expires_at > NOW())
            ORDER BY is_pinned DESC, published_at DESC
            LIMIT 50
        """).fetchall()
    return jsonify([ann_row(r) for r in rows])


@bp.route('/api/announcements/<int:aid>/view', methods=['POST'])
def api_ann_view(aid):
    with get_db() as conn:
        conn.execute(
            "UPDATE announcements SET view_count = view_count + 1 WHERE id=%s", (aid,)
        )
    return jsonify({'ok': True})
