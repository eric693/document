"""
config.py — 全域常數與環境變數
"""
import os
from datetime import timezone, timedelta

# ── 時區 ──────────────────────────────────────────────────────────
TW_TZ = timezone(timedelta(hours=8))

# ── 中文星期 ──────────────────────────────────────────────────────
WEEKDAY_ZH = ['一', '二', '三', '四', '五', '六', '日']

# ── LINE Bot ──────────────────────────────────────────────────────
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
LINE_CHANNEL_SECRET       = os.environ.get('LINE_CHANNEL_SECRET', '')

# ── 資料庫 ────────────────────────────────────────────────────────
_raw_db_url  = os.environ.get('DATABASE_URL', '')
DATABASE_URL = _raw_db_url.replace('postgres://', 'postgresql://', 1) if _raw_db_url.startswith('postgres://') else _raw_db_url

# ── 管理員預設密碼 ────────────────────────────────────────────────
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')

# ── Anthropic OCR ─────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

# ── WebAuthn ──────────────────────────────────────────────────────
WEBAUTHN_RP_ID   = os.environ.get('WEBAUTHN_RP_ID',   'document.crownai.ink')
WEBAUTHN_RP_NAME = '打卡系統'
WEBAUTHN_ORIGIN  = os.environ.get('WEBAUTHN_ORIGIN',  'https://document.crownai.ink')

# ── Mobile JWT ────────────────────────────────────────────────────
MOBILE_JWT_SECRET  = os.environ.get('MOBILE_JWT_SECRET', '')  # fallback = app.secret_key
JWT_EXPIRE_HOURS   = 24 * 7   # 7 days
