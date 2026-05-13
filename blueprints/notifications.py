"""
blueprints/notifications.py — LINE 通知輔助函式（跨模組共用）
"""
from db import get_db


def _notify_staff_line(staff_id, message):
    """向指定員工的 LINE 推播一則訊息"""
    try:
        from linebot import LineBotApi
        from linebot.models import TextSendMessage
        with get_db() as conn:
            staff = conn.execute(
                "SELECT line_user_id FROM punch_staff WHERE id=%s", (staff_id,)
            ).fetchone()
            if not staff or not staff['line_user_id']:
                return
            cfg = conn.execute(
                "SELECT channel_access_token, enabled FROM line_punch_config WHERE id=1"
            ).fetchone()
            if not cfg or not cfg['enabled'] or not cfg['channel_access_token']:
                return
        api = LineBotApi(cfg['channel_access_token'])
        api.push_message(staff['line_user_id'], TextSendMessage(text=message))
    except Exception as e:
        print(f'[notify_staff_line] staff={staff_id}: {e}')


def _notify_review_result(staff_id, category, action, extra_info=''):
    """
    category: '打卡補登' | '加班' | '請假' | '排班' | '費用報帳'
    action:   'approve' | 'reject'
    """
    action_zh = '已核准' if action == 'approve' else '已駁回'
    msg = f'[{category}] 申請{action_zh}'
    if extra_info:
        msg += f'\n{extra_info}'
    _notify_staff_line(staff_id, msg)
