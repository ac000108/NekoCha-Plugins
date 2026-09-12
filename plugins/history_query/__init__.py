# -*- coding: utf-8 -*-
"""
历史查询插件
用于查询历史弹幕、礼物等消息
"""

from core.plugin_manager import BasePlugin


class HistoryQueryPlugin(BasePlugin):
    """历史查询插件 - 仅提供显示页面"""
    
    def process_message(self, message: dict):
        """处理消息（插件不处理实时消息，仅提供查询功能）"""
        pass

