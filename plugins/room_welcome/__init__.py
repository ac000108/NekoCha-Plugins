"""弹幕欢迎插件 - 观众进入直播间时自动发欢迎弹幕"""

import random
import threading
import time
from core.plugin_manager import BasePlugin


class RoomWelcomePlugin(BasePlugin):
    """弹幕欢迎插件"""

    def __init__(self, name: str, plugin_path: str):
        super().__init__(name, plugin_path)
        self._welcome_log = {}  # {uid: timestamp}
        self._lock = threading.Lock()
        self._in_lottery = False  # 天选时刻进行中

    def process_message(self, message: dict):
        msg_type = message.get('消息类型')

        # 天选状态维护
        if msg_type == '天选':
            stage = message.get('阶段')
            if stage == '开始':
                self._in_lottery = True
            elif stage == '结束':
                self._in_lottery = False
            return

        if msg_type != '互动':
            return
        if message.get('动作') != '进入直播间':
            return
        if self.is_self_danmu(message):
            return

        # 天选时跳过
        if self._in_lottery and self._config.get('天选时禁用欢迎', True):
            return

        uid = str(message.get('用户ID', ''))
        cooldown = self._config.get('同一用户冷却秒', 60)
        now = time.time()

        with self._lock:
            if now - self._welcome_log.get(uid, 0) < cooldown:
                return
            self._welcome_log[uid] = now

        templates = self._config.get('欢迎模板列表', [])
        if not templates:
            return

        username = message.get('用户名', '观众')
        reply = random.choice(templates).replace('{username}', username)
        self.send_danmu(reply)

    def cleanup(self):
        with self._lock:
            self._welcome_log.clear()
        self._in_lottery = False
