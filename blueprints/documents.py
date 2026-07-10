"""
blueprints/documents.py — 文件管理模組（員工資料缺件提醒）

每位員工 × 每個文件項目一格。狀態判定順序：
1. 有手動記錄 → 依記錄 status（received / missing / na）
2. 項目有設定「自動帶入欄位」且員工該欄位有值 → 視為已收（auto）
3. 其餘 → 缺件
"""
from flask import Blueprint, request, jsonify, session

from auth import require_module
from db import get_db

bp = Blueprint('documents', __name__)

# 「自動帶入欄位」白名單（doc type 可綁定 punch_staff 欄位，避免 SQL injection）
STAFF_FIELD_WHITELIST = {
    'name':          '姓名',
    'birth_date':    '生日',
    'hire_date':     '到職日',
    'employee_code': '員工編號',
    'bank_account':  '轉薪帳號',
    'line_user_id':  'LINE 綁定',
}

# 預設文件項目（首次建表時 seed）
DEFAULT_DOC_TYPES = [
    ('姓名',       'name'),
    ('電話',       ''),
    ('身分證字號', ''),
    ('生日',       'birth_date'),
    ('住址',       ''),
    ('緊急聯絡人', ''),
    ('轉薪帳號',   'bank_account'),
]


# ─── DB init ─────────────────────────────────────────────────────────────────

