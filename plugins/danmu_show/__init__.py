"""
弹幕显示插件 - 滚动显示直播间消息

类型一 floating_scroll：无边框、始终穿透，游戏直播覆盖层（100% 透明不遮游戏画面）
Ctrl+Shift+D 临时解除穿透 → 进入交互模式可调试 display.html

窗口通过前端 D 按钮（display_mode=floating_scroll）打开，不需要插件 Python 代码主动创建。
"""

from collections import deque
from core.plugin_manager import BasePlugin


class DanmuShowPlugin(BasePlugin):
    """弹幕滚动覆盖层插件

    display.html 通过 SSE /api/events 获取全量消息；
    native_renderer（Qt 自绘版）通过 get_all_data() 轮询拿最新 30 条。
    两条路径并存，互不冲突。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 缓存最新 30 条消息，供 Qt 原生渲染器轮询
        self._buffer: deque[dict] = deque(maxlen=30)

    def process_message(self, message: dict):
        """每条弹幕消息 → 加进缓存"""
        self._buffer.append(message)

    def get_all_data(self) -> list:
        return list(self._buffer)
