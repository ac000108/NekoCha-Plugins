"""
盲盒统计插件 - 查询盲盒数量与盈亏

查询关键词:
    本月盲盒: 查询本月盲盒统计
    上月盲盒: 查询上月盲盒统计

只能查询当前登录账号自己的盲盒数据。
"""

from datetime import datetime, timedelta
from core.plugin_manager import BasePlugin


class BlindBoxStatsPlugin(BasePlugin):
    """盲盒统计插件"""

    def process_message(self, message: dict):
        if message.get('消息类型') != '弹幕':
            return

        content = message.get('弹幕内容', '').strip()
        uid = str(message.get('用户ID', ''))

        cmd_this = self._config.get('查询指令_本月', '本月盲盒')
        cmd_last = self._config.get('查询指令_上月', '上月盲盒')

        if content == cmd_this:
            self._reply_stats(uid, 'this_month')
        elif content == cmd_last:
            self._reply_stats(uid, 'last_month')

    def _reply_stats(self, uid: str, period: str):
        """查询并回复盲盒统计（查询发送者自己的数据）"""
        # 计算时间范围
        now = datetime.now()
        if period == 'this_month':
            start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_date = now
        else:
            # 上月
            first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            last_month_end = first_of_this_month - timedelta(seconds=1)
            last_month_start = last_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            start_date = last_month_start
            end_date = last_month_end

        start_ts = start_date.timestamp()
        end_ts = end_date.timestamp()

        # 查询盲盒统计（同时获取用户名）
        try:
            rows = self.execute_sql(
                """
                SELECT 
                    MAX(用户名) as 用户名,
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

            if not rows:
                self.send_danmu("暂无盲盒记录")
                return

            row = rows[0]
            username = row['用户名'] or '你'
            count = row['count'] or 0
            total_count = row['total_count'] or 0
            total_profit = row['total_profit'] or 0

            if count == 0:
                self.send_danmu("暂无盲盒记录")
                return

            # 转换为 CNY
            profit_cny = total_profit / 1000

            # 盈亏带正负号
            profit_text = f"{profit_cny:+.2f}"

            reply = f"{username}盲盒{total_count}个（{profit_text}）"

            self.send_danmu(reply)

        except Exception as e:
            print(f"[BlindBoxStats] 查询失败: {e}")
            self.send_danmu("查询盲盒统计失败，请稍后重试")
