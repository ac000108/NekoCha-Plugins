"""
弹幕历史插件 — 全屏高度可滚动历史回看窗

display_mode=floating_scroll，靠 FloatingWindow 尺寸 clamp 机制把 height:9999 自动缩到屏幕高度。
窗口通过前端 D 按钮打开，不需要插件 Python 代码主动创建。
"""

from core.plugin_manager import BasePlugin


class DanmuHistoryPlugin(BasePlugin):
    """全屏高度弹幕历史滚动窗口"""

    def process_message(self, message):
        """消息 → SSE 广播已由 danmu_service 完成，这里保持空即可"""
        pass
