"""
blueprints/performance.py — 績效考核模組
"""
import json as _json
from datetime import datetime as _dtm

from flask import Blueprint, session, request, jsonify

from auth import login_required, require_module
from config import TW_TZ
from db import get_db
from blueprints.notifications import _notify_staff_line

bp = Blueprint('performance', __name__)


# ─── DB init ─────────────────────────────────────────────────────────────────

def _init_performance_db():
    sqls = [
        """CREATE TABLE IF NOT EXISTS performance_templates (
            id          SERIAL PRIMARY KEY,
            name        TEXT NOT NULL,
            description TEXT DEFAULT '',
            period      TEXT DEFAULT 'quarterly',
            items       JSONB DEFAULT '[]',
            active      BOOLEAN DEFAULT TRUE,
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS performance_reviews (
            id              SERIAL PRIMARY KEY,
            staff_id        INT REFERENCES punch_staff(id) ON DELETE CASCADE,
            template_id     INT REFERENCES performance_templates(id) ON DELETE SET NULL,
            period_label    TEXT NOT NULL,
            scores          JSONB DEFAULT '{}',
            total_score     NUMERIC(6,2) DEFAULT 0,
            max_score       NUMERIC(6,2) DEFAULT 100,
            grade           TEXT DEFAULT '',
            comments        TEXT DEFAULT '',
            reviewer        TEXT DEFAULT '',
            salary_adjusted BOOLEAN DEFAULT FALSE,
            salary_delta    NUMERIC(12,2) DEFAULT 0,
            reviewed_at     TIMESTAMPTZ DEFAULT NOW(),
            created_at      TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS performance_config (
            key        TEXT PRIMARY KEY,
            value      JSONB NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )""",
    ]
    for sql in sqls:
        try:
            with get_db() as conn:
                conn.execute(sql)
        except Exception as e:
            print(f"[perf_init] {e}")


_DEFAULT_GRADE_CONFIG = [
    {'grade': 'A', 'label': '優秀', 'min_pct': 90},
    {'grade': 'B', 'label': '良好', 'min_pct': 75},
    {'grade': 'C', 'label': '待加強', 'min_pct': 60},
    {'grade': 'D', 'label': '需改善', 'min_pct':  0},
]


def _get_grade_config():
    """從 DB 讀取評級設定，若未設定則回傳預設值（按門檻由高到低排序）。"""
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT value FROM performance_config WHERE key='grade_config'"
            ).fetchone()
        if row:
            cfg = row['value']
            if isinstance(cfg, str):
                cfg = _json.loads(cfg)
            if isinstance(cfg, list) and cfg:
                return sorted(cfg, key=lambda x: -float(x.get('min_pct', 0)))
    except Exception:
        pass
    return _DEFAULT_GRADE_CONFIG


def _grade_labels():
    return {c['grade']: c['label'] for c in _get_grade_config()}


def _perf_template_row(r):
    if not r: return None
    d = dict(r)
    if d.get('created_at'): d['created_at'] = d['created_at'].isoformat()
    if isinstance(d.get('items'), str):
        try: d['items'] = _json.loads(d['items'])
        except: d['items'] = []
    return d


def _perf_review_row(r):
    if not r: return None
    d = dict(r)
    for f in ('reviewed_at', 'created_at'):
        if d.get(f): d[f] = d[f].isoformat()
    if isinstance(d.get('scores'), str):
        try: d['scores'] = _json.loads(d['scores'])
        except: d['scores'] = {}
    if d.get('total_score') is not None: d['total_score'] = float(d['total_score'])
    if d.get('max_score')   is not None: d['max_score']   = float(d['max_score'])
    if d.get('salary_delta')is not None: d['salary_delta']= float(d['salary_delta'])
    return d


def _score_to_grade(pct):
    for cfg in _get_grade_config():
        if pct >= cfg['min_pct']:
            return cfg['grade']
    return _get_grade_config()[-1]['grade']


# ─── 考核範本 CRUD ────────────────────────────────────────────────────────────

@bp.route('/api/performance/templates', methods=['GET'])
@require_module('perf')
def api_perf_templates_list():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM performance_templates ORDER BY created_at DESC"
        ).fetchall()
    return jsonify([_perf_template_row(r) for r in rows])


@bp.route('/api/performance/templates', methods=['POST'])
@require_module('perf')
def api_perf_template_create():
    b = request.get_json(force=True)
    name = (b.get('name') or '').strip()
    if not name: return jsonify({'error': '請填寫範本名稱'}), 400
    items = b.get('items', [])
    with get_db() as conn:
        row = conn.execute("""
            INSERT INTO performance_templates (name, description, period, items)
            VALUES (%s,%s,%s,%s) RETURNING *
        """, (name, b.get('description',''), b.get('period','quarterly'),
              _json.dumps(items))).fetchone()
    return jsonify(_perf_template_row(row)), 201


