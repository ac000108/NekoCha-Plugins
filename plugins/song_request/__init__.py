"""
点歌姬 v3 - 三层权限模型

点歌指令（两种）：
  随机点歌      → 随机选歌
  点歌 <歌名>    → 指定歌曲

三层配置：
  基础设置（所有路径都遵守）：歌单范围 + 冷却时间
  免费次数：粉丝牌/舰长每日额度
  盲盒点歌（花钱续杯，不扣免费次数）

主播豁免所有限制。
"""

import random
import time
import datetime
import threading
from core.plugin_manager import BasePlugin

GUARD_LEVELS = {'舰长', '提督', '总督'}


class SongRequestPlugin(BasePlugin):

    def __init__(self, name, plugin_path):
        super().__init__(name, plugin_path)
        self._lock = threading.Lock()
        self._last_request = {}   # {user_id: timestamp} 冷却
        self._daily_count = {}    # {user_id: count} 免费次数已用
        self._daily_date = {}     # {user_id: date_int} 日期标记
        self._pending_blind = {}  # {user_id: {'expire_ts': int}}
        # 从 state.json 恢复队列（框架重启不丢）
        self._queue = self.get_state('queue', []) or []

    def _append_queue(self, song: str, variables: dict, trigger: str):
        """点歌成功后追加到队列并持久化"""
        with self._lock:
            max_id = max((q.get('id', 0) for q in self._queue), default=0)
            item = {
                'id': max_id + 1,
                'song': song,
                'user': variables.get('用户名', '观众'),
                'user_id': variables.get('用户ID', 0),
                'trigger': trigger,
                'ts': int(time.time()),
            }
            self._queue.append(item)
            # 只保留最近 50 条
            if len(self._queue) > 50:
                self._queue = self._queue[-50:]
            self.set_state('queue', self._queue)

    # ==================== 工具方法 ====================

    def _get_song_list(self) -> list:
        raw = self._config.get('歌曲列表', '')
        if not raw or not isinstance(raw, str):
            return []
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _is_guard(self, message: dict) -> bool:
        return message.get('舰长等级', '') in GUARD_LEVELS

    def _today_int(self) -> int:
        n = datetime.datetime.now()
        return n.year * 10000 + n.month * 100 + n.day

    # ==================== 指令解析 ====================

    def _parse_command(self, content: str) -> str | None:
        """
        解析弹幕是否为点歌指令。
        返回:
          None        → 不是点歌指令，忽略
          'RANDOM'    → 随机点歌
          <歌名 str>  → 指定歌曲
        """
        content = content.strip()

        # 完整匹配 "随机点歌"
        if content == '随机点歌':
            return 'RANDOM'

        # "点歌 <歌名>" 前缀式
        if content.startswith('点歌 '):
            song = content[3:].strip()
            if song:
                return song

        return None

    # ==================== 权限检查 ====================

    def _check_cooldown(self, user_id) -> tuple[bool, str]:
        """基础冷却（所有路径都遵守）"""
        cooldown = self._config.get('点歌冷却秒数', 30)
        if cooldown <= 0:
            return True, ''
        last = self._last_request.get(user_id, 0)
        remain = cooldown - (time.time() - last)
        if remain > 0:
            return False, f'冷却中（还需 {int(remain)} 秒）'
        return True, ''

    def _check_and_consume_free(self, user_id, is_guard) -> bool:
        """
        检查并消耗一次免费次数。
        返回 True=扣到了次数，False=免费次数用完。
        """
        with self._lock:
            today = self._today_int()
            if self._daily_date.get(user_id) != today:
                self._daily_count[user_id] = 0
                self._daily_date[user_id] = today

            limit = self._config.get(
                '舰长每日免费次数' if is_guard else '粉丝牌每日免费次数',
                10 if is_guard else 3
            )
            if self._daily_count.get(user_id, 0) >= limit:
                return False

            self._daily_count[user_id] += 1
            self._last_request[user_id] = time.time()
            return True

    # ==================== 歌曲解析 ====================

    def _resolve_song(self, command_result, variables: dict) -> str | None:
        """
        command_result: _parse_command 的返回值
          'RANDOM'     → 随机选
          <歌名 str>   → 指定歌曲
        """
        song_list = self._get_song_list()
        only_in_list = self._config.get('只支持歌单内歌曲', True)

        # 随机
        if command_result == 'RANDOM':
            return random.choice(song_list) if song_list else None

        # 指定
        song = command_result
        if only_in_list and song not in song_list:
            return None
        return song

    # ==================== 核心：执行点歌 ====================

    def _do_request(self, song: str, variables: dict):
        reply = f"{variables.get('用户名', '观众')} 点歌 {song}"
        result = self.send_danmu(reply)
        tag = '[点歌姬]'
        if result.get('success'):
            print(f"{tag} 发送成功: {reply}")
        else:
            print(f"{tag} 发送失败: {reply}, 错误: {result.get('message')}")

    # ==================== 礼物处理（盲盒触发）====================

    def _handle_gift(self, message: dict):
        if self.is_self_danmu(message):
            return
        if not self._config.get('盲盒点歌开关', False):
            return
        if not message.get('是否盲盒'):
            return

        total_coin = message.get('总价值', 0)
        gift_count = message.get('礼物数量', 1)
        if gift_count <= 0:
            return
        blind_price = total_coin // gift_count

        threshold = self._config.get('盲盒最低价格', 1000)
        if blind_price < threshold:
            return

        user_id = message.get('用户ID')
        confirm_sec = self._config.get('盲盒确认时长秒', 120)

        with self._lock:
            self._pending_blind[user_id] = {
                'expire_ts': time.time() + confirm_sec,
                '用户名': message.get('用户名', '观众'),
            }
        print(f"[点歌姬] {message.get('用户名')} 盲盒触发，{confirm_sec}秒内发弹幕确认点歌")

    # ==================== 弹幕处理 ====================

    def _handle_danmu(self, message: dict):
        if self.is_self_danmu(message):
            return

        user_id = message.get('用户ID')
        user_name = message.get('用户名', '观众')
        content = message.get('弹幕内容', '').strip()
        is_anchor = self.is_anchor_danmu(message)
        is_guard = self._is_guard(message)

        variables = {
            '用户名': user_name,
            '用户ID': user_id,
            '舰长等级': message.get('舰长等级', ''),
        }

        # 解析指令
        command = self._parse_command(content)
        if command is None:
            return  # 不是点歌指令，忽略

        # --- 主播：跳过所有限制 ---
        if is_anchor:
            song = self._resolve_song(command, variables)
            if song:
                self._do_request(song, variables)
                self._append_queue(song, variables, '主播')
                print(f"[点歌姬] [主播] {user_name} -> {song}")
            return

        # --- 路径 1：有盲盒待确认 ---
        pending = None
        with self._lock:
            pending = self._pending_blind.pop(user_id, None)
        if pending:
            if time.time() < pending['expire_ts']:
                song = self._resolve_song(command, variables)
                if song:
                    self._do_request(song, variables)
                    self._append_queue(song, variables, '盲盒')
                    print(f"[点歌姬] [盲盒] {user_name} -> {song}")
                else:
                    print(f"[点歌姬] [盲盒] {user_name} 拒绝（歌名不在歌单内）")
                return
            else:
                print(f"[点歌姬] [盲盒] {user_name} 超时")

        # --- 路径 2：免费次数 ---
        # 先看免费次数
        ok = self._check_and_consume_free(user_id, is_guard)
        if not ok:
            print(f"[点歌姬] {user_name} 免费次数用完，盲盒也没开")
            return

        # 冷却（已在 consume 里记录时间戳，这里再确认没秒级并发问题）
        ok, reason = self._check_cooldown(user_id)
        if not ok:
            # 理论上刚 consume 过应该刚好冷却边界，除非配置改成 0 秒
            print(f"[点歌姬] {user_name} 被拒: {reason}")
            return

        song = self._resolve_song(command, variables)
        if song:
            self._do_request(song, variables)
            self._append_queue(song, variables, '免费')
            print(f"[点歌姬] [免费] {user_name} -> {song}")
        else:
            print(f"[点歌姬] {user_name} 拒绝（歌名不在歌单内）")

    # ==================== 消息入口 ====================

    def process_message(self, message: dict):
        msg_type = message.get('消息类型')
        if msg_type == '礼物':
            self._handle_gift(message)
        elif msg_type == '弹幕':
            self._handle_danmu(message)
