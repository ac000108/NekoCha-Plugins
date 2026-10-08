"""
盲盒随机点歌 - 点歌姬简化版

规则（只有一条）：
  观众送出盲盒礼物 → 从歌单随机抽一首 → 发弹幕点歌

相比点歌姬去掉了：弹幕指令、免费次数、冷却、主播豁免、确认等待、展示页。
SEND_GIFT_V2 中每个盲盒 GiftItem 会产出独立礼物消息，故一条盲盒消息抽一首歌。
"""

import random
from core.plugin_manager import BasePlugin


class BlindBoxSongPlugin(BasePlugin):

    def _get_song_list(self) -> list:
        raw = self._config.get('歌曲列表', '')
        if not raw or not isinstance(raw, str):
            return []
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def process_message(self, message: dict):
        if message.get('消息类型') != '礼物':
            return
        if self.is_self_danmu(message):
            return
        if not message.get('是否盲盒'):
            return

        song_list = self._get_song_list()
        if not song_list:
            return

        reply = f"{message.get('用户名', '观众')} 盲盒点歌 {random.choice(song_list)}"
        result = self.send_danmu(reply)
        if result.get('success'):
            print(f"[盲盒点歌] {reply}")
        else:
            print(f"[盲盒点歌] 发送失败: {reply}, 错误: {result.get('message')}")
