# -*- coding: utf-8 -*-
"""
netease_player —— 网易云点歌姬（NekoCha 架构）
==============================================

纯 Python，零 Node 依赖，零 pip 依赖。
网易云加密 / 多端解灰全部在 _eapi.py / _multi.py 内部实现。

数据流：
  弹幕: "点歌 晴天"
    → process_message()
    → netease_api.search_and_play('晴天')
    → 网易云搜索 → weapi 加密 → 直链/解灰/歌词 → 多端融合降级
    → danmu_service.broadcast_to_room({cmd:'netease_play', data:{...}})
    → send_danmu('[🎵] 晴天 - 周杰伦')
"""

import logging
import re
import threading

from core.plugin_manager import BasePlugin
from services import danmu_service

logger = logging.getLogger(__name__)


class NeteasePlayerPlugin(BasePlugin):
    """网易云点歌姬 —— 弹幕指令 + 纯 Python API + display.html 悬浮窗"""

    def __init__(self, name, plugin_path):
        super().__init__(name, plugin_path)
        # 纯 Python，没有子进程需要预热
        logger.info(f'[{self.name}] ✅ 纯 Python 模式（无 Node 依赖）')

    def cleanup(self):
        """插件销毁：无子进程需要清理"""
        super().cleanup()

    # ========== BasePlugin 钩子 ==========

    def process_message(self, message: dict):
        msg_type = message.get('消息类型')
        if msg_type != '弹幕':
            return
        if self.is_self_danmu(message):
            return

        content = (message.get('弹幕内容') or '').strip()
        user = message.get('用户名', '观众')
        if not content:
            return

        for kw in self._config.get('cmd_keywords', ['点歌']):
            pattern = rf'^(/?{re.escape(kw)})\s+(.+)$'
            m = re.match(pattern, content)
            if m:
                keyword = m.group(2).strip()
                logger.info(f'[{self.name}] 🎵 收到点歌: "{keyword}" by {user}')
                threading.Thread(
                    target=self._handle_point_request,
                    args=(keyword, user),
                    daemon=True,
                ).start()
                return

    # ========== 核心：点歌处理 ==========

    def _handle_point_request(self, keyword: str, user: str = ""):
        try:
            from . import netease_api

            self._notify_status(f"🔍 {user} 点歌：{keyword}，正在搜索...")
            result = netease_api.search_and_play(keyword)

            if not result.get("ok"):
                self._notify_reply(f"❌ {result.get('msg', '未知错误')}")
                return

            song = result["song"]
            song_name = song["name"]
            artist_name = song.get("artist", "")
            fee = song.get("fee", -1)
            is_trial = song.get("is_trial", False)
            unblocked = song.get("unblocked", False)
            logger.info(f'[{self.name}] 🎵 {song_name} - {artist_name} fee={fee}'
                        f'{" 🔞试听" if is_trial else ""}{" 🔓解灰" if unblocked else ""}')

            self._sse_broadcast({
                "cmd": "netease_play",
                "data": {
                    "song_id": song.get("netease_id") or song.get("id"),
                    "name": song_name,
                    "artist": artist_name,
                    "album": song.get("album", ""),
                    "cover": song.get("cover", ""),
                    "url": song["url"],
                    "lrc": song.get("lrc", ""),
                    "tlyric": song.get("tlyric", ""),
                    "yrc": song.get("yrc", ""),
                    "fee": fee,
                    "is_trial": is_trial,
                    "unblocked": unblocked,
                    "source_user": user,
                }
            })

            if self._config.get('enable_reply', True):
                if is_trial:
                    fee_tag = "🔞试听"
                elif unblocked:
                    fee_tag = "🔓解灰完整版"
                elif fee in (0, 8):
                    fee_tag = "🎵"
                else:
                    fee_tag = f"💎fee={fee}"
                self.send_danmu(f"🎶 {fee_tag} {song_name} - {artist_name}")

        except Exception as e:
            logger.exception(f'[{self.name}] 点歌处理异常')
            self._notify_reply(f"❌ 点歌失败：{str(e)[:50]}")

    # ========== SSE / 通知工具 ==========

    def _sse_broadcast(self, msg: dict):
        try:
            danmu_service.broadcast_to_room(self._room_id, msg)
        except Exception:
            pass

    def _notify_status(self, text: str):
        self._sse_broadcast({"cmd": "netease_status", "data": {"text": text}})

    def _notify_reply(self, text: str):
        self.send_danmu(text)
