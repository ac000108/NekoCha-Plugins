"""
定时任务插件 - 从随机列表中抽取内容发送弹幕
支持设置时间间隔和发言间隔，两个条件需同时满足
"""

from core.plugin_manager import BasePlugin
from datetime import datetime, timedelta
import random
import threading


class TimedSchedulerPlugin(BasePlugin):
    """定时任务插件"""

    def _start_polling(self):
        """启动定时轮询检查"""
        timer = getattr(self, '_timer', None)
        if timer:
            timer.cancel()
        self._timer = threading.Timer(60, self._check_and_send)
        self._timer.start()

    def _can_send(self):
        """检查是否满足发送条件"""
        config = self._config
        content_list = config.get('随机列表', [])
        min_msg_count = config.get('最小发言次数', 3)
        
        if not content_list or getattr(self, '_message_count', 0) < min_msg_count:
            return False
        
        last_send_time = getattr(self, '_last_send_time', None)
        if last_send_time:
            time_diff = datetime.now() - last_send_time
            min_interval = timedelta(minutes=config.get('最小间隔分钟', 5))
            if time_diff < min_interval:
                return False

        return True

    def _check_and_send(self):
        """定时检查条件并发送消息"""
        if self.is_enabled() and self._can_send():
            self._send_random_message()
        self._start_polling()

    def process_message(self, message: dict):
        if message.get('消息类型') != '弹幕':
            return

        # 忽略自己发出的弹幕（防止自己的回复被计数）
        if self.is_self_danmu(message):
            return

        self._message_count = getattr(self, '_message_count', 0) + 1
        
        if self._can_send():
            self._send_random_message()

    def _send_random_message(self):
        """发送随机消息"""
        content = random.choice(self._config.get('随机列表', []))
        result = self.send_danmu(content)
        
        if result.get('success'):
            print(f"[TimedScheduler] 定时发送: {content}")
            self._last_send_time = datetime.now()
            self._message_count = 0
        else:
            print(f"[TimedScheduler] 发送失败: {result.get('message', '未知错误')}")

    def is_enabled(self, is_live=None):
        """检查插件是否启用，同时启动定时器（第一次调用时）
        
        仅在房间实例（self._room_id 不为 None）且启用时启动定时器，
        模板实例（无房间绑定）跳过以避免资源泄漏。
        """
        result = super().is_enabled(is_live=is_live)
        if result and self._room_id is not None and not getattr(self, '_timer', None):
            self._start_polling()
        return result

    def cleanup(self):
        """房间实例销毁时取消定时器，防止资源泄漏"""
        timer = getattr(self, '_timer', None)
        if timer:
            timer.cancel()
            self._timer = None