def init_documents_db():
    try:
        with get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS document_types (
                    id          SERIAL PRIMARY KEY,
                    name        TEXT NOT NULL,
                    required    BOOLEAN DEFAULT TRUE,
                    staff_field TEXT DEFAULT '',
                    active      BOOLEAN DEFAULT TRUE,
                    sort_order  INT DEFAULT 0,
                    created_at  TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS staff_documents (
                    id            SERIAL PRIMARY KEY,
                    staff_id      INT REFERENCES punch_staff(id) ON DELETE CASCADE,
                    doc_type_id   INT REFERENCES document_types(id) ON DELETE CASCADE,
                    status        TEXT DEFAULT 'missing',
                    content       TEXT DEFAULT '',
                    note          TEXT DEFAULT '',
                    received_date DATE,
                    updated_by    TEXT DEFAULT '',
                    updated_at    TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(staff_id, doc_type_id)
                )
            """)
            # seed 預設項目（僅在空表時）
            exists = conn.execute("SELECT 1 FROM document_types LIMIT 1").fetchone()
            if not exists:
                for i, (name, field) in enumerate(DEFAULT_DOC_TYPES):
                    conn.execute(
                        "INSERT INTO document_types (name, staff_field, sort_order) VALUES (%s,%s,%s)",
                        (name, field, i)
                    )
    except Exception as e:
        print(f"[documents_init] {e}")


# ─── 狀態矩陣共用邏輯 ────────────────────────────────────────────────────────

def _build_matrix(conn):
    """回傳 (types, staff_rows)。staff_rows 每筆含 items{type_id: cell} 與 missing_count"""
    types = [dict(t) for t in conn.execute(
        "SELECT * FROM document_types WHERE active=TRUE ORDER BY sort_order, id"
    ).fetchall()]
    staff = conn.execute("""
        SELECT id, name, department, employee_code, birth_date, hire_date,
               bank_account, line_user_id
        FROM punch_staff WHERE active=TRUE
        ORDER BY sort_order, id
    """).fetchall()
    recs = conn.execute("SELECT * FROM staff_documents").fetchall()

    by_key = {(r['staff_id'], r['doc_type_id']): r for r in recs}

    rows = []
    for s in staff:
        items = {}
        missing = 0
        for t in types:
            rec = by_key.get((s['id'], t['id']))
            if rec:
                cell = {
                    'status': rec['status'], 'source': 'manual',
                    'content': rec['content'] or '', 'note': rec['note'] or '',
                    'received_date': str(rec['received_date']) if rec['received_date'] else '',
                    'updated_by': rec['updated_by'] or '',
                }
            else:
                field = t.get('staff_field') or ''
                val = s.get(field) if field in STAFF_FIELD_WHITELIST else None
                if val:
                    cell = {'status': 'received', 'source': 'auto',
                            'content': str(val), 'note': '', 'received_date': '', 'updated_by': ''}
                else:
                    cell = {'status': 'missing', 'source': 'none',
                            'content': '', 'note': '', 'received_date': '', 'updated_by': ''}
            if cell['status'] == 'missing' and t['required']:
                missing += 1
            items[str(t['id'])] = cell
        rows.append({
            'id': s['id'], 'name': s['name'], 'department': s['department'] or '',
            'items': items, 'missing_count': missing,
        })
    for t in types:
        t['created_at'] = str(t['created_at'])
    return types, rows


# ─── 文件項目 CRUD ───────────────────────────────────────────────────────────

@bp.route('/api/documents/types', methods=['GET'])
@require_module('docs')
def api_doc_types():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM document_types ORDER BY sort_order, id"
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['created_at'] = str(d['created_at'])
        result.append(d)
    return jsonify({'types': result, 'staff_fields': STAFF_FIELD_WHITELIST})


@bp.route('/api/documents/types', methods=['POST'])
@require_module('docs')
def api_doc_type_create():
    b = request.get_json(force=True) or {}
    name = (b.get('name') or '').strip()
    if not name:
        return jsonify({'error': '請輸入項目名稱'}), 400
    staff_field = b.get('staff_field') or ''
    if staff_field and staff_field not in STAFF_FIELD_WHITELIST:
        return jsonify({'error': '無效的自動帶入欄位'}), 400
    with get_db() as conn:
        dup = conn.execute(
            "SELECT 1 FROM document_types WHERE name=%s", (name,)
        ).fetchone()
        if dup:
            return jsonify({'error': '已有同名項目'}), 400
        mx = conn.execute("SELECT COALESCE(MAX(sort_order),0)+1 AS n FROM document_types").fetchone()
        row = conn.execute(
            """INSERT INTO document_types (name, required, staff_field, sort_order)
               VALUES (%s,%s,%s,%s) RETURNING id""",
            (name, bool(b.get('required', True)), staff_field, mx['n'])
        ).fetchone()
    return jsonify({'ok': True, 'id': row['id']})


@bp.route('/api/documents/types/<int:tid>', methods=['PUT'])
@require_module('docs')
def api_doc_type_update(tid):
    b = request.get_json(force=True) or {}
    name = (b.get('name') or '').strip()
    if not name:
        return jsonify({'error': '請輸入項目名稱'}), 400
    staff_field = b.get('staff_field') or ''
    if staff_field and staff_field not in STAFF_FIELD_WHITELIST:
        return jsonify({'error': '無效的自動帶入欄位'}), 400
    with get_db() as conn:
        dup = conn.execute(
            "SELECT 1 FROM document_types WHERE name=%s AND id!=%s", (name, tid)
        ).fetchone()
        if dup:
            return jsonify({'error': '已有同名項目'}), 400
        conn.execute(
            """UPDATE document_types SET
                 name=%s, required=%s, staff_field=%s, active=%s, sort_order=%s
               WHERE id=%s""",
            (name, bool(b.get('required', True)), staff_field,
             bool(b.get('active', True)), int(b.get('sort_order', 0)), tid)
        )
    return jsonify({'ok': True})


@bp.route('/api/documents/types/<int:tid>', methods=['DELETE'])
@require_module('docs')
def api_doc_type_delete(tid):
    with get_db() as conn:
        conn.execute("DELETE FROM document_types WHERE id=%s", (tid,))
    return jsonify({'ok': True})


# ─── 缺件矩陣 / 記錄維護 ─────────────────────────────────────────────────────

@bp.route('/api/documents/matrix', methods=['GET'])
@require_module('docs')
def api_doc_matrix():
    with get_db() as conn:
        types, rows = _build_matrix(conn)
    return jsonify({'types': types, 'staff': rows})


@bp.route('/api/documents/set', methods=['POST'])
@require_module('docs')
def api_doc_set():
    b = request.get_json(force=True) or {}
    staff_id = b.get('staff_id')
    doc_type_id = b.get('doc_type_id')
    status = b.get('status') or 'missing'
    if not staff_id or not doc_type_id:
        return jsonify({'error': '缺少必填欄位'}), 400

    with get_db() as conn:
        # 'clear'：刪除手動記錄，回復自動判定
        if status == 'clear':
            conn.execute(
                "DELETE FROM staff_documents WHERE staff_id=%s AND doc_type_id=%s",
                (staff_id, doc_type_id)
            )
            return jsonify({'ok': True})

        if status not in ('received', 'missing', 'na'):
            return jsonify({'error': '無效的狀態'}), 400
        ok = conn.execute(
            """SELECT (SELECT 1 FROM punch_staff WHERE id=%s) AS s,
                      (SELECT 1 FROM document_types WHERE id=%s) AS t""",
            (staff_id, doc_type_id)
        ).fetchone()
        if not ok['s'] or not ok['t']:
            return jsonify({'error': '員工或項目不存在'}), 404
        conn.execute(
            """INSERT INTO staff_documents
                 (staff_id, doc_type_id, status, content, note, received_date, updated_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (staff_id, doc_type_id) DO UPDATE SET
                 status=EXCLUDED.status, content=EXCLUDED.content, note=EXCLUDED.note,
                 received_date=EXCLUDED.received_date, updated_by=EXCLUDED.updated_by,
                 updated_at=NOW()""",
            (staff_id, doc_type_id, status,
             (b.get('content') or '').strip(), (b.get('note') or '').strip(),
             b.get('received_date') or None,
             session.get('admin_display_name', '管理員'))
        )
    return jsonify({'ok': True})


@bp.route('/api/documents/badge', methods=['GET'])
@require_module('docs')
def api_doc_badge():
    """nav badge：有缺件（必收項目）的在職員工人數"""
    with get_db() as conn:
        _, rows = _build_matrix(conn)
    missing_staff = [r for r in rows if r['missing_count'] > 0]
    return jsonify({
        'missing_staff': len(missing_staff),
        'missing_items': sum(r['missing_count'] for r in missing_staff),
    })
