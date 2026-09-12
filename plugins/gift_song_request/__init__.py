"""
礼物随机点歌插件 - 通过礼物/舰长弹幕触发随机点歌

配置说明：
- 触发礼物列表: 礼物名称或盲盒名称匹配时触发
- 歌曲列表: 配置中的多行字符串，每行一首歌
- 回复模板: 点歌回复模板，支持 {用户名} {歌曲名} {礼物名称} {盲盒名称} {舰长等级} 变量
- 舰长弹幕点歌开关: 开启后，舰长/提督/总督发送"随机点歌"弹幕可触发随机点歌
- 舰长弹幕触发词: 触发点歌的弹幕关键词（默认"随机点歌"）
"""

import random
from core.plugin_manager import BasePlugin

# 舰长等级常量（消息中存储为中文）
GUARD_LEVELS = {'舰长', '提督', '总督'}


class GiftSongRequestPlugin(BasePlugin):
    """礼物随机点歌插件"""

    def _get_song_list(self) -> list:
        """从配置的歌曲列表字符串解析，按行分割"""
        raw = self._config.get('歌曲列表', '')
        if not raw or not isinstance(raw, str):
            return []
        songs = [line.strip() for line in raw.splitlines() if line.strip()]
        return songs

    def _reply_with_song(self, template: str, song: str, variables: dict):
        """根据模板和变量发送点歌弹幕"""
        reply = template.replace('{歌曲名}', song)
        for key, value in variables.items():
            reply = reply.replace(f'{{{key}}}', str(value))

        result = self.send_danmu(reply)
        if result.get('success'):
            print(f"[GiftSongRequest] 发送成功: {reply}")
        else:
            print(f"[GiftSongRequest] 发送失败: {reply}, 错误: {result.get('message')}")

    def process_message(self, message: dict):
        msg_type = message.get('消息类型')

        if msg_type == '礼物':
            self._handle_gift(message)
        elif msg_type == '弹幕':
            self._handle_danmu(message)

    def _handle_gift(self, message: dict):
        """处理礼物消息触发点歌"""
        if self.is_self_danmu(message):
            print("[GiftSongRequest] 忽略自己的礼物")
            return

        gift_name = message.get('礼物名称', '')
        blind_name = message.get('盲盒名称', '')

        trigger_gifts = self._config.get('触发礼物列表', [])
        if not trigger_gifts:
            return

        matched = any(trigger == gift_name or (blind_name and trigger == blind_name) for trigger in trigger_gifts)
        if not matched:
            return

        song_list = self._get_song_list()
        if not song_list:
            return

        song = random.choice(song_list)
        print(f"[GiftSongRequest] 礼物触发 - 随机选歌: {song}")

        template = self._config.get('回复模板', '{用户名} 点歌：{歌曲名}')
        variables = {
            '用户名': message.get('用户名', '观众'),
            '礼物名称': gift_name,
            '盲盒名称': blind_name or '',
            '舰长等级': message.get('舰长等级', '')
        }
        self._reply_with_song(template, song, variables)

    def _handle_danmu(self, message: dict):
        """处理弹幕消息 - 舰长/提督/总督发送关键词触发点歌"""
        if not self._config.get('舰长弹幕点歌开关', False):
            return

        if self.is_self_danmu(message):
            return

        guard_level = message.get('舰长等级', '')
        if guard_level not in GUARD_LEVELS:
            return

        content = message.get('弹幕内容', '').strip()
        trigger_word = self._config.get('舰长弹幕触发词', '随机点歌')
        if trigger_word not in content:
            return

        song_list = self._get_song_list()
        if not song_list:
            return

        song = random.choice(song_list)
        print(f"[GiftSongRequest] 舰长弹幕触发 - 随机选歌: {song}")

        template = self._config.get('回复模板', '{用户名} 点歌：{歌曲名}')
        variables = {
            '用户名': message.get('用户名', '观众'),
            '礼物名称': '',
            '盲盒名称': '',
            '舰长等级': guard_level
        }
        self._reply_with_song(template, song, variables)