@bp.route('/api/performance/templates/<int:tid>', methods=['PUT'])
@require_module('perf')
def api_perf_template_update(tid):
    b = request.get_json(force=True)
    with get_db() as conn:
        row = conn.execute("""
            UPDATE performance_templates
            SET name=%s, description=%s, period=%s, items=%s, active=%s
            WHERE id=%s RETURNING *
        """, (b.get('name','').strip(), b.get('description',''),
              b.get('period','quarterly'), _json.dumps(b.get('items',[])),
              bool(b.get('active', True)), tid)).fetchone()
    return jsonify(_perf_template_row(row)) if row else ('', 404)


@bp.route('/api/performance/templates/<int:tid>', methods=['DELETE'])
@require_module('perf')
def api_perf_template_delete(tid):
    with get_db() as conn:
        conn.execute("DELETE FROM performance_templates WHERE id=%s", (tid,))
    return jsonify({'deleted': tid})


# ─── 考核記錄 CRUD ────────────────────────────────────────────────────────────

@bp.route('/api/performance/reviews', methods=['GET'])
@require_module('perf')
def api_perf_reviews_list():
    staff_id = request.args.get('staff_id')
    period   = request.args.get('period')
    conds, params = ['TRUE'], []
    if staff_id: conds.append("pr.staff_id=%s"); params.append(int(staff_id))
    if period:   conds.append("pr.period_label ILIKE %s"); params.append(f'%{period}%')
    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT pr.*,
                   ps.name  AS staff_name,  ps.role   AS staff_role,
                   pt.name  AS tpl_name
            FROM performance_reviews pr
            JOIN punch_staff         ps ON ps.id = pr.staff_id
            LEFT JOIN performance_templates pt ON pt.id = pr.template_id
            WHERE {' AND '.join(conds)}
            ORDER BY pr.reviewed_at DESC
        """, params).fetchall()
    result = []
    for r in rows:
        d = _perf_review_row(r)
        d['staff_name']   = r['staff_name']
        d['staff_role']   = r['staff_role']
        d['template_name'] = r['tpl_name'] or ''
        result.append(d)
    return jsonify(result)


@bp.route('/api/performance/reviews', methods=['POST'])
@require_module('perf')
def api_perf_review_create():
    b           = request.get_json(force=True)
    staff_id    = b.get('staff_id')
    template_id = b.get('template_id')
    period_label= (b.get('period_label') or '').strip()
    scores      = b.get('scores', {})
    comments    = (b.get('comments') or '').strip()
    reviewer    = (b.get('reviewer') or '').strip() or session.get('admin_display_name', '管理員')

    if not staff_id or not period_label:
        return jsonify({'error': '請選擇員工及考核期間'}), 400

    total = 0.0; max_s = 100.0
    if template_id:
        with get_db() as conn:
            tpl = conn.execute(
                "SELECT items FROM performance_templates WHERE id=%s", (template_id,)
            ).fetchone()
        if tpl:
            items = tpl.get('items') or []
            if isinstance(items, str):
                try: items = _json.loads(items)
                except: items = []
            if items:
                max_s = sum(float(it.get('max_score', 10)) for it in items)
                total = sum(
                    float(scores.get(str(it.get('id', it.get('name',''))), 0))
                    for it in items
                )
    else:
        total = float(b.get('total_score', 0))
        max_s = float(b.get('max_score', 100))

    pct   = (total / max_s * 100) if max_s > 0 else 0
    grade = _score_to_grade(pct)

    with get_db() as conn:
        row = conn.execute("""
            INSERT INTO performance_reviews
              (staff_id, template_id, period_label, scores, total_score,
               max_score, grade, comments, reviewer, reviewed_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW()) RETURNING *
        """, (staff_id, template_id or None, period_label,
              _json.dumps(scores), round(total, 2), round(max_s, 2),
              grade, comments, reviewer)).fetchone()
        staff = conn.execute(
            "SELECT name FROM punch_staff WHERE id=%s", (staff_id,)
        ).fetchone()

    grade_labels = _grade_labels()
    msg = (f"[績效考核] {period_label} 考核結果\n"
           f"總分：{total:.1f} / {max_s:.0f}（{pct:.0f}%）\n"
           f"評級：{grade} {grade_labels.get(grade,'')}\n"
           f"考核人：{reviewer}\n"
           + (f"備注：{comments[:60]}\n" if comments else '')
           + "請至員工系統查看詳情。")
    _notify_staff_line(staff_id, msg)

    d = _perf_review_row(row)
    d['staff_name'] = staff['name'] if staff else ''
    return jsonify(d), 201


@bp.route('/api/performance/reviews/<int:rid>', methods=['PUT'])
@require_module('perf')
def api_perf_review_update(rid):
    b        = request.get_json(force=True)
    scores   = b.get('scores', {})
    comments = (b.get('comments') or '').strip()
    with get_db() as conn:
        rev = conn.execute(
            "SELECT * FROM performance_reviews WHERE id=%s", (rid,)
        ).fetchone()
        if not rev: return ('', 404)
        total = float(b.get('total_score', rev['total_score']))
        max_s = float(b.get('max_score',   rev['max_score']))
        pct   = (total / max_s * 100) if max_s > 0 else 0
        grade = _score_to_grade(pct)
        row = conn.execute("""
            UPDATE performance_reviews
            SET scores=%s, total_score=%s, max_score=%s, grade=%s,
                comments=%s, reviewed_at=NOW()
            WHERE id=%s RETURNING *
        """, (_json.dumps(scores), round(total,2), round(max_s,2),
              grade, comments, rid)).fetchone()
    return jsonify(_perf_review_row(row)) if row else ('', 404)


@bp.route('/api/performance/reviews/<int:rid>/adjust-salary', methods=['POST'])
@require_module('perf')
def api_perf_adjust_salary(rid):
    """依考核結果調薪 — 直接更新員工底薪並記錄"""
    b     = request.get_json(force=True)
    delta = float(b.get('salary_delta', b.get('delta', 0)))
    note  = (b.get('note') or '').strip()
    if delta == 0: return jsonify({'error': '調薪金額不可為 0'}), 400
    with get_db() as conn:
        rev = conn.execute(
            "SELECT * FROM performance_reviews WHERE id=%s", (rid,)
        ).fetchone()
        if not rev: return ('', 404)
        staff = conn.execute(
            "SELECT id, name, base_salary FROM punch_staff WHERE id=%s", (rev['staff_id'],)
        ).fetchone()
        if not staff: return ('', 404)
        new_salary = float(staff['base_salary'] or 0) + delta
        conn.execute(
            "UPDATE punch_staff SET base_salary=%s WHERE id=%s",
            (new_salary, staff['id'])
        )
        conn.execute("""
            UPDATE performance_reviews
            SET salary_adjusted=TRUE, salary_delta=%s
            WHERE id=%s
        """, (delta, rid))

    direction = '調升' if delta > 0 else '調降'
    msg = (f"[薪資調整] 績效考核連動\n"
           f"考核期：{rev['period_label']}　評級：{rev['grade']}\n"
           f"{direction} NT$ {abs(delta):,.0f}\n"
           f"新底薪：NT$ {new_salary:,.0f}\n"
           + (f"說明：{note}" if note else ''))
    _notify_staff_line(staff['id'], msg)

    return jsonify({'ok': True, 'new_salary': new_salary, 'delta': delta})


# ─── 員工查自己的考核 ─────────────────────────────────────────────────────────

@bp.route('/api/performance/my-reviews', methods=['GET'])
def api_perf_my_reviews():
    from flask import session as _sess
    sid = _sess.get('punch_staff_id')
    if not sid: return jsonify({'error': 'not logged in'}), 401
    with get_db() as conn:
        rows = conn.execute("""
            SELECT pr.*, pt.name AS tpl_name
            FROM performance_reviews pr
            LEFT JOIN performance_templates pt ON pt.id=pr.template_id
            WHERE pr.staff_id=%s
            ORDER BY pr.reviewed_at DESC LIMIT 10
        """, (sid,)).fetchall()
    result = []
    for r in rows:
        d = _perf_review_row(r)
        d['template_name'] = r['tpl_name'] or ''
        result.append(d)
    return jsonify(result)


# ─── 評級設定 CRUD ────────────────────────────────────────────────────────────

@bp.route('/api/performance/config', methods=['GET'])
@require_module('perf')
def api_perf_config_get():
    return jsonify({'grades': _get_grade_config()})


@bp.route('/api/performance/config', methods=['PUT'])
@require_module('perf')
def api_perf_config_update():
    b      = request.get_json(force=True)
    grades = b.get('grades', [])
    if not grades:
        return jsonify({'error': '請至少設定一個評級'}), 400
    for g in grades:
        if not str(g.get('grade', '')).strip() or not str(g.get('label', '')).strip():
            return jsonify({'error': '評級代碼與標籤不可為空'}), 400
        pct = g.get('min_pct')
        if pct is None or not (0 <= float(pct) <= 100):
            return jsonify({'error': '門檻百分比需介於 0~100'}), 400
    if not any(float(g.get('min_pct', -1)) == 0 for g in grades):
        return jsonify({'error': '必須有一個評級的門檻設為 0%（作為最低等級）'}), 400
    grades_sorted = sorted(
        [{'grade': str(g['grade']).strip(), 'label': str(g['label']).strip(),
          'min_pct': float(g['min_pct'])} for g in grades],
        key=lambda x: -x['min_pct']
    )
    with get_db() as conn:
        conn.execute("""
            INSERT INTO performance_config (key, value, updated_at)
            VALUES ('grade_config', %s, NOW())
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()
        """, (_json.dumps(grades_sorted),))
    return jsonify({'ok': True, 'grades': grades_sorted})


@bp.route('/api/export/performance', methods=['GET'])
@require_module('perf')
def api_export_performance():
    """匯出績效考核 Excel"""
    from blueprints.exports import _xl_workbook, _xl_write_header, _xl_write_rows, _xl_response
    period   = request.args.get('period', '')
    staff_id = request.args.get('staff_id', '')

    conds, params = ['TRUE'], []
    if period:   conds.append("pr.period_label ILIKE %s"); params.append(f'%{period}%')
    if staff_id: conds.append("pr.staff_id=%s"); params.append(int(staff_id))

    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT pr.*,
                   ps.name AS staff_name, ps.employee_code, ps.department, ps.role,
                   pt.name AS template_name
            FROM performance_reviews pr
            JOIN punch_staff ps ON ps.id = pr.staff_id
            LEFT JOIN performance_templates pt ON pt.id = pr.template_id
            WHERE {' AND '.join(conds)}
            ORDER BY pr.reviewed_at DESC
        """, params).fetchall()

    wb, ws = _xl_workbook('績效考核')
    headers = ['員工代碼','姓名','案場','職稱','考核期間','考核表','分數','滿分','百分比','等級','考核人','備註','考核日期']
    widths  = [10, 10, 12, 12, 16, 16, 8, 8, 9, 6, 10, 30, 16]
    _xl_write_header(ws, headers, widths)
    _xl_write_rows(ws, [
        [r['employee_code'] or '', r['staff_name'], r['department'] or '', r['role'] or '',
         r['period_label'] or '', r['template_name'] or '',
         float(r['total_score'] or 0), float(r['max_score'] or 100),
         round(float(r['total_score'] or 0) / float(r['max_score'] or 100) * 100, 1),
         r['grade'] or '',
         r['reviewer'] or '', r['comments'] or '',
         str(r['reviewed_at'])[:16] if r.get('reviewed_at') else '']
        for r in rows
    ], len(headers), number_cols={7, 8, 9})
    return _xl_response(wb, f'performance_{period or "all"}.xlsx')


