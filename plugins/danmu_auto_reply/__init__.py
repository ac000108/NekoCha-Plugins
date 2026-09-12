"""
弹幕自动回复插件 - 根据关键词自动回复弹幕
支持精确匹配和包含匹配两种模式
"""

from core.plugin_manager import BasePlugin


class DanmuAutoReplyPlugin(BasePlugin):
    """弹幕自动回复插件"""

    def process_message(self, message: dict):
        if message.get('消息类型') != '弹幕':
            return

        # 忽略自己发出的弹幕（防止回复再次触发回复）
        if self.is_self_danmu(message):
            return

        content = message.get('弹幕内容', '')
        if not content:
            return

        # 1. 精确匹配（优先级更高）
        exact_dict = self._config.get('精确匹配词条', {})
        if exact_dict:
            matched = exact_dict.get(content)
            if matched is not None:
                self._send_reply(matched)
                return

        # 2. 包含匹配
        contain_dict = self._config.get('包含匹配词条', {})
        if contain_dict:
            for keyword, reply in contain_dict.items():
                if keyword and keyword in content:
                    self._send_reply(reply)
                    return

    def _send_reply(self, reply: str):
        result = self.send_danmu(reply)
        if result.get('success'):
            print(f"[DanmuAutoReply] 已回复: {reply}")

