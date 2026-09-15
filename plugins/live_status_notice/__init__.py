"""
开播下播通知插件 - 在直播状态切换时自动发送弹幕通知，并可选调用 NapCat 发送 QQ 群消息/图片卡片

触发时机：
- 开播（直播状态=1）→ 发送弹幕 + 可选 QQ 群文本/卡片
- 下播（直播状态=0）或切轮播（直播状态=2）→ 发送弹幕 + 可选 QQ 群文本/卡片

去重机制：记录上一次的直播状态，只有状态真正变化时才发送，
避免 B 站重复推送同一条 LIVE/PREPARING 消息导致重复发送。
"""

import io
import os
import sys
import base64
import threading

import requests
from core.plugin_manager import BasePlugin


# ==================== 工具函数 ====================

def _get_chinese_font(size: int):
    """获取中文字体。优先用插件自带的悠哉字体，fallback 到系统字体。"""
    from PIL import ImageFont

    # 1. 插件自带悠哉字体（优先级最高）
    fp = os.path.join(os.path.dirname(__file__), 'fonts', 'Yozai-Regular.ttf')
    if os.path.exists(fp):
        try:
            return ImageFont.truetype(fp, size)
        except Exception:
            pass

    # 2. 系统字体 fallback
    font_paths = []
    if sys.platform == 'win32':
        windir = os.environ.get('WINDIR', r'C:\Windows')
        font_dir = os.path.join(windir, 'Fonts')
        font_paths = [
            os.path.join(font_dir, 'msyh.ttc'),
            os.path.join(font_dir, 'msyhbd.ttc'),
            os.path.join(font_dir, 'simhei.ttf'),
            os.path.join(font_dir, 'simfang.ttf'),
        ]
    elif sys.platform == 'darwin':
        font_paths = [
            '/System/Library/Fonts/PingFang.ttc',
            '/System/Library/Fonts/STHeiti Medium.ttc',
        ]
    else:
        font_paths = [
            '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
            '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
        ]

    for fp in font_paths:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _fetch_room_info(room_id: str) -> dict:
    """从 B 站 API 拉取直播间信息（标题、封面、主播名、头像、分区等）

    用两步组合:
    1. room/v1/Room/get_info 拿房间基本信息（title, cover, area, uid）
    2. x/web-interface/card 拿主播 name + face
    避开 getInfoByRoom 的风控(-352)

    返回字典额外包含 live_start_ts（开播时间 Unix 秒，API 拿不到则 None）
    """
    from datetime import datetime as _dt

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://live.bilibili.com/',
    }
    result = {
        'title': f'直播间{room_id}',
        'cover': '',
        'live_status': 0,
        'area_name': '',
        'parent_area_name': '',
        'uname': '',
        'face': '',
        'live_start_ts': None,  # 开播时间 Unix 秒
    }

    # 第一步: 房间信息
    try:
        url = f'https://api.live.bilibili.com/room/v1/Room/get_info?room_id={room_id}'
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        if data.get('code') == 0:
            info = data.get('data', {})
            result['title'] = info.get('title', result['title'])
            result['cover'] = info.get('user_cover', '') or info.get('keyframe', '')
            result['live_status'] = info.get('live_status', 0)
            result['area_name'] = info.get('area_name', '')
            result['parent_area_name'] = info.get('parent_area_name', '')
            # 直播中时尝试解析开播时间
            if result['live_status'] == 1:
                lt = info.get('live_time', '')
                if lt and lt != '0000-00-00 00:00:00':
                    try:
                        result['live_start_ts'] = _dt.strptime(lt, '%Y-%m-%d %H:%M:%S').timestamp()
                    except ValueError:
                        pass
            uid = info.get('uid')
        else:
            uid = None
    except Exception:
        uid = None

    # 第二步: 主播信息（用 uid 查 card 接口）
    if uid:
        try:
            url = f'https://api.bilibili.com/x/web-interface/card?mid={uid}'
            resp = requests.get(url, headers=headers, timeout=10)
            data = resp.json()
            if data.get('code') == 0:
                card = data.get('data', {}).get('card', {})
                result['uname'] = card.get('name', '')
                result['face'] = card.get('face', '')
        except Exception:
            pass

    return result


