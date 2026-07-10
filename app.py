"""
app.py — Flask 應用入口（精簡版）
原始 12168 行已拆分為 blueprints/ 各模組。
"""
import os
import secrets

from dotenv import load_dotenv
load_dotenv()

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from config import DATABASE_URL
from db import init_db

# ── Flask App ──────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config.update(
    SESSION_COOKIE_SECURE=True,      # 只透過 HTTPS 傳送（nginx 反代 TLS）
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',   # 阻擋跨站 POST 帶 cookie（CSRF 緩解）
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

print(f"[startup] DATABASE_URL prefix: {DATABASE_URL[:20] if DATABASE_URL else 'NOT SET'}")

# ── 初始化資料庫（主表 + migration） ──────────────────────────────
init_db()

# ── 各模組補充 table 初始化 ────────────────────────────────────────
from blueprints.leave        import init_leave_db
from blueprints.salary       import init_salary_db
from blueprints.announcements import init_announcement_db
from blueprints.finance      import init_finance_db, init_finance_settings_db, init_insurance_db
from blueprints.training     import init_training_db
from blueprints.holidays     import init_holiday_db
from blueprints.expense      import init_expense_db
from blueprints.performance  import _init_performance_db
from blueprints.webauthn     import init_webauthn_db
from blueprints.dashboard    import init_labor_law_db, start_labor_law_monitor
from blueprints.documents    import init_documents_db

init_leave_db()
init_salary_db()
init_announcement_db()
init_finance_db()
init_finance_settings_db()
init_insurance_db()
init_training_db()
init_holiday_db()
init_expense_db()
_init_performance_db()
init_webauthn_db()
init_labor_law_db()
init_documents_db()

# ── 注冊 Blueprints ────────────────────────────────────────────────
from blueprints.admin         import bp as admin_bp
from blueprints.punch         import bp as punch_bp
from blueprints.schedule      import bp as schedule_bp
from blueprints.shifts        import bp as shifts_bp
from blueprints.overtime      import bp as overtime_bp
from blueprints.leave         import bp as leave_bp
from blueprints.salary        import bp as salary_bp
from blueprints.announcements import bp as announcements_bp
from blueprints.line_bot      import bp as line_bot_bp
from blueprints.finance       import bp as finance_bp
from blueprints.training      import bp as training_bp
from blueprints.performance   import bp as performance_bp
from blueprints.expense       import bp as expense_bp
from blueprints.holidays      import bp as holidays_bp
from blueprints.mobile        import bp as mobile_bp
from blueprints.webauthn      import bp as webauthn_bp
from blueprints.dashboard     import bp as dashboard_bp
from blueprints.exports       import bp as exports_bp
from blueprints.documents     import bp as documents_bp

app.register_blueprint(admin_bp)
app.register_blueprint(punch_bp)
app.register_blueprint(schedule_bp)
app.register_blueprint(shifts_bp)
app.register_blueprint(overtime_bp)
app.register_blueprint(leave_bp)
app.register_blueprint(salary_bp)
app.register_blueprint(announcements_bp)
app.register_blueprint(line_bot_bp)
app.register_blueprint(finance_bp)
app.register_blueprint(training_bp)
app.register_blueprint(performance_bp)
app.register_blueprint(expense_bp)
app.register_blueprint(holidays_bp)
app.register_blueprint(mobile_bp)
app.register_blueprint(webauthn_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(exports_bp)
app.register_blueprint(documents_bp)

# ── Health check ───────────────────────────────────────────────────
from flask import jsonify
from db import get_db

@app.route('/health')
def health():
    try:
        with get_db() as conn:
            conn.execute('SELECT 1')
        return jsonify({'status': 'ok', 'db': 'connected'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'detail': str(e)}), 500

# ── 背景執行緒（keep-alive + 年假同步） ────────────────────────────
from startup import start_background_threads
start_background_threads()
start_labor_law_monitor()

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
