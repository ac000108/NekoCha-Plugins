"""
贴贴统计插件 - 从数据库实时统计当天每个用户发送包含关键词弹幕的次数
支持弹幕查询个人次数和排行榜（查询指令本身不计入统计）
数据直接从 danmu 表读，无需内存存储，天然支持跨场次累计
"""

from datetime import datetime

from core.plugin_manager import BasePlugin


class TieTieStatsPlugin(BasePlugin):
    """贴贴统计插件（DB 实时查询版，无需 per-room 字典或内存缓存）"""

    def process_message(self, message: dict):
        msg_type = message.get('消息类型')

        # 只拦截查询指令，其他消息直接放过（统计交给 DB）
        if msg_type != '弹幕':
            return

        if self.is_self_danmu(message):
            return

        content = message.get('弹幕内容', '') or ''
        uid = message.get('用户ID')
        if not uid:
            return
        uid = str(uid)
        uname = message.get('用户名', '')

        query_cmd = self._config.get('查询指令', '查询贴贴次数')
        if query_cmd and content.strip() == query_cmd:
            self._reply_user_count(uid, uname)
            return

        rank_cmd = self._config.get('查询排行指令', '查排行')
        if rank_cmd and content.strip() == rank_cmd:
            self._reply_ranking()
            return

    # ---------- DB 查询 ----------

    def _get_today_start_ts(self) -> float:
        """今天 00:00:00 的 Unix 时间戳"""
        return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def _build_exclude_clause(self) -> tuple:
        """生成排除查询指令的 SQL 片段和参数（精确匹配，避免误排除含指令的混合弹幕）"""
        clauses = []
        params = []
        for cmd_key in ('查询指令', '查询排行指令'):
            cmd = self._config.get(cmd_key, '')
            if cmd:
                clauses.append('"弹幕内容" != ?')
                params.append(cmd)
        exclude_sql = ' AND '.join(clauses)
        return (f'AND {exclude_sql}' if exclude_sql else '', tuple(params))

    def _query_user_count(self, uid: str) -> int:
        keyword = self._config.get('关键词', '贴贴')
        exclude_sql, exclude_params = self._build_exclude_clause()
        sql = f'SELECT COUNT(*) as cnt FROM danmu WHERE "用户ID" = ? AND "弹幕内容" LIKE ? AND "时间戳" >= ? {exclude_sql}'
        rows = self.execute_sql(sql, (int(uid), f'%{keyword}%', self._get_today_start_ts()) + exclude_params, max_rows=1)
        return rows[0]['cnt'] if rows else 0

    def _query_ranking(self, top_n: int) -> list:
        """返回 [{用户ID, 用户名, cnt}, ...]"""
        keyword = self._config.get('关键词', '贴贴')
        exclude_sql, exclude_params = self._build_exclude_clause()
        sql = f'''
            SELECT "用户ID", "用户名", COUNT(*) as cnt
            FROM danmu
            WHERE "弹幕内容" LIKE ? AND "时间戳" >= ? {exclude_sql}
            GROUP BY "用户ID"
            ORDER BY cnt DESC
            LIMIT ?
        '''
        return self.execute_sql(sql, (f'%{keyword}%', self._get_today_start_ts()) + exclude_params + (top_n,))

    # ---------- 弹幕回复 ----------

    def _reply_user_count(self, uid: str, uname: str):
        count = self._query_user_count(uid)
        keyword = self._config.get('关键词', '贴贴')
        safe_keyword = keyword.replace('贴', '貼') if keyword == '贴贴' else keyword
        display_name = uname or uid
        msg = f"@{display_name} 今日「{safe_keyword}」次数: {count}"
        self.send_danmu(msg)

    def _truncate_name(self, name: str) -> str:
        limit = int(self._config.get('弹幕排行名字长度', 2) or 2)
        if limit > 0 and len(name) > limit:
            return name[:limit] + '…'
        return name

    def _reply_ranking(self):
        keyword = self._config.get('关键词', '贴贴')
        safe_keyword = keyword.replace('贴', '貼') if keyword == '贴贴' else keyword
        top_n = int(self._config.get('弹幕排行TopN', 5) or 5)

        rows = self._query_ranking(top_n)
        if not rows:
            self.send_danmu(f"今日暂无「{safe_keyword}」记录")
            return

        max_len = int(self._config.get('弹幕最大长度', 40) or 40)
        sep = self._config.get('弹幕排行分隔符', '')

        parts = []
        for row in rows:
            name = self._truncate_name(row.get('用户名') or '')
            cnt = row.get('cnt', 0)
            piece = f"{name}({cnt})"
            candidate = sep.join(parts + [piece])
            if len(candidate) > max_len and parts:
                break
            parts.append(piece)

        msg = sep.join(parts)
        if len(msg) > max_len:
            msg = msg[:max_len - 1] + '…'
        self.send_danmu(msg)
