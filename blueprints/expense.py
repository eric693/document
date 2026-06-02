"""
blueprints/expense.py — 費用報帳申請模組
"""
import json as _json

from flask import Blueprint, session, request, jsonify, Response

from auth import login_required
from db import get_db
from config import ANTHROPIC_API_KEY
from blueprints.notifications import _notify_review_result

bp = Blueprint('expense', __name__)


# ─── DB init ─────────────────────────────────────────────────────────────────

def init_expense_db():
    sqls = [
        """CREATE TABLE IF NOT EXISTS expense_claims (
            id                SERIAL PRIMARY KEY,
            staff_id          INT REFERENCES punch_staff(id) ON DELETE CASCADE,
            title             TEXT NOT NULL,
            amount            NUMERIC(12,2) NOT NULL DEFAULT 0,
            expense_date      DATE NOT NULL,
            category          TEXT DEFAULT '',
            note              TEXT DEFAULT '',
            status            TEXT NOT NULL DEFAULT 'pending',
            document_id       INT REFERENCES finance_documents(id) ON DELETE SET NULL,
            review_note       TEXT DEFAULT '',
            reviewed_by       TEXT DEFAULT '',
            reviewed_at       TIMESTAMPTZ,
            finance_record_id INT REFERENCES finance_records(id) ON DELETE SET NULL,
            created_at        TIMESTAMPTZ DEFAULT NOW()
        )""",
    ]
    for sql in sqls:
        try:
            with get_db() as conn:
                conn.execute(sql)
        except Exception as e:
            print(f"[expense_init] {e}")


def _expense_row(r):
    if not r: return None
    d = dict(r)
    if d.get('expense_date'): d['expense_date'] = str(d['expense_date'])
    if d.get('reviewed_at'): d['reviewed_at'] = d['reviewed_at'].isoformat()
    if d.get('created_at'):  d['created_at']  = d['created_at'].isoformat()
    if d.get('amount') is not None: d['amount'] = float(d['amount'])
    return d


# ── Employee endpoints ────────────────────────────────────────────────────────

