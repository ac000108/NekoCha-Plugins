# -*- coding: utf-8 -*-
"""
netease_player —— 网易云点歌姬（适配 NekoCha 架构）
====================================================

职责：弹幕指令 → Node API 搜索 → 解灰 → 播放 + 歌词显示
依赖：netease_api（Node API 封装，localhost:3000）

数据流：
  弹幕: "点歌 晴天"
    → process_message()
    → netease_api.search_and_play('晴天')
    → 三平台并行搜索 → 网易云匹配 → 解灰/直链/歌词
    → danmu_service.broadcast_to_room({cmd:'netease_play', data:{...}})
    → send_danmu('[🎵] 晴天 - 周杰伦')
"""

import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from core.plugin_manager import BasePlugin
from services import danmu_service

logger = logging.getLogger(__name__)

_NODE_RUNTIME_DIR = Path(__file__).parent / "node_runtime"
_NODE_SERVER_JS = _NODE_RUNTIME_DIR / "server.js"
_NODE_API_BASE = "http://127.0.0.1:3000"


class NeteasePlayerPlugin(BasePlugin):
    """网易云点歌姬 —— 弹幕指令 + Node API + display.html 悬浮窗"""

    # Node 子进程
    _node_proc = None

    def __init__(self, name, plugin_path):
        super().__init__(name, plugin_path)
        # Node API 自动拉起（后台线程，不阻塞 start）
        threading.Thread(target=self._ensure_node_api_running, daemon=True).start()

    def cleanup(self):
        """插件销毁时清理 Node API 子进程"""
        super().cleanup()
        self._kill_node_api()

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

        # 匹配指令
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

            # 推送给前端 display.html
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

            # 发回复弹幕
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

    # ========== Node API 生命周期 ==========

    def _find_node_exe(self) -> str:
        # 1. 插件内置 node_bin/node.exe
        node_bin = Path(__file__).parent / "node_bin" / "node.exe"
        if node_bin.exists():
            return str(node_bin)
        # 2. 系统 PATH
        try:
            result = subprocess.run(
                ["where", "node"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().split("\n")[0]
        except Exception:
            pass
        # 3. 环境变量
        env_path = os.environ.get("NODE_EXE_PATH")
        if env_path and Path(env_path).exists():
            return env_path
        return ""

    def _ping_node_api(self, timeout=2) -> bool:
        try:
            import urllib.request
            urllib.request.urlopen(
                f"{_NODE_API_BASE}/search?keywords=test&limit=1", timeout=timeout,
            )
            return True
        except Exception:
            return False

    def _ensure_node_api_running(self):
        if self._ping_node_api():
            logger.info(f'[{self.name}] ✅ Node API 已在运行 ({_NODE_API_BASE})')
            return

        node_exe = self._find_node_exe()
        if not node_exe:
            logger.error(
                f'[{self.name}] ❌ 找不到 node.exe！请安装 Node.js 22+，'
                f'或把 node.exe 放到 {Path(__file__).parent / "node_bin"}'
            )
            return

        if not _NODE_SERVER_JS.exists():
            logger.error(f'[{self.name}] ❌ Node API server.js 不存在于 {_NODE_RUNTIME_DIR}')
            return

        try:
            node_dir = str(_NODE_RUNTIME_DIR)
            log_path = os.path.join(node_dir, "node_api.log")
            log_fh = open(log_path, "a", encoding="utf-8")

            env = os.environ.copy()
            env.setdefault("ENABLE_GENERAL_UNBLOCK", "true")
            env.setdefault("ENABLE_RANDOM_CN_IP", "true")
            env.setdefault("ENABLE_FLAC", "true")
            env.setdefault("CORS_ALLOW_ORIGIN", "*")

            kwargs = {
                "cwd": node_dir,
                "stdout": log_fh, "stderr": log_fh,
                "env": env, "shell": False,
            }
            if sys.platform == "win32":
                kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

            self._node_proc = subprocess.Popen([node_exe, "server.js"], **kwargs)
            logger.info(f'[{self.name}] 🚀 拉起 Node API (pid={self._node_proc.pid})')

            for i in range(15):
                time.sleep(1)
                if self._ping_node_api(timeout=1):
                    logger.info(f'[{self.name}] ✅ Node API 启动成功')
                    return
                if self._node_proc.poll() is not None:
                    logger.error(
                        f'[{self.name}] ❌ Node API 启动失败（进程退出 code={self._node_proc.returncode}）'
                    )
                    try:
                        log_fh.close()
                        with open(log_path, "r", encoding="utf-8") as f:
                            logger.error(f'[{self.name}] Node API 日志:\n{"".join(f.readlines()[-10:])}')
                    except Exception:
                        pass
                    self._node_proc = None
                    return

            logger.warning(f'[{self.name}] ⚠️ Node API 启动超时（15s）')
        except Exception:
            logger.exception(f'[{self.name}] ❌ 拉起 Node API 异常')

    def _kill_node_api(self):
        if self._node_proc and self._node_proc.poll() is None:
            try:
                self._node_proc.terminate()
                self._node_proc.wait(timeout=3)
                logger.info(f'[{self.name}] 🛑 Node API 已停止')
            except Exception:
                try:
                    self._node_proc.kill()
                except Exception:
                    pass
            self._node_proc = None

    # ========== SSE / 通知工具 ==========

    def _sse_broadcast(self, msg: dict):
        """广播 SSE 给所有前端（display.html 用 EventSource 订阅 /api/events）"""
        try:
            danmu_service.broadcast_to_room(self._room_id, msg)
        except Exception:
            pass

    def _notify_status(self, text: str):
        self._sse_broadcast({"cmd": "netease_status", "data": {"text": text}})

    def _notify_reply(self, text: str):
        """发弹幕回复（send_danmu 已经会自己处理成功/失败）"""
        self.send_danmu(text)
