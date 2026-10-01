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
import urllib.parse

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
            raw_url = song["url"]
            play_url = self._proxy_url(raw_url)
            logger.info(f'[{self.name}] 🎵 {song_name} - {artist_name} fee={fee}'
                        f'{" 🔞试听" if is_trial else ""}{" 🔓解灰" if unblocked else ""}'
                        f' proxy={play_url != raw_url}')

            self._sse_broadcast({
                "cmd": "netease_play",
                "data": {
                    "song_id": song.get("netease_id") or song.get("id"),
                    "name": song_name,
                    "artist": artist_name,
                    "album": song.get("album", ""),
                    "cover": song.get("cover", ""),
                    "url": play_url,
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

    # 这些域名的 CDN 没有 CORS 头，必须经后端代理才能在浏览器里播放
    _PROXY_REQUIRED_HOSTS = (
        'kuwo.cn',    # bd-lw.kuwo.cn / bd-er.kuwo.cn / bd-bj.kuwo.cn / bd-lv.kuwo.cn ...
        'kugou.com',
        'y.qq.com',
    )

    def _proxy_url(self, url: str) -> str:
        """
        判断 URL 是否来自无 CORS 的 CDN，是的话改写成同源代理路径。
        网易云官方 (music.126.net) 有 ACAO=* 直接用。
        """
        if not url:
            return url
        try:
            parsed = urllib.parse.urlparse(url)
            host = parsed.hostname or ''
            if any(host.endswith(d) for d in self._PROXY_REQUIRED_HOSTS):
                # 同源代理 —— 前端用相对路径，避免又多一层跨域
                return f'/api/proxy/audio?url={urllib.parse.quote(url, safe="")}'
        except Exception:
            pass
        return url

    def _sse_broadcast(self, msg: dict):
        try:
            danmu_service.broadcast_to_room(self._room_id, msg)
        except Exception:
            pass

    def _notify_status(self, text: str):
        self._sse_broadcast({"cmd": "netease_status", "data": {"text": text}})

    def _notify_reply(self, text: str):
        self.send_danmu(text)

    # ========== 对外 call 方法（供 WebUI 手动触发）==========

    def search(self, keyword: str, limit: int = 5) -> dict:
        """WebUI 搜索按钮调用：返回网易云搜索结果列表"""
        from . import netease_api
        songs = netease_api.search(keyword, limit=limit)
        return {"ok": True, "keyword": keyword, "count": len(songs), "songs": songs}

    def play_by_id(self, song_id) -> dict:
        """WebUI 直接播放按钮：用网易云 song_id 放歌"""
        from . import netease_api
        try:
            sid = int(song_id)
            play = netease_api.get_play_url(sid, level="exhigh")
            if not play or not play.get("url"):
                return {"ok": False, "msg": "拿不到直链（可能是 VIP 歌，解灰失败）"}
            lyric = netease_api.get_lyric(sid)
            detail = netease_api.get_song_detail(sid)
            self._sse_broadcast({
                "cmd": "netease_play",
                "data": {
                    "song_id": sid,
                    "name": detail.get("name", ""),
                    "artist": detail.get("artist", ""),
                    "cover": detail.get("cover", ""),
                    "url": self._proxy_url(play["url"]),
                    "lrc": lyric.get("lrc", ""),
                    "tlyric": lyric.get("tlyric", ""),
                    "yrc": lyric.get("yrc", ""),
                    "fee": play.get("fee", -1),
                    "is_trial": bool(play.get("freeTrialInfo")),
                    "unblocked": play.get("unblocked", False),
                }
            })
            return {"ok": True, "msg": "已推送给播放器"}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def play_keyword(self, keyword: str) -> dict:
        """WebUI 关键词点歌按钮：触发后台点歌流程"""
        threading.Thread(
            target=self._handle_point_request,
            args=(keyword, "WebUI"),
            daemon=True,
        ).start()
        return {"ok": True, "msg": "已触发点歌：" + keyword}

    def status(self) -> dict:
        """插件状态：供 WebUI / 测试用"""
        base = super().status()
        return {
            **base,
            "name": self.name,
            "backend": "pure_python",  # 明确告诉上层：纯 Python，无 Node
            "api": {
                "provider": "netease_weapi",
                "encryption": "pycryptodome_aes_cbc",
                "deblock": "bodian_kuwo_via_urllib",
            },
        }
