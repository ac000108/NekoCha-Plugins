# -*- coding: utf-8 -*-
"""
礼物展示插件
前端每秒查询数据库，滚动展示最新礼物记录
"""

from core.plugin_manager import BasePlugin


class GiftShowPlugin(BasePlugin):
    """礼物展示插件 - 仅提供显示页面"""

    def process_message(self, message: dict):
        """处理消息（插件不处理实时消息，仅提供查询展示功能）"""
        pass
