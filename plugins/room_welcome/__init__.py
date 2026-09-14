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

    def process_message(self, message: dict):
        msg_type = message.get('消息类型')
        action = message.get('动作')
        if msg_type != '互动' or action != '进入直播间':
            return

        uid = str(message.get('用户ID', ''))
        username = message.get('用户名', '观众')
        print(f"[RoomWelcome] 收到进入事件: uid={uid}, 用户={username}")

        if self.is_self_danmu(message):
            print(f"[RoomWelcome] 跳过自己")
            return

        cooldown = self._config.get('同一用户冷却秒', 60)
        now = time.time()

        with self._lock:
            last = self._welcome_log.get(uid, 0)
            if now - last < cooldown:
                print(f"[RoomWelcome] 冷却中，跳过 {username} ({now - last:.1f}s < {cooldown}s)")
                return
            self._welcome_log[uid] = now

        templates = self._config.get('欢迎模板列表', [])
        if not templates:
            print(f"[RoomWelcome] 模板列表为空")
            return

        reply = random.choice(templates).replace('{username}', username)
        result = self.send_danmu(reply)
        print(f"[RoomWelcome] 发送: {reply} → {result}")

    def cleanup(self):
        with self._lock:
            self._welcome_log.clear()
