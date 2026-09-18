"""
点歌插件 v2 - 详细权限控制版

两种点歌方式：
1. 弹幕点歌：观众直接发弹幕触发，受冷却时间和每日上限限制
2. 盲盒点歌：购买盲盒后 1 分钟内发弹幕确认

所有限制主播自身豁免。
"""

import random
import time
import threading
from core.plugin_manager import BasePlugin

# 舰长等级常量
GUARD_LEVELS = {'舰长', '提督', '总督'}


class SongRequestPlugin(BasePlugin):

    def __init__(self, name, plugin_path):
        super().__init__(name, plugin_path)
        self._lock = threading.Lock()
        self._last_request = {}   # {user_id: timestamp} 冷却
        self._daily_count = {}    # {user_id: count} 每日计数
        self._daily_date = {}     # {user_id: date_int} 日期标记
        self._pending_blind = {}  # {user_id: {'expire_ts': int, 'variables': dict}} 盲盒待确认

    # ==================== 配置解析 ====================

    def _get_song_list(self) -> list:
        raw = self._config.get('歌曲列表', '')
        if not raw or not isinstance(raw, str):
            return []
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _is_guard(self, message: dict) -> bool:
        return message.get('舰长等级', '') in GUARD_LEVELS

    def _today_int(self) -> int:
        """今天的 YYYYMMDD 整数，用于每日计数重置"""
        import datetime
        n = datetime.datetime.now()
        return n.year * 10000 + n.month * 100 + n.day

    # ==================== 权限检查 ====================

    def _check_danmu_limit(self, user_id, is_guard) -> tuple[bool, str]:
        """
        检查弹幕点歌权限（冷却 + 每日上限）
        返回 (通过, 拒绝原因)
        """
        with self._lock:
            # 每日计数重置
            today = self._today_int()
            if self._daily_date.get(user_id) != today:
                self._daily_count[user_id] = 0
                self._daily_date[user_id] = today

            # 每日上限（主播豁免已经在调用方做了）
            if is_guard:
                limit = self._config.get('舰长每日上限', 10)
            else:
                limit = self._config.get('普通用户每日上限', 3)
            if self._daily_count.get(user_id, 0) >= limit:
                return False, f'今日点歌已用完（上限 {limit} 次）'

            # 冷却时间
            cooldown = self._config.get('弹幕冷却秒数', 30)
            last = self._last_request.get(user_id, 0)
            now = time.time()
            if now - last < cooldown:
                remain = int(cooldown - (now - last))
                return False, f'冷却中（还需 {remain} 秒）'

            return True, ''

    def _consume_user_quota(self, user_id):
        """消耗一次点歌额度"""
        with self._lock:
            today = self._today_int()
            if self._daily_date.get(user_id) != today:
                self._daily_count[user_id] = 0
                self._daily_date[user_id] = today
            self._daily_count[user_id] = self._daily_count.get(user_id, 0) + 1
            self._last_request[user_id] = time.time()

    # ==================== 歌曲解析 ====================

    def _resolve_song(self, content: str) -> str | None:
        """
        从弹幕内容解析出歌曲名。
        - 内容匹配触发词 → 返回 None（表示随机）
        - 内容匹配歌单内歌曲 → 返回那首歌
        - 内容不匹配歌单 + 只支持歌单内 = True → 返回 '' （拒绝）
        - 内容不匹配歌单 + 只支持歌单内 = False → 返回内容本身
        """
        content = content.strip()
        trigger_word = self._config.get('弹幕触发词', '随机点歌')

        # 触发词 → 随机
        if trigger_word and trigger_word in content:
            return None

        song_list = self._get_song_list()
        only_in_list = self._config.get('只支持歌单内歌曲', True)

        # 精确匹配歌单
        if content in song_list:
            return content

        # 歌单内匹配不到
        if only_in_list:
            return ''  # 拒绝
        return content  # 允许任意歌名

    def _pick_song(self, song_name: str | None, allow_random: bool = True) -> str | None:
        """
        最终确定歌曲：
        - song_name is None → 从歌单随机选
        - song_name is '' → 拒绝（不在歌单）
        - song_name is str → 直接用
        """
        if song_name is None:
            if not allow_random:
                return None
            song_list = self._get_song_list()
            return random.choice(song_list) if song_list else None
        if song_name == '':
            return None
        return song_name

    # ==================== 核心：执行点歌 ====================

    def _do_request(self, song: str, variables: dict):
        template = self._config.get('回复模板', '{用户名} 点歌｜{歌曲名}')
        reply = template.replace('{歌曲名}', song)
        for key, value in variables.items():
            reply = reply.replace(f'{{{key}}}', str(value))

        result = self.send_danmu(reply)
        if result.get('success'):
            print(f"[点歌插件] 发送成功: {reply}")
        else:
            print(f"[点歌插件] 发送失败: {reply}, 错误: {result.get('message')}")

    # ==================== 入口 ====================

    def process_message(self, message: dict):
        msg_type = message.get('消息类型')
        if msg_type == '礼物':
            self._handle_gift(message)
        elif msg_type == '弹幕':
            self._handle_danmu(message)

    # ==================== 礼物处理（盲盒点歌资格）====================

    def _handle_gift(self, message: dict):
        if self.is_self_danmu(message):
            return

        if not self._config.get('盲盒点歌开关', False):
            return

        # 必须是盲盒
        if not message.get('是否盲盒'):
            return

        # 盲盒单价 = 总价值 / 礼物数量
        total_coin = message.get('总价值', 0)
        gift_count = message.get('礼物数量', 1)
        if gift_count <= 0:
            return
        blind_price = total_coin // gift_count

        threshold = self._config.get('盲盒最低价格', 1000)
        if blind_price < threshold:
            print(f"[点歌插件] 盲盒单价 {blind_price} < {threshold}，跳过")
            return

        # 记录待确认
        user_id = message.get('用户ID')
        confirm_sec = self._config.get('盲盒确认时长秒', 60)
        variables = {
            '用户名': message.get('用户名', '观众'),
            '礼物名称': message.get('礼物名称', ''),
            '盲盒名称': message.get('盲盒名称', ''),
            '舰长等级': message.get('舰长等级', ''),
        }
        with self._lock:
            self._pending_blind[user_id] = {
                'expire_ts': time.time() + confirm_sec,
                'variables': variables,
            }

        print(f"[点歌插件] {variables['用户名']} 盲盒触发，{confirm_sec}秒内发弹幕确认点歌")

    # ==================== 弹幕处理 ====================

    def _handle_danmu(self, message: dict):
        if self.is_self_danmu(message):
            return

        user_id = message.get('用户ID')
        user_name = message.get('用户名', '观众')
        content = message.get('弹幕内容', '').strip()
        is_anchor = self.is_anchor_danmu(message)
        is_guard = self._is_guard(message)

        # 构造默认变量（后续填充）
        base_variables = {
            '用户名': user_name,
            '礼物名称': '',
            '盲盒名称': '',
            '舰长等级': message.get('舰长等级', ''),
        }

        # --- 路径 1：盲盒待确认 ---
        pending = None
        with self._lock:
            pending = self._pending_blind.pop(user_id, None)
        if pending:
            if time.time() < pending['expire_ts']:
                variables = {**base_variables, **pending['variables']}
                song_name = self._resolve_song(content)
                song = self._pick_song(song_name)
                if song:
                    self._do_request(song, variables)
                    print(f"[点歌插件] [盲盒确认] {user_name} -> {song}")
                else:
                    print(f"[点歌插件] [盲盒确认] {user_name} 拒绝（歌名不在歌单内）")
                return
            else:
                print(f"[点歌插件] [盲盒确认] {user_name} 超时")

        # --- 路径 2：普通弹幕点歌 ---
        if not self._config.get('弹幕点歌开关', False):
            return

        # 主播豁免所有限制
        if not is_anchor:
            ok, reason = self._check_danmu_limit(user_id, is_guard)
            if not ok:
                print(f"[点歌插件] {user_name} 被拒: {reason}")
                return

        song_name = self._resolve_song(content)
        song = self._pick_song(song_name)
        if not song:
            print(f"[点歌插件] {user_name} 拒绝（歌名不在歌单内）")
            return

        # 非主播才扣额度
        if not is_anchor:
            self._consume_user_quota(user_id)

        self._do_request(song, base_variables)
        print(f"[点歌插件] [弹幕点歌] {user_name} -> {song}")
