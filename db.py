"""
db.py — 資料庫連線、密碼雜湊、完整建表邏輯
"""
import hashlib
import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from config import DATABASE_URL, ADMIN_PASSWORD


# ── 連線 ──────────────────────────────────────────────────────────

@contextmanager
def get_db():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        yield conn


# ── 密碼雜湊 ──────────────────────────────────────────────────────

def _hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


# ── 完整建表（atomic，不拆分） ────────────────────────────────────

def init_db():
    # --- 主要資料表 ---
    tables = [
        """CREATE TABLE IF NOT EXISTS punch_staff (
            id             SERIAL PRIMARY KEY,
            name           TEXT NOT NULL,
            username       TEXT UNIQUE,
            password_hash  TEXT,
            role           TEXT DEFAULT 'employee',
            department     TEXT DEFAULT '',
            position_title TEXT DEFAULT '',
            employee_code  TEXT DEFAULT '',
            hire_date      DATE,
            birth_date     DATE,
            base_salary    NUMERIC(12,2) DEFAULT 0,
            insured_salary NUMERIC(12,2) DEFAULT 0,
            daily_hours    NUMERIC(5,2) DEFAULT 8,
            salary_type    TEXT DEFAULT 'monthly',
            line_user_id   TEXT,
            active         BOOLEAN DEFAULT TRUE,
            sort_order     INT DEFAULT 0,
            store_id       INT,
            finance_synced BOOLEAN DEFAULT FALSE,
            created_at     TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS punch_records (
            id            SERIAL PRIMARY KEY,
            staff_id      INT REFERENCES punch_staff(id) ON DELETE CASCADE,
            punch_type    TEXT NOT NULL,
            punched_at    TIMESTAMPTZ DEFAULT NOW(),
            note          TEXT DEFAULT '',
            latitude      DOUBLE PRECISION,
            longitude     DOUBLE PRECISION,
            gps_distance  INT,
            location_name TEXT DEFAULT '',
            is_manual     BOOLEAN DEFAULT FALSE
        )""",
        """CREATE TABLE IF NOT EXISTS punch_locations (
            id            SERIAL PRIMARY KEY,
            location_name TEXT NOT NULL,
            lat           DOUBLE PRECISION NOT NULL,
            lng           DOUBLE PRECISION NOT NULL,
            radius_m      INT DEFAULT 100,
            active        BOOLEAN DEFAULT TRUE
        )""",
        """CREATE TABLE IF NOT EXISTS punch_config (
            id           INT PRIMARY KEY DEFAULT 1,
            gps_required BOOLEAN DEFAULT FALSE,
            updated_at   TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS line_punch_config (
            id                   INT PRIMARY KEY DEFAULT 1,
            enabled              BOOLEAN DEFAULT FALSE,
            channel_access_token TEXT DEFAULT '',
            channel_secret       TEXT DEFAULT '',
            updated_at           TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS schedule_config (
            id           SERIAL PRIMARY KEY,
            month        TEXT NOT NULL,
            off_days_per_week INT DEFAULT 2,
            note         TEXT DEFAULT '',
            updated_at   TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(month)
        )""",
        """CREATE TABLE IF NOT EXISTS schedule_requests (
            id           SERIAL PRIMARY KEY,
            staff_id     INT REFERENCES punch_staff(id) ON DELETE CASCADE,
            month        TEXT NOT NULL,
            preferred_off JSONB DEFAULT '[]',
            note         TEXT DEFAULT '',
            status       TEXT DEFAULT 'pending',
            reviewed_by  TEXT DEFAULT '',
            reviewed_at  TIMESTAMPTZ,
            created_at   TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS punch_requests (
            id           SERIAL PRIMARY KEY,
            staff_id     INT REFERENCES punch_staff(id) ON DELETE CASCADE,
            punch_type   TEXT NOT NULL,
            requested_at TIMESTAMPTZ NOT NULL,
            reason       TEXT DEFAULT '',
            status       TEXT DEFAULT 'pending',
            reviewed_by  TEXT DEFAULT '',
            reviewed_at  TIMESTAMPTZ,
            created_at   TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS shift_types (
            id         SERIAL PRIMARY KEY,
            name       TEXT NOT NULL,
            start_time TIME,
            end_time   TIME,
            color      TEXT DEFAULT '#4A90D9',
            active     BOOLEAN DEFAULT TRUE,
            sort_order INT DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS shift_assignments (
            id            SERIAL PRIMARY KEY,
            staff_id      INT REFERENCES punch_staff(id) ON DELETE CASCADE,
            shift_type_id INT REFERENCES shift_types(id) ON DELETE CASCADE,
            shift_date    DATE NOT NULL,
            note          TEXT DEFAULT '',
            created_at    TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(staff_id, shift_date)
        )""",
        """CREATE TABLE IF NOT EXISTS overtime_requests (
            id           SERIAL PRIMARY KEY,
            staff_id     INT REFERENCES punch_staff(id) ON DELETE CASCADE,
            request_date DATE NOT NULL,
            start_time   TEXT DEFAULT '',
            end_time     TEXT DEFAULT '',
            ot_hours     NUMERIC(5,2) DEFAULT 0,
            reason       TEXT DEFAULT '',
            status       TEXT DEFAULT 'pending',
            pay_type     TEXT DEFAULT 'normal',
            ot_pay       NUMERIC(12,2) DEFAULT 0,
            reviewed_by  TEXT DEFAULT '',
            reviewed_at  TIMESTAMPTZ,
            created_at   TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS shift_staffing_requirements (
            id             SERIAL PRIMARY KEY,
            shift_type_id  INT REFERENCES shift_types(id) ON DELETE CASCADE,
            day_of_week    INT NOT NULL,
            min_staff      INT DEFAULT 1,
            UNIQUE(shift_type_id, day_of_week)
        )""",
        """CREATE TABLE IF NOT EXISTS admin_accounts (
            id             SERIAL PRIMARY KEY,
            username       TEXT NOT NULL UNIQUE,
            password_hash  TEXT NOT NULL,
            display_name   TEXT DEFAULT '',
            is_super       BOOLEAN DEFAULT FALSE,
            permissions    JSONB DEFAULT '[]',
            active         BOOLEAN DEFAULT TRUE,
            last_login_at  TIMESTAMPTZ,
            created_at     TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS stores (
            id         SERIAL PRIMARY KEY,
            name       TEXT NOT NULL,
            address    TEXT DEFAULT '',
            phone      TEXT DEFAULT '',
            active     BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
    ]

    for sql in tables:
        try:
            with get_db() as conn:
                conn.execute(sql)
        except Exception as e:
            print(f'[init_db table] {e}')

    # --- Schema migrations (各用獨立連線，避免 transaction abort 汙染) ---
    migrations = [
        # punch_staff
        "ALTER TABLE punch_staff ADD COLUMN IF NOT EXISTS department TEXT DEFAULT ''",
        "ALTER TABLE punch_staff ADD COLUMN IF NOT EXISTS position_title TEXT DEFAULT ''",
        "ALTER TABLE punch_staff ADD COLUMN IF NOT EXISTS employee_code TEXT DEFAULT ''",
        "ALTER TABLE punch_staff ADD COLUMN IF NOT EXISTS hire_date DATE",
        "ALTER TABLE punch_staff ADD COLUMN IF NOT EXISTS birth_date DATE",
        "ALTER TABLE punch_staff ADD COLUMN IF NOT EXISTS base_salary NUMERIC(12,2) DEFAULT 0",
        "ALTER TABLE punch_staff ADD COLUMN IF NOT EXISTS insured_salary NUMERIC(12,2) DEFAULT 0",
        "ALTER TABLE punch_staff ADD COLUMN IF NOT EXISTS daily_hours NUMERIC(5,2) DEFAULT 8",
        "ALTER TABLE punch_staff ADD COLUMN IF NOT EXISTS salary_type TEXT DEFAULT 'monthly'",
        "ALTER TABLE punch_staff ADD COLUMN IF NOT EXISTS line_user_id TEXT",
        "ALTER TABLE punch_staff ADD COLUMN IF NOT EXISTS sort_order INT DEFAULT 0",
        "ALTER TABLE punch_staff ADD COLUMN IF NOT EXISTS store_id INT",
        "ALTER TABLE punch_staff ADD COLUMN IF NOT EXISTS finance_synced BOOLEAN DEFAULT FALSE",
        "ALTER TABLE punch_staff ADD COLUMN IF NOT EXISTS terminated_at DATE",
        "ALTER TABLE punch_staff ADD COLUMN IF NOT EXISTS termination_reason TEXT DEFAULT ''",
        # punch_records
        "ALTER TABLE punch_records ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION",
        "ALTER TABLE punch_records ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION",
        "ALTER TABLE punch_records ADD COLUMN IF NOT EXISTS gps_distance INT",
        "ALTER TABLE punch_records ADD COLUMN IF NOT EXISTS location_name TEXT DEFAULT ''",
        "ALTER TABLE punch_records ADD COLUMN IF NOT EXISTS is_manual BOOLEAN DEFAULT FALSE",
        # overtime_requests
        "ALTER TABLE overtime_requests ADD COLUMN IF NOT EXISTS start_time TEXT DEFAULT ''",
        "ALTER TABLE overtime_requests ADD COLUMN IF NOT EXISTS end_time TEXT DEFAULT ''",
        "ALTER TABLE overtime_requests ADD COLUMN IF NOT EXISTS pay_type TEXT DEFAULT 'normal'",
        "ALTER TABLE overtime_requests ADD COLUMN IF NOT EXISTS ot_pay NUMERIC(12,2) DEFAULT 0",
        # schedule_requests
        "ALTER TABLE schedule_requests ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'pending'",
        "ALTER TABLE schedule_requests ADD COLUMN IF NOT EXISTS reviewed_by TEXT DEFAULT ''",
        "ALTER TABLE schedule_requests ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ",
        # admin_accounts
        "ALTER TABLE admin_accounts ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ",
        # leave_requests
        "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS leave_start_time TEXT",
        "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS leave_end_time TEXT",
        "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS total_days NUMERIC(5,2) DEFAULT 0",
        "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS document_id INT",
        "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS force_reviewed BOOLEAN DEFAULT FALSE",
        # salary_records
        "ALTER TABLE salary_records ADD COLUMN IF NOT EXISTS finance_synced BOOLEAN DEFAULT FALSE",
        # finance_documents
        "ALTER TABLE finance_documents ADD COLUMN IF NOT EXISTS image_data TEXT",
        "ALTER TABLE finance_documents ADD COLUMN IF NOT EXISTS upload_date DATE",
    ]

    for sql in migrations:
        try:
            with get_db() as conn:
                conn.execute(sql)
        except Exception:
            pass

    # --- Seed default admin account ---
    try:
        with get_db() as conn:
            exists = conn.execute(
                "SELECT 1 FROM admin_accounts WHERE username='admin' LIMIT 1"
            ).fetchone()
            if not exists:
                conn.execute(
                    """INSERT INTO admin_accounts (username, password_hash, display_name, is_super)
                       VALUES ('admin', %s, '超級管理員', TRUE)""",
                    (_hash_pw(ADMIN_PASSWORD),)
                )
    except Exception as e:
        print(f'[init_db seed admin] {e}')

    # --- Seed default store ---
    try:
        with get_db() as conn:
            exists = conn.execute("SELECT 1 FROM stores LIMIT 1").fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO stores (name) VALUES ('總店')"
                )
    except Exception as e:
        print(f'[init_db seed store] {e}')

    # --- Seed default punch_config row ---
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO punch_config (id) VALUES (1) ON CONFLICT DO NOTHING"
            )
    except Exception as e:
        print(f'[init_db seed config] {e}')

    # --- Seed default line_punch_config row ---
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO line_punch_config (id) VALUES (1) ON CONFLICT DO NOTHING"
            )
    except Exception as e:
        print(f'[init_db seed line_config] {e}')
