"""
盲盒随机点歌 - 点歌姬简化版

规则（只有一条）：
  观众送出盲盒礼物 → 按礼物时间戳对歌单长度取余抽歌 → 发弹幕点歌

伪随机种子为消息自带时间戳，结果确定：同一时间戳在任意插件/任意机器上抽到同一首，
方便后续多个插件对齐结果。
"""

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

        song = song_list[int(message.get('时间戳', 0)) % len(song_list)]
        reply = f"{message.get('用户名', '观众')} 盲盒点歌 {song}"
        result = self.send_danmu(reply)
        if result.get('success'):
            print(f"[盲盒点歌] {reply}")
        else:
            print(f"[盲盒点歌] 发送失败: {reply}, 错误: {result.get('message')}")
