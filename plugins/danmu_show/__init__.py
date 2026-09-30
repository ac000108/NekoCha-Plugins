"""
弹幕显示插件 - 滚动显示直播间消息

类型一 floating_scroll：无边框、始终穿透，游戏直播覆盖层（100% 透明不遮游戏画面）
Ctrl+Shift+D 临时解除穿透 → 进入交互模式可调试 display.html

窗口通过前端 D 按钮（display_mode=floating_scroll）打开，不需要插件 Python 代码主动创建。
"""

from core.plugin_manager import BasePlugin


class DanmuShowPlugin(BasePlugin):
    """弹幕滚动覆盖层插件

    process_message 保持空实现 —— display.html 通过 SSE /api/events 直接获取全量消息，
    插件侧不做过滤。类型过滤由 display.html 启动时从 config.json 读取。
    """

    def process_message(self, message):
        """消息 → SSE 广播已由 danmu_service 完成，这里保持空即可"""
        pass
