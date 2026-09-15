"""
盲盒统计插件 - 查询盲盒数量与盈亏

查询关键词:
    今天盲盒 / 昨天盲盒 / 本周盲盒 / 上周盲盒 / 
    本月盲盒 / 上月盲盒 / 本年盲盒
    8月盲盒 / 盲盒8月  → 查询指定月份

主播查询整个直播间汇总，观众查询自己的数据。
"""

import re
from datetime import datetime, timedelta
from calendar import monthrange
from core.plugin_manager import BasePlugin

# period → 默认关键词映射
_DEFAULT_CMDS = {
    'today':     '今天盲盒',
    'yesterday': '昨天盲盒',
    'this_week': '本周盲盒',
    'last_week': '上周盲盒',
    'this_month': '本月盲盒',
    'last_month': '上月盲盒',
    'this_year':  '本年盲盒',
}


class BlindBoxStatsPlugin(BasePlugin):
    """盲盒统计插件"""

    def process_message(self, message: dict):
        if message.get('消息类型') != '弹幕':
            return

        content = message.get('弹幕内容', '').strip()
        uid = str(message.get('用户ID', ''))
        is_anchor = self.is_anchor_danmu(message)
        username = message.get('用户名', '')

        # 1. 匹配固定关键词
        for period, default_cmd in _DEFAULT_CMDS.items():
            config_key = f'查询指令_{period}'
            if content == self._config.get(config_key, default_cmd):
                self._reply_stats(uid, period, is_anchor, username)
                return

        # 2. 匹配"某月盲盒"/"盲盒某月"：含"盲盒"且有 \d{1,2}月
        if '盲盒' in content:
            m = re.search(r'(\d{1,2})\s*月', content)
            if m:
                month = int(m.group(1))
                if 1 <= month <= 12:
                    now = datetime.now()
                    year = now.year
                    # 如果是未来月份，用去年
                    if month > now.month:
                        year -= 1
                    self._reply_stats(uid, 'a_month', is_anchor, username,
                                      year=year, month=month)

    def _calc_range(self, period: str, year: int = None, month: int = None):
        """根据 period 计算 (start_ts, end_ts)"""
        now = datetime.now()

        if period == 'today':
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now
        elif period == 'yesterday':
            start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1) - timedelta(seconds=1)
        elif period == 'this_week':
            # Python weekday(): 周一=0, 周日=6
            monday = now - timedelta(days=now.weekday())
            start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now
        elif period == 'last_week':
            this_monday = now - timedelta(days=now.weekday())
            last_monday = this_monday - timedelta(days=7)
            last_sunday = this_monday - timedelta(seconds=1)
            start = last_monday.replace(hour=0, minute=0, second=0, microsecond=0)
            end = last_sunday
        elif period == 'this_month':
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = now
        elif period == 'last_month':
            first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            last_end = first_this - timedelta(seconds=1)
            start = last_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = last_end
        elif period == 'this_year':
            start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            end = now
        elif period == 'a_month':
            # 指定月份
            y = year or now.year
            m = month or now.month
            start = datetime(y, m, 1, 0, 0, 0)
            days_in_month = monthrange(y, m)[1]
            end = datetime(y, m, days_in_month, 23, 59, 59)
        else:
            return None, None

        return start.timestamp(), end.timestamp()

    def _reply_stats(self, uid: str, period: str, is_anchor: bool,
                     username: str = '', year: int = None, month: int = None):
        """查询并回复盲盒统计"""
        start_ts, end_ts = self._calc_range(period, year=year, month=month)
        if start_ts is None:
            return

        try:
            if is_anchor:
                rows = self.execute_sql(
                    """
                    SELECT 
                        COUNT(*) as count,
                        SUM(礼物数量) as total_count,
                        SUM(盲盒盈亏) as total_profit
                    FROM gift 
                    WHERE 是否盲盒 = 1 
                        AND 时间戳 >= ? 
                        AND 时间戳 <= ?
                    """,
                    (start_ts, end_ts)
                )
            else:
                rows = self.execute_sql(
                    """
                    SELECT 
                        COUNT(*) as count,
                        SUM(礼物数量) as total_count,
                        SUM(盲盒盈亏) as total_profit
                    FROM gift 
                    WHERE 用户ID = ? 
                        AND 是否盲盒 = 1 
                        AND 时间戳 >= ? 
                        AND 时间戳 <= ?
                    """,
                    (uid, start_ts, end_ts)
                )

            if not rows or rows[0]['count'] == 0:
                self.send_danmu("暂无盲盒记录")
                return

            row = rows[0]
            total_count = row['total_count'] or 0
            total_profit = row['total_profit'] or 0

            profit_cny = total_profit / 1000
            profit_text = f"{profit_cny:+.2f}"

            prefix = "直播间" if is_anchor else (username or '你')
            reply = f"{prefix}盲盒{total_count}个（{profit_text}）"
            self.send_danmu(reply)

        except Exception as e:
            print(f"[BlindBoxStats] 查询失败: {e}")
            self.send_danmu("查询盲盒统计失败，请稍后重试")
