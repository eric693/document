"""
startup.py — 背景執行緒：keep_alive、年假同步
"""
import threading
import time
import urllib.request
from datetime import datetime

from config import RENDER_EXTERNAL_URL, TW_TZ


def keep_alive():
    """每 14 分鐘 ping 自身，避免 Render free tier 休眠"""
    url = RENDER_EXTERNAL_URL + '/health' if RENDER_EXTERNAL_URL else None
    if not url:
        return
    while True:
        try:
            urllib.request.urlopen(url, timeout=10)
        except Exception:
            pass
        time.sleep(14 * 60)


def _run_annual_leave_sync():
    """觸發年假同步（呼叫 leave blueprint 的函式）"""
    try:
        from blueprints.leave import _calc_annual_leave_days
        from db import get_db
        with get_db() as conn:
            staff_rows = conn.execute(
                "SELECT id, hire_date FROM punch_staff WHERE active=TRUE AND hire_date IS NOT NULL"
            ).fetchall()
            today_str = datetime.now(TW_TZ).strftime('%Y-%m-%d')
            for s in staff_rows:
                try:
                    days = _calc_annual_leave_days(str(s['hire_date']), today_str)
                    year = int(today_str[:4])
                    # 取得年假 leave_type id
                    lt = conn.execute(
                        "SELECT id FROM leave_types WHERE name='特休假' AND active=TRUE LIMIT 1"
                    ).fetchone()
                    if not lt:
                        continue
                    # Upsert leave balance
                    existing = conn.execute(
                        "SELECT id, total_days FROM leave_balances WHERE staff_id=%s AND leave_type_id=%s AND year=%s",
                        (s['id'], lt['id'], year)
                    ).fetchone()
                    if existing:
                        if float(existing['total_days'] or 0) == 0:
                            conn.execute(
                                "UPDATE leave_balances SET total_days=%s WHERE id=%s",
                                (days, existing['id'])
                            )
                    else:
                        conn.execute(
                            """INSERT INTO leave_balances (staff_id, leave_type_id, year, total_days, used_days)
                               VALUES (%s,%s,%s,%s,0) ON CONFLICT DO NOTHING""",
                            (s['id'], lt['id'], year, days)
                        )
                except Exception as e:
                    print(f'[annual_leave_sync staff {s["id"]}] {e}')
    except Exception as e:
        print(f'[annual_leave_sync] {e}')


def _annual_leave_sync_loop():
    """每天 00:05 台灣時間觸發年假同步"""
    while True:
        now = datetime.now(TW_TZ)
        # 計算距離下一個 00:05 的秒數
        next_run = now.replace(hour=0, minute=5, second=0, microsecond=0)
        if now >= next_run:
            from datetime import timedelta
            next_run = next_run + timedelta(days=1)
        sleep_sec = (next_run - now).total_seconds()
        time.sleep(sleep_sec)
        _run_annual_leave_sync()


def start_background_threads():
    """啟動所有背景執行緒"""
    t1 = threading.Thread(target=keep_alive, daemon=True)
    t1.start()

    t2 = threading.Thread(target=_annual_leave_sync_loop, daemon=True)
    t2.start()