def _download_image(url: str) -> 'PIL.Image.Image | None':
    """从 URL 下载图片并返回 PIL Image 对象"""
    if not url:
        return None
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://live.bilibili.com/',
        }
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        from PIL import Image
        return Image.open(io.BytesIO(resp.content)).convert('RGBA')
    except Exception:
        return None


# ==================== 卡片绘制 ====================

def _draw_card(room_id: str, room_info: dict, is_live: bool, footer: str = 'NekoCha Live') -> bytes:
    """绘制开播/下播卡片图片，返回 JPEG bytes.

    封面固定 16:10 (B 站标准直播封面比例).
    H 动态计算 = 所有元素实际位置 + MARGIN.
    """
    from PIL import Image, ImageDraw, ImageFont
    import qrcode

    # --- 画布尺寸（2x 高清） ---
    W = 1360
    MARGIN = 40              # 四边统一 (20*2)
    CARD_RADIUS = 40         # (20*2)
    LINE_H = 88              # (44*2)
    TITLE_Y = MARGIN         # 标题顶部 = MARGIN
    QR_Y = MARGIN            # QR顶部 = MARGIN（和标题对齐）

    # --- 颜色方案 ---
    if is_live:
        BG_TOP = (255, 240, 245)
        BG_BOT = (255, 250, 252)
        ACCENT = (255, 105, 155)
        STATUS_TEXT = '正在直播'
        COVER_STATUS_BG = (255, 120, 150, 220)
    else:
        BG_TOP = (240, 245, 255)
        BG_BOT = (250, 252, 255)
        ACCENT = (100, 150, 220)
        STATUS_TEXT = '下播啦~'
        COVER_STATUS_BG = (120, 140, 200, 220)

    TEXT_DARK = (50, 40, 50)
    TEXT_MID = (120, 110, 120)
    TEXT_LIGHT = (255, 255, 255)

    # --- 字体（2x，悠哉优先）---
    font_title = _get_chinese_font(68)
    font_name = _get_chinese_font(48)
    font_area = _get_chinese_font(36)
    font_status = _get_chinese_font(32)
    font_footer = _get_chinese_font(28)

    # --- 先算好所有布局 ---
    tmp = Image.new('RGB', (W, 200), 'white')
    tmp_draw = ImageDraw.Draw(tmp)

    title = _ellipsis_text(
        tmp_draw,
        room_info.get('title', f'直播间 {room_id}'),
        font_title,
        # QR 底板 220px + 左侧间距 80px = 300，留够呼吸空间
        W - MARGIN * 2 - 300,
    )

    avatar_size = 112
    cover_w = W - MARGIN * 2              # 1280
    cover_h = int(cover_w * 9 / 16)       # 720  (16:9)
    cover_gap = 32

    # --- 布局精确计算 ---
    title_bottom = TITLE_Y + LINE_H
    avatar_y = title_bottom + 16
    avatar_bottom = avatar_y + avatar_size
    cover_top = avatar_bottom + cover_gap
    cover_bottom = cover_top + cover_h
    # 底部 MARGIN 区域就是页脚空间，文字居中画在 MARGIN 里
    footer_y = cover_bottom + MARGIN // 2
    H = cover_bottom + MARGIN
    H = int(H)

    # ============ 正式绘制 ============
    # 渐变背景
    rounded_bg = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    for y in range(H):
        ratio = y / H
        r = int(BG_TOP[0] * (1 - ratio) + BG_BOT[0] * ratio)
        g = int(BG_TOP[1] * (1 - ratio) + BG_BOT[1] * ratio)
        b = int(BG_TOP[2] * (1 - ratio) + BG_BOT[2] * ratio)
        ImageDraw.Draw(rounded_bg).line([(0, y), (W, y)], fill=(r, g, b, 255))

    mask = Image.new('L', (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle([(0, 0), (W - 1, H - 1)], radius=CARD_RADIUS, fill=255)
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    img.paste(rounded_bg, (0, 0), mask)
    draw = ImageDraw.Draw(img)

    # --- 标题（单行 + 省略号）---
    draw.text((MARGIN, TITLE_Y), title, fill=TEXT_DARK, font=font_title)

    # --- QR 码 ---
    qr_size = 192
    qr_pad = 14              # 圆角底板比 QR 大一圈
    qr_bg_size = qr_size + qr_pad * 2
    qr_bg_x = W - MARGIN - qr_bg_size
    qr_bg_y = QR_Y           # 底板顶部 = MARGIN
    qr_x = qr_bg_x + qr_pad  # QR 内容在底板内偏移
    qr_y = qr_bg_y + qr_pad
    try:
        qr = qrcode.QRCode(version=None, box_size=4, border=1)
        qr.add_data(f'https://live.bilibili.com/{room_id}')
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color=TEXT_DARK, back_color='white').convert('RGBA')
        qr_img = qr_img.resize((qr_size, qr_size), Image.LANCZOS)
        qr_bg = Image.new('RGBA', (qr_bg_size, qr_bg_size), (255, 255, 255, 0))
        ImageDraw.Draw(qr_bg).rounded_rectangle([(0, 0), (qr_bg_size - 1, qr_bg_size - 1)], radius=20, fill=(255, 255, 255, 240))
        img.paste(qr_bg, (qr_bg_x, qr_bg_y), qr_bg)
        img.paste(qr_img, (qr_x, qr_y), qr_img)
    except Exception:
        pass

    # --- 头像 ---
    avatar_img = _download_image(room_info.get('face', ''))
    if avatar_img:
        avatar_img = avatar_img.resize((avatar_size, avatar_size), Image.LANCZOS)
        circle_mask = Image.new('L', (avatar_size, avatar_size), 0)
        ImageDraw.Draw(circle_mask).ellipse([(0, 0), (avatar_size - 1, avatar_size - 1)], fill=255)
        avatar_round = Image.new('RGBA', (avatar_size, avatar_size), (0, 0, 0, 0))
        avatar_round.paste(avatar_img, (0, 0), circle_mask)
        img.paste(avatar_round, (MARGIN, avatar_y), avatar_round)
        draw.ellipse(
            [(MARGIN - 2, avatar_y - 2), (MARGIN + avatar_size + 1, avatar_y + avatar_size + 1)],
            outline=ACCENT, width=4
        )
    else:
        draw.ellipse(
            [(MARGIN, avatar_y), (MARGIN + avatar_size, avatar_y + avatar_size)],
            fill=(230, 220, 230)
        )

    # --- 主播名 ---
    name_x = MARGIN + avatar_size + 24
    uname = room_info.get('uname', '未知主播')
    draw.text((name_x, avatar_y + 4), uname, fill=TEXT_DARK, font=font_name)

    # --- 分区 ---
    area = room_info.get('area_name', '') or room_info.get('parent_area_name', '') or ''
    if area:
        draw.text((name_x, avatar_y + 64), area, fill=ACCENT, font=font_area)

    # --- 封面 ---
    cover_img = _download_image(room_info.get('cover', ''))
    if cover_img:
        cw, ch = cover_img.size
        target_ratio = cover_w / cover_h
        src_ratio = cw / ch
        if src_ratio > target_ratio:
            new_ch = cover_h
            new_cw = int(new_ch * src_ratio)
            cover_img = cover_img.resize((new_cw, new_ch), Image.LANCZOS)
            left = (new_cw - cover_w) // 2
            cover_img = cover_img.crop((left, 0, left + cover_w, cover_h))
        else:
            new_cw = cover_w
            new_ch = int(new_cw / src_ratio)
            cover_img = cover_img.resize((new_cw, new_ch), Image.LANCZOS)
            top = (new_ch - cover_h) // 2
            cover_img = cover_img.crop((0, top, cover_w, top + cover_h))
        cover_img = cover_img.convert('RGBA')
    else:
        cover_img = Image.new('RGBA', (cover_w, cover_h), ACCENT)
        for yy in range(cover_h):
            alpha = int(80 * (1 - yy / cover_h))
            ImageDraw.Draw(cover_img).line(
                [(0, yy), (cover_w, yy)],
                fill=(ACCENT[0], ACCENT[1], ACCENT[2], alpha)
            )

    cover_mask = Image.new('L', (cover_w, cover_h), 0)
    ImageDraw.Draw(cover_mask).rounded_rectangle([(0, 0), (cover_w - 1, cover_h - 1)], radius=28, fill=255)
    cover_canvas = Image.new('RGBA', (cover_w, cover_h), (0, 0, 0, 0))
    cover_canvas.paste(cover_img, (0, 0), cover_mask)
    img.paste(cover_canvas, (MARGIN, cover_top), cover_canvas)

    # --- 封面状态徽章 ---
    badge_text = f'● {STATUS_TEXT}~'
    bw = int(draw.textlength(badge_text, font=font_status)) + 40
    bh = 60
    bx = W - MARGIN - bw - 20
    by = cover_top + 20
    badge_bg = Image.new('RGBA', (bw, bh), (0, 0, 0, 0))
    ImageDraw.Draw(badge_bg).rounded_rectangle([(0, 0), (bw - 1, bh - 1)], radius=30, fill=COVER_STATUS_BG)
    img.paste(badge_bg, (bx, by), badge_bg)
    draw = ImageDraw.Draw(img)
    draw.text((bx + 20, by + 12), badge_text, fill=TEXT_LIGHT, font=font_status)

    # --- Footer（居中，底部 MARGIN 处） ---
    if footer:
        fw = int(draw.textlength(footer, font=font_footer))
        # footer_y 是 MARGIN 顶部，需要减去文字高度的一半来真正居中
        # 用 textbbox 或 textlength 算高度太麻烦，直接用 font size 的比例
        fh = font_footer.size
        draw.text(((W - fw) // 2, footer_y - fh // 2), footer, fill=TEXT_MID, font=font_footer)

    # --- 返回 JPEG（高画质：q95 + 4:4:4 不做色度子采样） ---
    buf = io.BytesIO()
    img.convert('RGB').save(buf, format='JPEG', quality=95, subsampling=0, optimize=True)
    return buf.getvalue()


def _wrap_text(draw, text, font, max_width):
    """按最大宽度自动换行"""
    lines = []
    current = ''
    for ch in text:
        test = current + ch
        w = draw.textlength(test, font=font)
        if w > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = test
    if current:
        lines.append(current)
    return lines


def _ellipsis_text(draw, text, font, max_width, ellipsis='…'):
    """单行截断，超出加省略号"""
    if not text:
        return text
    if draw.textlength(text, font=font) <= max_width:
        return text
    # 从末尾逐个删字直到 fit，再加省略号
    ellipsis_w = draw.textlength(ellipsis, font=font)
    available = max_width - ellipsis_w
    for i in range(len(text), 0, -1):
        s = text[:i]
        if draw.textlength(s, font=font) <= available:
            return s + ellipsis
    return ellipsis


# ==================== 插件主体 ====================

class LiveStatusNoticePlugin(BasePlugin):
    """开播下播通知插件"""

    # NapCat 连接配置（写死）
    NAPCAT_URL = 'http://nekocha.ac000108.cn'
    NAPCAT_TOKEN = 'loerkn7b-fBPPm-2'
    CARD_FOOTER = 'NekoCha Live'

    def __init__(self, name: str, plugin_path: str):
        super().__init__(name, plugin_path)
        # 上一次的直播状态: None=未知, 0=未开播, 1=直播中, 2=轮播中
        self._last_status = None
        self._lock = threading.Lock()
        # LIVE 消息自带的开播时间（Unix 秒），API 兜底为辅
        self._live_start_ts = None

    def _bind_room(self, room_id: str):
        super()._bind_room(room_id)
        # 主动查一次 B 站 API 拿当前直播状态 + 开播时间做基线
        # B 站 WS 只在开播/下播瞬间推 LIVE/PREPARING，不会告诉你"当前状态"
        # 所以必须主动查询才能避免冷启动误判
        if room_id:
            try:
                info = _fetch_room_info(str(room_id))
                status = int(info.get('live_status', 0))
                with self._lock:
                    self._last_status = status
                    # 中途开启/插件 reload 时主播已在播：API 能拿到 live_time 就存上
                    if status == 1:
                        self._live_start_ts = info.get('live_start_ts') or None
            except Exception:
                # API 调用失败也没关系，会在 process_message 里兜底
                pass

    def process_message(self, message: dict):
        if message.get('消息类型') != '直播状态':
            return

        try:
            status = int(message.get('直播状态', -1))
        except (TypeError, ValueError):
            return

        with self._lock:
            if self._last_status is None:
                # API 也查失败的兜底：第一次收到状态只建基线，不触发
                self._last_status = status
                if status == 1:
                    self._live_start_ts = message.get('live_time') or None
                return
            if status == self._last_status:
                return
            self._last_status = status

        if status == 1:
            self._live_start_ts = message.get('live_time') or None
            try:
                room_info = _fetch_room_info(str(self._room_id)) if self._room_id else None
            except Exception:
                room_info = None
            self._on_live_start(room_info)
        elif status in (0, 2):
            duration_sec = self._calc_duration(message)
            self._live_start_ts = None
            try:
                room_info = _fetch_room_info(str(self._room_id)) if self._room_id else None
            except Exception:
                room_info = None
            self._on_live_end(duration_sec, room_info)

    # ==================== NapCat 消息发送 ====================

    def _napcat_post(self, endpoint: str, payload: dict) -> dict:
        """发送 POST 请求到 NapCat OneBot v11 API"""
        base_url = self.NAPCAT_URL.rstrip('/')
        token = self.NAPCAT_TOKEN
        group_id = self._config.get('QQ群号', '').strip()

        if not base_url or not group_id:
            return {'success': False, 'message': 'NapCat地址或QQ群号未配置'}

        try:
            group_id = int(group_id)
        except (ValueError, TypeError):
            return {'success': False, 'message': f'QQ群号格式错误: {group_id}'}

        url = f"{base_url}{endpoint}"
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = f'Bearer {token}'

        full_payload = {'group_id': group_id, **payload}

        try:
            resp = requests.post(url, json=full_payload, headers=headers, timeout=30)
            try:
                data = resp.json()
            except Exception:
                data = {}
            if data.get('retcode') == 0:
                return {'success': True}
            return {'success': False, 'retcode': data.get('retcode'), 'message': data.get('message', '')}
        except requests.RequestException as e:
            return {'success': False, 'message': str(e)}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def _send_qq_text(self, message: str, at_all: bool = False):
        """发送 QQ 群文字消息，可选 @全体成员"""
        if not self._config.get('启用QQ群通知', False):
            return

        segments = []
        if at_all:
            segments.append({'type': 'at', 'data': {'qq': 'all'}})
        segments.append({'type': 'text', 'data': {'text': message}})

        result = self._napcat_post('/send_group_msg', {'message': segments})
        if result.get('success'):
            tag = '@全体 ' if at_all else ''
            print(f"[{self.name}] QQ群 {tag}已发送: {message}")
        else:
            print(f"[{self.name}] QQ群 发送失败: {result}")

    def _send_qq_image_card(self, is_live: bool, duration_text: str = None):
        """发送 QQ 群图片卡片"""
        if not self._config.get('启用QQ群通知', False):
            return

        room_id = self._room_id or ''
        if not room_id:
            print(f"[{self.name}] 无可用房间ID，跳过卡片生成")
            return

        # 拉取房间信息
        room_info = _fetch_room_info(room_id)

        # 生成卡片图片
        try:
            footer = self.CARD_FOOTER
            if not is_live and duration_text:
                footer = f'直播 {duration_text} · {footer}'
            png_bytes = _draw_card(room_id, room_info, is_live, footer)
        except Exception as e:
            print(f"[{self.name}] 卡片绘制失败: {e}")
            return

        # base64 编码
        b64 = base64.b64encode(png_bytes).decode('ascii')

        segments = [{'type': 'image', 'data': {'file': f'base64://{b64}'}}]
        result = self._napcat_post('/send_group_msg', {'message': segments})
        if result.get('success'):
            print(f"[{self.name}] QQ卡片已发送 (直播={'开' if is_live else '下'})")
        else:
            print(f"[{self.name}] QQ卡片发送失败: {result}")

    # ==================== 开播/下播触发 ====================

    def _calc_duration(self, preparing_message: dict) -> int | None:
        """算直播时长。优先用内存里存的 LIVE 消息 live_time，API 兜底为辅。"""
        start_ts = self._live_start_ts
        if not start_ts and self._room_id:
            try:
                info = _fetch_room_info(self._room_id)
                start_ts = info.get('live_start_ts')
            except Exception:
                pass
        if not start_ts:
            return None
        send_time_ms = preparing_message.get('send_time', 0) or 0
        end_ts = send_time_ms / 1000 if send_time_ms else None
        if not end_ts:
            import time as _t
            end_ts = _t.time()
        return max(0, int(end_ts - start_ts))

    @staticmethod
    def _format_duration(sec: int) -> str:
        """秒数 → 人读的时长文本，如 '2h 35m' / '45m 10s' / '8s'"""
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        if h > 0:
            return f'{h}h {m}m'
        if m > 0:
            return f'{m}m {s}s'
        return f'{s}s'

    @staticmethod
    def _format_msg(template: str, **variables) -> str:
        """模板变量替换，支持 {live_time} {uname} {room_id} {title} 等。
        变量不存在时保留原样（比如开播时 {live_time} 会原样显示，调用方自行决定）。"""
        if not template:
            return template
        result = template
        for key, value in variables.items():
            if value is None:
                continue
            result = result.replace(f'{{{key}}}', str(value))
        return result

    def _on_live_start(self, room_info: dict = None):
        # 可用变量
        variables = {
            'uname': room_info.get('uname', '') if room_info else '',
            'room_id': self._room_id or '',
            'title': room_info.get('title', '') if room_info else '',
        }

        # B站弹幕
        if self._config.get('启用B站弹幕', True) and self._config.get('开播B站弹幕', True):
            msg = self._format_msg(self._config.get('开播弹幕', '开！'), **variables)
            result = self.send_danmu(msg)
            if result.get('success'):
                print(f"[{self.name}] 开播弹幕已发送: {msg}")
            else:
                print(f"[{self.name}] 开播弹幕发送失败: {result}")

        # QQ群
        if self._config.get('启用QQ群通知', False) and self._config.get('开播QQ通知', True):
            text = self._format_msg(self._config.get('开播群文字', '开播啦~'), **variables)
            at_all = self._config.get('开播@全体', True)
            send_image = self._config.get('开播发图片', True)
            if text:
                self._send_qq_text(text, at_all=at_all)
            if send_image and text:
                import time; time.sleep(2)
            if send_image:
                self._send_qq_image_card(is_live=True)

    def _on_live_end(self, duration_sec: int | None = None, room_info: dict = None):
        duration_text = self._format_duration(duration_sec) if duration_sec is not None else None

        # 可用变量
        variables = {
            'live_time': duration_text or '',
            'uname': room_info.get('uname', '') if room_info else '',
            'room_id': self._room_id or '',
            'title': room_info.get('title', '') if room_info else '',
        }

        # B站弹幕
        if self._config.get('启用B站弹幕', True) and self._config.get('下播B站弹幕', True):
            msg = self._format_msg(self._config.get('下播弹幕', '下播啦~'), **variables)
            result = self.send_danmu(msg)
            if result.get('success'):
                print(f"[{self.name}] 下播弹幕已发送: {msg}")
            else:
                print(f"[{self.name}] 下播弹幕发送失败: {result}")

        # QQ群
        if self._config.get('启用QQ群通知', False) and self._config.get('下播QQ通知', False):
            text = self._format_msg(self._config.get('下播群文字', '下播啦~'), **variables)
            at_all = self._config.get('下播@全体', False)
            send_image = self._config.get('下播发图片', False)
            if text:
                self._send_qq_text(text, at_all=at_all)
            if send_image and text:
                import time; time.sleep(2)
            if send_image:
                self._send_qq_image_card(is_live=False, duration_text=duration_text)