@bp.route('/api/export/performance/pdf', methods=['GET'])
@require_module('perf')
def api_export_performance_pdf():
    """匯出績效考核 PDF"""
    from blueprints.exports import _build_pdf, _pdf_response
    from datetime import date as _date
    period   = request.args.get('period', '')
    staff_id = request.args.get('staff_id', '')
    conds, params = ['TRUE'], []
    if period:   conds.append("pr.period_label ILIKE %s"); params.append(f'%{period}%')
    if staff_id: conds.append("pr.staff_id=%s"); params.append(int(staff_id))
    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT pr.*, ps.name AS staff_name, ps.employee_code, ps.department, ps.role,
                   pt.name AS template_name
            FROM performance_reviews pr
            JOIN punch_staff ps ON ps.id = pr.staff_id
            LEFT JOIN performance_templates pt ON pt.id = pr.template_id
            WHERE {' AND '.join(conds)} ORDER BY pr.reviewed_at DESC
        """, params).fetchall()
    headers    = ['代碼', '姓名', '案場', '考核期間', '分數', '滿分', '%', '等級', '考核人', '考核日期']
    col_widths = [45, 55, 60, 70, 40, 40, 40, 40, 55, 75]
    data = [[r['employee_code'] or '', r['staff_name'], r['department'] or '',
             r['period_label'] or '',
             str(float(r['total_score'] or 0)), str(float(r['max_score'] or 100)),
             str(round(float(r['total_score'] or 0) / float(r['max_score'] or 100) * 100, 1)),
             r['grade'] or '', r['reviewer'] or '',
             str(r['reviewed_at'])[:10] if r.get('reviewed_at') else '']
            for r in rows]
    buf = _build_pdf('績效考核報表', f'製表：{_dtm.now(TW_TZ).date().isoformat()}  共 {len(data)} 筆',
                     headers, col_widths, data, landscape=True)
    return _pdf_response(buf, f'performance_{period or "all"}.pdf')
