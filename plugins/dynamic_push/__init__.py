# -*- coding: utf-8 -*-
"""动态推送插件 - 接收系统分发的动态消息，生成卡片推送到QQ群"""

import io
import os
import re
import time
import urllib.request

from core.plugin_manager import BasePlugin


def _download_image(url: str):
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={'Referer': 'https://www.bilibili.com/'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        from PIL import Image
        return Image.open(io.BytesIO(data)).convert('RGBA')
    except Exception:
        return None


def draw_dynamic_card(msg: dict) -> bytes:
    """根据系统动态消息生成卡片图片"""
    from PIL import Image, ImageDraw, ImageFont

    font_path = os.path.join(os.path.dirname(__file__), 'fonts', 'Yozai-Regular.ttf')
    if not os.path.exists(font_path):
        font_path = None

    fallback_font_paths = []
    for fp in [
        r'C:\Windows\Fonts\msyh.ttc',
        r'C:\Windows\Fonts\simsun.ttc',
        r'C:\Windows\Fonts\segoeuisl.ttf',
        r'C:\Windows\Fonts\seguiemj.ttf',
    ]:
        if os.path.exists(fp):
            fallback_font_paths.append(fp)

    def font(size):
        if font_path:
            return ImageFont.truetype(font_path, size)
        return ImageFont.load_default()

    def fallback_font(size):
        for fp in fallback_font_paths:
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
        return font(size)

    def char_in_font(f, ch):
        """检测主字体是否支持该字符：getbbox 返回有效宽度就算支持"""
        try:
            bbox = f.getbbox(ch)
            return bbox is not None and bbox[2] > bbox[0]
        except Exception:
            return False

    def wrap_text(draw_obj, text, fnt, max_width):
        if not text:
            return []
        lines = []
        for para in text.split('\n'):
            line = ''
            for ch in para:
                test = line + ch
                bbox = draw_obj.textbbox((0, 0), test, font=fnt)
                if bbox[2] - bbox[0] > max_width:
                    lines.append(line)
                    line = ch
                else:
                    line = test
            if line:
                lines.append(line)
        return lines

    def draw_text_with_emoji(draw_obj, img_obj, text, fnt, emoji_map, x, y, max_width, line_height):
        segments = re.split(r'(\[[^\]]+\]|@\S+)', text)
        emoji_size = fnt.size
        fb_font = fallback_font(fnt.size)
        cur_x = x
        cur_y = y
        emoji_cache = {}
        for seg in segments:
            if not seg:
                continue
            if seg.startswith('@'):
                for ch in seg:
                    use_font = fnt if char_in_font(fnt, ch) else fb_font
                    bbox = draw_obj.textbbox((0, 0), ch, font=use_font)
                    w = bbox[2] - bbox[0]
                    if cur_x + w > x + max_width and cur_x > x:
                        cur_x = x
                        cur_y += line_height
                    draw_obj.text((cur_x, cur_y), ch, fill='#00AEEC', font=use_font)
                    cur_x += w
            elif seg.startswith('[') and seg.endswith(']') and seg in emoji_map:
                if seg not in emoji_cache:
                    em = _download_image(emoji_map[seg])
                    if em:
                        # 统一转 RGBA，确保透明度通道正确（B 站表情可能是 P 模式或无 alpha 的 RGB）
                        em = em.convert('RGBA')
                        em = em.resize((emoji_size, emoji_size), Image.LANCZOS)
                    emoji_cache[seg] = em
                em = emoji_cache[seg]
                if em:
                    if cur_x + emoji_size > x + max_width:
                        cur_x = x
                        cur_y += line_height
                    img_obj.paste(em, (cur_x, cur_y + (line_height - emoji_size) // 2), em)
                    cur_x += emoji_size
                else:
                    use_font = fnt if char_in_font(fnt, seg) else fb_font
                    bbox = draw_obj.textbbox((0, 0), seg, font=use_font)
                    w = bbox[2] - bbox[0]
                    if cur_x + w > x + max_width:
                        cur_x = x
                        cur_y += line_height
                    draw_obj.text((cur_x, cur_y), seg, fill='#18191C', font=use_font)
                    cur_x += w
            else:
                for ch in seg:
                    if ch == '\n':
                        cur_x = x
                        cur_y += line_height
                        continue
                    use_font = fnt if char_in_font(fnt, ch) else fb_font
                    bbox = draw_obj.textbbox((0, 0), ch, font=use_font)
                    w = bbox[2] - bbox[0]
                    if cur_x + w > x + max_width and cur_x > x:
                        cur_x = x
                        cur_y += line_height
                    draw_obj.text((cur_x, cur_y), ch, fill='#18191C', font=use_font)
                    cur_x += w
        return cur_y + line_height

    def draw_line_fallback(draw_obj, img_obj, text, fnt, fb_fnt, color, x, y, max_width, line_height):
        """逐字符绘制单行文字，主字体缺字时自动回退到 fallback 字体。
        返回绘制结束后的 y（含该行的 line_height），供下一行使用。"""
        cur_x = x
        for ch in text:
            use_font = fnt if char_in_font(fnt, ch) else fb_fnt
            bbox = draw_obj.textbbox((0, 0), ch, font=use_font)
            w = bbox[2] - bbox[0]
            if cur_x + w > x + max_width and cur_x > x:
                cur_x = x
                y += line_height
            draw_obj.text((cur_x, y), ch, fill=color, font=use_font)
            cur_x += w
        return y + line_height


    # ---- 提取消息内容 ----
    name = msg.get('作者', 'Unknown')
    face = msg.get('作者头像', '')
    pub_time_display = msg.get('发布时间', '')
    dyn_type = msg.get('动态类型', '')
    text_content = msg.get('配文', '')
    dyn_title = msg.get('动态标题', '')

    # 表情映射
    merged_emoji_map = dict(msg.get('动态表情', {}) or {})

    # 过滤无法识别的表情标签
    if merged_emoji_map:
        text_content = re.sub(r'\[[^\]]+\]', lambda m: m.group() if m.group() in merged_emoji_map else '', text_content)
    text_content = text_content.strip()

    # 动作描述
    action_text = '发布了动态'
    if dyn_type == 'DYNAMIC_TYPE_AV':
        action_text = '投稿了视频'
    elif dyn_type == 'DYNAMIC_TYPE_ARTICLE':
        action_text = '投稿了专栏'
    elif dyn_type == 'DYNAMIC_TYPE_FORWARD':
        action_text = '转发了动态'

    # 视频/图文信息
    is_video = dyn_type == 'DYNAMIC_TYPE_AV'
    video_title = msg.get('视频标题', '')
    video_desc = msg.get('视频简介', '')
    cover_url = msg.get('视频封面', '') if is_video else ''
    duration_text = msg.get('视频时长', '')
    pics = msg.get('图片列表', []) if not is_video else []

    # ---- 布局参数 ----
    SCALE = 2.0
    W = int(720 * SCALE)
    MARGIN = int(24 * SCALE)
    content_width = W - MARGIN * 2

    def s(v): return int(v * SCALE)

    name_font = font(s(24))
    name_fb_font = fallback_font(s(24))
    meta_font = font(s(18))
    meta_fb_font = fallback_font(s(18))
    body_font = font(s(24))
    mini_font = font(s(16))

    temp_draw = ImageDraw.Draw(Image.new('RGB', (10, 10), 'white'))

    avatar_size = s(48)
    avatar_img = _download_image(face) if face else None
    if avatar_img:
        avatar_img = avatar_img.resize((avatar_size, avatar_size), Image.LANCZOS)

    header_h = avatar_size + s(16)

    text_lines = wrap_text(temp_draw, text_content, body_font, content_width)
    text_h = len(text_lines) * s(36)

    title_lines = wrap_text(temp_draw, dyn_title, body_font, content_width) if dyn_title else []
    title_h = len(title_lines) * s(36) if title_lines else 0

    card_h = 0  # 最终卡片图片区域的总高度（计算用）
    card_type = 'none'   # 'video' | 'multi' | 'single' | 'none'
    card_data = {}       # 存下载好的图片 + 尺寸等

    if is_video:
        # 视频动态：始终渲染标题+简介，封面下载成功则显示在左侧
        card_type = 'video'
        card_cover_w = s(220)
        card_cover_h = 0
        video_cover = None
        if cover_url:
            video_cover = _download_image(cover_url)
        if video_cover:
            ratio = card_cover_w / video_cover.width
            card_cover_h = int(video_cover.height * ratio)
            video_cover = video_cover.resize((card_cover_w, card_cover_h), Image.LANCZOS)
        # 卡片高度：有封面用封面高度，没封面用最小高度
        card_h = max(card_cover_h, s(100))
        card_data = {
            'cover_img': video_cover, 'cover_w': card_cover_w, 'cover_h': card_cover_h,
        }
    elif pics:
        # 图文动态（可能多张）：九宫格布局，全部裁成正方形
        COLS = 3
        GAP = s(4)        # 格子间距
        BORDER_R = s(8)
        cell_side = (content_width - GAP * (COLS - 1)) // COLS  # 正方形边长

        # 下载所有图片
        all_imgs = []
        for url in pics:
            img = _download_image(url)
            if img:
                all_imgs.append(img)
        if not all_imgs:
            pass  # 没图就跳过
        elif len(all_imgs) == 1:
            # 单张：居中显示，不超过最大宽高
            card_type = 'single'
            ci = all_imgs[0]
            max_single_w = content_width
            max_single_h = s(400)
            ratio = min(max_single_w / ci.width, max_single_h / ci.height, 1.0)
            cw = int(ci.width * ratio)
            ch = int(ci.height * ratio)
            ci = ci.resize((cw, ch), Image.LANCZOS)
            card_data = {'imgs': [ci], 'rows': 1, 'cols': 1, 'cell_w': cw, 'cell_h': ch, 'gap': GAP, 'radius': BORDER_R}
            card_h = ch
        else:
            # 多张：3列九宫格，全部 cover 裁成正方形
            card_type = 'multi'
            imgs = all_imgs[:COLS * COLS]  # 最多显示 9 张
            rows = (len(imgs) + COLS - 1) // COLS
            norm_imgs = []
            for ci in imgs:
                src_ratio = ci.width / ci.height
                if src_ratio >= 1:
                    # 更宽或正方形 → 按高度缩放，裁左右
                    new_h = cell_side
                    new_w = int(new_h * src_ratio)
                    ci_r = ci.resize((new_w, new_h), Image.LANCZOS)
                    left = (new_w - cell_side) // 2
                    ci_r = ci_r.crop((left, 0, left + cell_side, new_h))
                else:
                    # 更高 → 按宽度缩放，裁上下
                    new_w = cell_side
                    new_h = int(new_w / src_ratio)
                    ci_r = ci.resize((new_w, new_h), Image.LANCZOS)
                    top = (new_h - cell_side) // 2
                    ci_r = ci_r.crop((0, top, new_w, top + cell_side))
                norm_imgs.append(ci_r)
            grid_h = rows * cell_side + (rows - 1) * GAP
            card_data = {
                'imgs': norm_imgs, 'rows': rows, 'cols': COLS,
                'cell_w': cell_side, 'cell_h': cell_side,
                'gap': GAP, 'radius': BORDER_R,
            }
            card_h = grid_h

    total_h = MARGIN + header_h + (title_h + 10 if title_h else 0) + (text_h + 16 if text_lines else 0) + (card_h + 16 if card_h else 0) + MARGIN
    # 纯文字无图动态：最小高度 500px，与视频卡片对齐
    if card_type == 'none':
        total_h = max(total_h, s(250))

    radius = s(20)
    # 先在白底 RGB 上绘制所有内容（paste RGB 图不会破坏 alpha）
    img = Image.new('RGB', (W, total_h), '#FFFFFF')
    draw = ImageDraw.Draw(img)
    y = MARGIN

    if avatar_img:
        circle_mask = Image.new('L', (avatar_size, avatar_size), 0)
        ImageDraw.Draw(circle_mask).ellipse([0, 0, avatar_size, avatar_size], fill=255)
        avatar_round = Image.new('RGBA', (avatar_size, avatar_size), (0, 0, 0, 0))
        avatar_round.paste(avatar_img, (0, 0), circle_mask)
        img.paste(avatar_round, (MARGIN, y), avatar_round)

    name_x = MARGIN + avatar_size + s(12)
    name_max_w = W - MARGIN - name_x
    draw_line_fallback(draw, img, name, name_font, name_fb_font, '#18191C', name_x, y + s(4), name_max_w, s(30))
    meta = f'{pub_time_display} · {action_text}'
    draw_line_fallback(draw, img, meta, meta_font, meta_fb_font, '#9499A0', name_x, y + s(34), name_max_w, s(24))
    y += header_h

    if dyn_title:
        title_font_draw = font(s(26))
        title_fb_font_draw = fallback_font(s(26))
        for line in title_lines:
            # 偏移画两次模拟加粗，颜色用深黑（不用蓝色）
            draw_line_fallback(draw, img, line, title_font_draw, title_fb_font_draw, '#18191C', MARGIN + 1, y + 1, content_width, 0)
            y = draw_line_fallback(draw, img, line, title_font_draw, title_fb_font_draw, '#18191C', MARGIN, y, content_width, s(34))
        y += s(2)

    if text_content:
        y = draw_text_with_emoji(draw, img, text_content, body_font, merged_emoji_map, MARGIN, y, content_width, s(36))
        y += s(10)

    if card_type == 'video':
        # 视频动态：左侧封面 + 右侧标题简介（封面可选）
        d = card_data
        card_bg_x = MARGIN
        card_bg_w = content_width
        draw.rounded_rectangle([card_bg_x, y, card_bg_x + card_bg_w, y + card_h], radius=s(8), fill='#F1F2F3')
        if d['cover_img']:
            # 有封面：贴封面 + 时长标签
            cover_mask = Image.new('L', (d['cover_w'], d['cover_h']), 0)
            ImageDraw.Draw(cover_mask).rounded_rectangle([0, 0, d['cover_w'], d['cover_h']], radius=s(6), fill=255)
            img.paste(d['cover_img'], (card_bg_x, y), cover_mask)
            if duration_text:
                tb = mini_font.getbbox(duration_text)
                tw = tb[2] - tb[0]
                th = tb[3] - tb[1]
                dur_x = card_bg_x + d['cover_w'] - tw - s(8)
                dur_y = y + d['cover_h'] - th - s(6)
                draw.text((dur_x + 1, dur_y + 1), duration_text, fill='#00000080', font=mini_font)
                draw.text((dur_x, dur_y), duration_text, fill='#FFFFFF', font=mini_font)
            text_x = card_bg_x + d['cover_w'] + s(14)
            text_area_w = card_bg_w - d['cover_w'] - s(14) - s(10)
        else:
            # 无封面：标题简介占满宽度
            text_x = card_bg_x + s(14)
            text_area_w = card_bg_w - s(28)
        title_font_card = font(s(24))
        title_fb_font_card = fallback_font(s(24))
        desc_font_card = font(s(16))
        desc_fb_font_card = fallback_font(s(16))
        title_all = wrap_text(temp_draw, video_title, title_font_card, text_area_w)
        title_line_count = 1 if len(title_all) <= 1 else 2
        title_lines = title_all[:title_line_count]
        desc_lines_raw = wrap_text(temp_draw, video_desc, desc_font_card, text_area_w)
        desc_line_count = 4 - title_line_count
        desc_lines = desc_lines_raw[:desc_line_count]
        if len(desc_lines_raw) > desc_line_count and desc_lines:
            desc_lines[-1] = desc_lines[-1][:-1] + '…'
        cy = y + s(4)
        for line in title_lines:
            # 偏移画两次模拟加粗，颜色用深黑
            draw_line_fallback(draw, img, line, title_font_card, title_fb_font_card, '#18191C', text_x + 1, cy + 1, text_area_w, 0)
            cy = draw_line_fallback(draw, img, line, title_font_card, title_fb_font_card, '#18191C', text_x, cy, text_area_w, s(32))
        if desc_lines:
            cy += s(6)
        for line in desc_lines:
            cy = draw_line_fallback(draw, img, line, desc_font_card, desc_fb_font_card, '#61666D', text_x, cy, text_area_w, s(22))
        y += card_h + s(16)

    elif card_type in ('single', 'multi'):
        # 图文动态：单图居中 or 多图网格
        d = card_data
        radius = d['radius']
        gap = d['gap']
        cell_w = d['cell_w']
        cell_h = d['cell_h']
        n = len(d['imgs'])
        for idx, ci in enumerate(d['imgs']):
            row = idx // d['cols']
            col = idx % d['cols']
            if d['cols'] == 1:
                # 单图：居中
                cx = MARGIN + (content_width - ci.width) // 2
                cy = y
            else:
                cx = MARGIN + col * (cell_w + gap)
                cy = y + row * (cell_h + gap)
            mask = Image.new('L', (ci.width, ci.height), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, ci.width, ci.height], radius=radius, fill=255)
            img.paste(ci, (cx, cy), mask)
        if n > 1:
            y += card_h + s(16)
        else:
            y += ci.height + s(16)

    # --- 圆角裁剪 + 高质量 JPEG ---
    # 用圆角 mask 把白底内容 paste 到透明 RGBA（只有 mask 区域可见）
    round_mask = Image.new('L', (W, total_h), 0)
    ImageDraw.Draw(round_mask).rounded_rectangle([(0, 0), (W - 1, total_h - 1)], radius=radius, fill=255)
    final = Image.new('RGBA', (W, total_h), (0, 0, 0, 0))
    final.paste(img.convert('RGBA'), (0, 0), round_mask)

    buf = io.BytesIO()
    final.convert('RGB').save(buf, format='JPEG', quality=95, subsampling=0, optimize=True)
    return buf.getvalue()


class DynamicPushPlugin(BasePlugin):
    """动态推送插件"""

    NAPCAT_URL = 'http://nekocha.ac000108.cn'
    NAPCAT_TOKEN = 'loerkn7b-fBPPm-2'

    def _napcat_post(self, group_id, endpoint: str, payload: dict) -> dict:
        import requests
        base_url = self.NAPCAT_URL.rstrip('/')
        url = f"{base_url}{endpoint}"
        headers = {'Content-Type': 'application/json'}
        if self.NAPCAT_TOKEN:
            headers['Authorization'] = f'Bearer {self.NAPCAT_TOKEN}'
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
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def _send_segments(self, group_id, segments: list) -> dict:
        """发送消息片段列表（可混合 @全体、文字、图片、链接）"""
        return self._napcat_post(group_id, '/send_group_msg', {'message': segments})

    def _build_dynamic_url(self, message: dict) -> str:
        # 优先用服务端已构造好的新版协议链接
        return message.get('动态链接', '')

    def process_message(self, message: dict):
        if message.get('消息类型') != '动态':
            return

        group_id = str(self._config.get('QQ群号', '')).strip()
        if not group_id:
            return
        try:
            group_id = int(group_id)
        except (ValueError, TypeError):
            return

        send_text = self._config.get('发送文字信息', True)
        notice_text = self._config.get('提醒文字', '新动态来了')
        at_all = self._config.get('@全体成员', False)
        send_card = self._config.get('发送图片卡片', True)
        send_link = self._config.get('发送动态链接', True)
        dyn_url = self._build_dynamic_url(message) if send_link else ''

        # 第一条：提醒（开关控制，含@全体）
        if send_text:
            try:
                segments = []
                if at_all:
                    segments.append({'type': 'at', 'data': {'qq': 'all'}})
                    segments.append({'type': 'text', 'data': {'text': ' ' + notice_text}})
                else:
                    segments.append({'type': 'text', 'data': {'text': notice_text}})
                self._send_segments(group_id, segments)
                time.sleep(0.5)
            except Exception as e:
                print(f"[{self.name}] 发送提醒失败: {e}")

        # 第二条：图片卡片
        if send_card:
            try:
                card_bytes = draw_dynamic_card(message)
                if card_bytes:
                    import base64
                    b64 = base64.b64encode(card_bytes).decode('utf-8')
                    self._send_segments(group_id, [
                        {'type': 'image', 'data': {'file': f'base64://{b64}'}},
                    ])
                    time.sleep(0.5)
            except Exception as e:
                print(f"[{self.name}] 生成卡片失败: {e}")

        # 第三条：动态链接
        if dyn_url:
            try:
                self._send_segments(group_id, [
                    {'type': 'text', 'data': {'text': dyn_url}},
                ])
            except Exception as e:
                print(f"[{self.name}] 发送链接失败: {e}")