@bp.route('/api/expense/my-claims', methods=['GET'])
def api_expense_my_list():
    sid = session.get('punch_staff_id')
    if not sid: return jsonify({'error': '請先登入'}), 401
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM expense_claims WHERE staff_id=%s ORDER BY created_at DESC LIMIT 50
        """, (sid,)).fetchall()
    return jsonify([_expense_row(r) for r in rows])


@bp.route('/api/expense/my-claims', methods=['POST'])
def api_expense_submit():
    sid = session.get('punch_staff_id')
    if not sid: return jsonify({'error': '請先登入'}), 401
    b = request.get_json(force=True)
    if not b.get('title', '').strip():  return jsonify({'error': '請填寫標題'}), 400
    if not b.get('expense_date'):       return jsonify({'error': '請填寫費用日期'}), 400
    with get_db() as conn:
        row = conn.execute("""
            INSERT INTO expense_claims
              (staff_id, title, amount, expense_date, category, note, document_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *
        """, (sid, b['title'].strip(), float(b.get('amount', 0)),
              b['expense_date'], b.get('category', '').strip(),
              b.get('note', '').strip(), b.get('document_id') or None)).fetchone()
    return jsonify(_expense_row(row)), 201


@bp.route('/api/expense/ocr', methods=['POST'])
def api_expense_ocr():
    """員工自助 OCR — 複用 finance OCR 邏輯"""
    sid = session.get('punch_staff_id')
    if not sid: return jsonify({'error': '請先登入'}), 401
    import anthropic as _ant
    import base64
    import re as _re2
    if not ANTHROPIC_API_KEY:
        return jsonify({'error': '尚未設定 ANTHROPIC_API_KEY'}), 500
    file = request.files.get('file')
    if not file: return jsonify({'error': '請上傳圖片'}), 400
    raw = file.read()
    media_type = file.content_type or 'image/jpeg'
    if media_type not in ('image/jpeg', 'image/png', 'image/gif', 'image/webp'):
        media_type = 'image/jpeg'
    img_b64 = base64.standard_b64encode(raw).decode()
    client = _ant.Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        msg = client.messages.create(
            model='claude-sonnet-4-6', max_tokens=512,
            messages=[{'role': 'user', 'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type, 'data': img_b64}},
                {'type': 'text', 'text': '請辨識此收據或發票，以JSON格式回傳：{"date":"YYYY-MM-DD","vendor":"廠商","title":"建議標題","total_amount":數字,"doc_type":"receipt或invoice"}\n只回傳JSON。'}
            ]}]
        )
        text = msg.content[0].text.strip()
        text = _re2.sub(r'^```json\s*', '', text, flags=_re2.MULTILINE)
        text = _re2.sub(r'\s*```$', '', text, flags=_re2.MULTILINE)
        result = _json.loads(text)
    except Exception as e:
        return jsonify({'error': f'OCR 失敗：{e}'}), 500
    try:
        with get_db() as conn:
            doc = conn.execute("""
                INSERT INTO finance_documents (filename, doc_type, ocr_raw)
                VALUES (%s,%s,%s) RETURNING id
            """, (file.filename, result.get('doc_type', ''), _json.dumps(result))).fetchone()
        result['document_id'] = doc['id']
    except Exception as e:
        print(f"[expense_ocr doc] {e}")
    return jsonify(result)


# 註：/api/leave/upload-cert 與 /api/documents/<id>/image 統一由 leave.py 提供
#     （原本兩處重複定義，leave 先註冊生效，已移除此處死碼）


# ── Admin endpoints ───────────────────────────────────────────────────────────

@bp.route('/api/expense/claims', methods=['GET'])
@login_required
def api_expense_admin_list():
    status = request.args.get('status', '')
    conds, params = ['TRUE'], []
    if status: conds.append("ec.status=%s"); params.append(status)
    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT ec.*, ps.name as staff_name, ps.employee_code
            FROM expense_claims ec
            JOIN punch_staff ps ON ps.id=ec.staff_id
            WHERE {' AND '.join(conds)}
            ORDER BY ec.created_at DESC
        """, params).fetchall()
    result = []
    for r in rows:
        d = _expense_row(r)
        d['staff_name']    = r['staff_name']
        d['employee_code'] = r['employee_code']
        result.append(d)
    return jsonify(result)


@bp.route('/api/expense/claims/<int:cid>', methods=['PUT'])
@login_required
def api_expense_review(cid):
    b      = request.get_json(force=True)
    action = b.get('action')  # approve / reject
    if action not in ('approve', 'reject'):
        return jsonify({'error': 'invalid action'}), 400
    reviewed_by = session.get('admin_display_name', '管理員')
    review_note = b.get('review_note', '').strip()
    new_status  = 'approved' if action == 'approve' else 'rejected'
    finance_rid = None

    with get_db() as conn:
        claim = conn.execute("SELECT * FROM expense_claims WHERE id=%s", (cid,)).fetchone()
        if not claim: return ('', 404)

        if action == 'approve' and b.get('create_finance_record', True):
            cat = conn.execute(
                "SELECT id FROM finance_categories WHERE type='expense' AND active=TRUE ORDER BY sort_order LIMIT 1"
            ).fetchone()
            frec = conn.execute("""
                INSERT INTO finance_records
                  (record_date, category_id, type, title, amount, note, document_id, created_by)
                VALUES (%s,%s,'expense',%s,%s,%s,%s,'expense-claim') RETURNING id
            """, (claim['expense_date'], cat['id'] if cat else None,
                  claim['title'], claim['amount'],
                  f"報帳申請 #{cid}：{claim['note'] or ''}",
                  claim['document_id'])).fetchone()
            finance_rid = frec['id']

        row = conn.execute("""
            UPDATE expense_claims SET
              status=%s, reviewed_by=%s, review_note=%s,
              reviewed_at=NOW(), finance_record_id=%s
            WHERE id=%s RETURNING *
        """, (new_status, reviewed_by, review_note, finance_rid, cid)).fetchone()

    if row:
        extra = f"標題：{claim['title']}　金額：${float(claim['amount']):,.0f}"
        if review_note: extra += f"\n意見：{review_note}"
        _notify_review_result(claim['staff_id'], '費用報帳', action, extra)

    return jsonify(_expense_row(row)) if row else ('', 404)
