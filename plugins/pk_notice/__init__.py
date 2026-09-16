"""
PK 对手情报插件 - PK 开始时自动查询对手信息，发送两条弹幕到本房间
"""

from core.plugin_manager import BasePlugin
import logging
import requests


HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://live.bilibili.com/',
}


class PKNoticePlugin(BasePlugin):
    """PK 对手情报插件"""

    def process_message(self, message: dict):
        if message.get('消息类型') != 'PK':
            return
        if message.get('阶段') != '开始':
            return

        opp_room = message.get('对方房间号')
        if not opp_room:
            logging.warning("[PKNotice] PK 消息缺少对方房间号")
            return

        try:
            info = self._fetch_opponent_info(opp_room)
            # 第一条：对手xx,x粉x船
            msg1 = self._format_basic(info)
            if msg1:
                self.send_danmu(msg1)
                logging.info(f"[PKNotice] 对手情报1: {msg1}")
            # 第二条：在线x名（X总X提X舰X贡献）
            msg2 = self._format_detail(info)
            self.send_danmu(msg2)
            logging.info(f"[PKNotice] 对手情报2: {msg2}")
        except Exception as e:
            logging.exception(f"[PKNotice] 查询对手信息失败: {e}")

    # ========== HTTP 查询 ==========

    def _fetch_opponent_info(self, room_id: int) -> dict:
        """查询对手房间的关键信息"""
        session = requests.Session()

        # 1. room_init → 拿主播 UID
        resp = session.get(
            'https://api.live.bilibili.com/room/v1/Room/room_init',
            params={'id': room_id}, headers=HEADERS, timeout=8
        )
        data = resp.json().get('data', {})
        real_roomid = data.get('room_id', room_id)
        anchor_uid = data.get('uid', 0)

        # 2. Master/info → 主播昵称 + 粉丝数 + 总舰长数
        nickname = ''
        follower_num = 0
        total_guards = 0
        try:
            resp = session.get(
                'https://api.live.bilibili.com/live_user/v1/Master/info',
                params={'uid': anchor_uid}, headers=HEADERS, timeout=8
            )
            master = resp.json().get('data', {})
            info = master.get('info', {})
            nickname = info.get('uname', '')
            follower_num = info.get('follower_num', 0)
            total_guards = info.get('guard_level', 0)
        except Exception:
            pass

        # 3. guardTab/topList → 舰长等级分布 + 覆盖 total_guards
        governor = admiral = captain = 0
        try:
            resp = session.get(
                'https://api.live.bilibili.com/xlive/app-room/v2/guardTab/topListNew',
                params={'ruid': anchor_uid, 'roomid': real_roomid, 'page': 1},
                headers=HEADERS, timeout=8
            )
            guard_data = resp.json().get('data', {})
            guard_info = guard_data.get('info', {})
            total_guards = guard_info.get('num', total_guards)
            for item in guard_data.get('top3', []) + guard_data.get('list', []):
                lv = item.get('guard_level', 0)
                if lv == 1:
                    governor += 1
                elif lv == 2:
                    admiral += 1
                elif lv == 3:
                    captain += 1
        except Exception:
            pass

        # 4. getOnlineGoldRank → 高能榜
        online_count = 0
        online_guard_count = 0
        top_score = 0
        try:
            resp = session.get(
                'https://api.live.bilibili.com/xlive/general-interface/v1/rank/getOnlineGoldRank',
                params={'ruid': anchor_uid, 'roomId': real_roomid},
                headers=HEADERS, timeout=8
            )
            rank_data = resp.json().get('data', {})
            online_count = rank_data.get('onlineNum', 0)
            for item in rank_data.get('OnlineRankItem', []):
                lv = item.get('guard_level', 0)
                score = item.get('score', 0)
                if lv > 0:
                    online_guard_count += 1
                if score > top_score:
                    top_score = score
        except Exception:
            pass

        return {
            'nickname': nickname,
            'follower_num': follower_num,
            'total_guards': total_guards,
            'governor': governor,
            'admiral': admiral,
            'captain': captain,
            'online_count': online_count,
            'online_guard_count': online_guard_count,
            'top_score': top_score,
        }

    # ========== 弹幕格式化 ==========

    def _format_basic(self, info: dict) -> str:
        """第一条：对手xx,x粉x船"""
        nick = info['nickname'] or f"房间"
        follower = self._fmt_num(info['follower_num'])
        ship = info['total_guards']
        return f"对手{nick},{follower}粉{ship}船"

    def _format_detail(self, info: dict) -> str:
        """第二条：在线x名（X总X提X舰X贡献）"""
        online = info['online_count'] or info['online_guard_count'] or '?'
        parts = []
        if info['governor']:
            parts.append(f"{info['governor']}总")
        if info['admiral']:
            parts.append(f"{info['admiral']}提")
        if info['captain']:
            parts.append(f"{info['captain']}舰")
        if not parts and info['total_guards']:
            parts.append(f"{info['total_guards']}舰")
        if info['top_score']:
            parts.append(f"{info['top_score']}贡献")
        return f"在线{online}名（{''.join(parts)}）"

    @staticmethod
    def _fmt_num(n: int) -> str:
        """粉丝数格式化：万以上转 x.x万"""
        if n >= 10000:
            return f"{n / 10000:.1f}万".rstrip('0').rstrip('.')
        return str(n)
