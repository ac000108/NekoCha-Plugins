# -*- coding: utf-8 -*-
"""
netease_api —— 纯 Python 网易云音乐 + 多端融合 API
====================================================

零 Node 依赖，零 pip 依赖。
加密算法 + 多端直链全部在 _eapi.py / _multi.py 内部实现。

接口（与原 Node 版本一一对应，上层业务逻辑完全复用）：
  网易云：search / get_play_url / get_lyric / get_play_urls / get_song_detail
  多端降级：merge_search / multi_play_url / unblock_url（= bodian）
  综合入口：search_and_play / _fallback_multi

解灰策略（与 Node 版完全相同）：
  1. VIP/灰歌（fee=1 且 freeTrialInfo 非空）→ unblock_url(id) 先试网易云 song detail 补全元数据 → bodian 匹配酷我完整版
  2. 网易云搜不到 → merge_search 并发酷狗/酷我/QQ → multi_play_url 拿直链
"""
import json
import logging
import re
import time
import urllib.parse
import urllib.request

from ._eapi import (
    eapi_search, eapi_song_url, eapi_song_url_batch,
    eapi_song_detail, eapi_lyric,
)
from ._multi import (
    merge_search, multi_play_url, unblock_by_bodian,
    kugou_search, kuwo_search, qq_search,  # 暴露给外部直接用
)

logger = logging.getLogger(__name__)


# ============ 网易云 API 封装（替换原 Node HTTP 调用）============

def _nc_get(url, params=None, timeout=10):
    """纯 urllib GET（备用，仅用于明文测试）"""
    qs = urllib.parse.urlencode(params or {})
    full = f'{url}?{qs}' if qs else url
    req = urllib.request.Request(full, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) NeteaseMusicDesktop/3.0.0',
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ============ 对外接口（签名与 Node 版完全相同）============

def search(keyword, limit=5):
    """
    搜索歌曲（weapi 加密）。
    返回: [{'platform','id','name','artists','artist','album','cover','duration','fee','alia','tns'}, ...]
    """
    d = eapi_search(keyword, limit=limit)
    songs = (d.get('result') or {}).get('songs') or []
    out = []
    for s in songs:
        # 兼容两种返回结构（weapi 返回 ar/al/dt，也可能有 artists/album/duration）
        artists = s.get('ar') or s.get('artists') or []
        album_obj = s.get('al') or s.get('album') or {}
        dur_ms = s.get('dt') if s.get('dt') is not None else s.get('duration', 0)
        out.append({
            'platform': 'netease',
            'id': s['id'],
            'name': s['name'],
            'artists': [a['name'] for a in artists if a.get('name')],
            'artist': ', '.join(a['name'] for a in artists if a.get('name')),
            'album': album_obj.get('name', ''),
            'cover': album_obj.get('picUrl', '') or '',
            'duration': (dur_ms or 0) // 1000,
            'fee': s.get('fee', -1),          # 0/8 免费, 1 VIP, 4 付费专辑
            'alia': s.get('alia') or s.get('alias') or [],
            'tns': s.get('tns') or [],
        })
    return out


def get_play_url(song_id, level='exhigh'):
    """
    播放直链。level: standard / higher / exhigh / lossless / hires ...
    返回: {'url', 'br'(kbps), 'fee', 'type', 'freeTrialInfo'} 或 {}
    注意: 免费歌 fee=0/8 拿完整 CDN；VIP 歌 fee=1 拿 45 秒试听片段
    """
    d = eapi_song_url(song_id, level=level)
    items = d.get('data') or []
    if not items:
        return {}
    item = items[0] if isinstance(items, list) else items
    url = item.get('url', '') or ''
    if url.startswith('http://'):
        url = 'https://' + url[7:]
    logger.info(f'[eapi] play id={song_id} fee={item.get("fee")} br={item.get("br")} url={url[:80]}')
    if not url:
        return {}
    return {
        'url': url,
        'br': (item.get('br') or 0) // 1000,
        'fee': item.get('fee', -1),
        'type': item.get('type', ''),
        'freeTrialInfo': item.get('freeTrialInfo'),
    }


def get_lyric(song_id):
    """歌词：lrc + tlyric + yrc（优先逐字 yrc）"""
    return eapi_lyric(song_id)


def get_play_urls(song_ids, level='exhigh'):
    """批量直链（验证多首歌真实可播性）"""
    ids = [int(x) for x in song_ids if x][:4]
    if not ids:
        return {}
    d = eapi_song_url_batch(ids, level=level)
    out = {}
    for item in (d.get('data') or []):
        sid = item.get('id')
        if sid is None:
            continue
        url = item.get('url', '') or ''
        if url.startswith('http://'):
            url = 'https://' + url[7:]
        out[sid] = {
            'url': url,
            'br': (item.get('br') or 0) // 1000,
            'fee': item.get('fee', -1),
            'type': item.get('type', ''),
            'freeTrialInfo': item.get('freeTrialInfo'),
        }
    logger.info(f'[eapi] batch play ids={ids} → 可播 {[i for i in ids if out.get(i, {}).get("url")]}')
    return out


def get_song_detail(song_id):
    """歌曲详情：旧版 search 没有封面，用 song detail 补全。"""
    try:
        d = eapi_song_detail(song_id)
        songs = d.get('songs') or []
        if not songs:
            return {}
        s = songs[0]
        artists = s.get('ar') or s.get('artists') or []
        album_obj = s.get('al') or s.get('album') or {}
        return {
            'name': s.get('name', ''),
            'artist': ', '.join(a.get('name', '') for a in artists if a.get('name')),
            'album': album_obj.get('name', ''),
            'cover': album_obj.get('picUrl', '') or '',
        }
    except Exception as e:
        logger.debug(f'[eapi] song/detail {song_id} 失败: {e}')
        return {}


def unblock_url(song_id, timeout=25):
    """
    解灰：给定网易云 ID → 先调 song detail 拿歌名/歌手/时长 → bodian 匹配酷我完整版。
    与 Node 版 /song/url/match 等价（同走 UnblockNeteaseMusic 逻辑链）。
    成功返回 https 直链，失败返回 None。
    """
    # 1. 拿网易云元数据
    detail = get_song_detail(song_id)
    name = detail.get('name', '')
    artist = detail.get('artist', '')
    duration = 0
    # 用 search 补 duration（detail 不返回时长）
    if not duration:
        sr = search(name, limit=1)
        if sr:
            duration = sr[0].get('duration', 0)

    if not name:
        logger.warning(f'[unblock] song_id={song_id} 拿不到歌名，放弃')
        return None

    # 2. 直接 bodian 匹配
    url = unblock_by_bodian(name, artist, duration)
    if url:
        logger.info(f'[unblock] 🔓 song_id={song_id} → bodian 命中 host={urllib.parse.urlparse(url).netloc}')
        return url
    logger.info(f'[unblock] song_id={song_id} bodian 失败')
    return None


# ============ 多端融合降级（直接引用 _multi.py）============
# merge_search / multi_play_url / unblock_by_bodian 已在 _multi.py 定义
# 这里只是做 alias，保持与原 Node API 调用链一致


# ============ 搜索结果智能排序 ============
_BRACKET_RE = re.compile(r'[\(（\[【][^\)）\]】]*[\)）\]】]')
_BRACKET_INNER_RE = re.compile(r'[\(（\[【]([^\)）\]】]*)[\)）\]】]')
_NONWORD_RE = re.compile(r'[\s\-—_·.,，。、!！?？&×:：;；/\\]+')

_BAD_TAGS = [
    '伴奏', 'instrumental', 'karaoke', '纯音乐', '钢琴', '钢琴曲', '八音盒',
    'guitar', '吉他版', 'cover', '翻唱', '女声版', '男声版', '童声',
    'dj', 'remix', '混音', '重制', 'live', '现场', '演唱会', 'acoustic',
    '不插电', '铃声', '片段', '剪辑版', 'demo', 'midi', '儿童版', '少儿版',
    '萨克斯', '口琴', '古筝', '二胡', '轻音乐', 'version', 'edit', '小合唱',
]


def _norm(s):
    return _NONWORD_RE.sub('', (s or '')).lower()


def _main_title(name):
    return _BRACKET_RE.sub('', name or '').strip()


def _parse_query(keyword):
    """拆 "歌名 歌手" / "歌名 - 歌手" → (title, [hints])"""
    q = (keyword or '').strip()
    hints = []
    m = re.split(r'\s+[-–—]{1,2}\s+', q, maxsplit=1)
    if len(m) == 2 and 0 < len(m[1]) <= 12:
        title, hint = m
        hints.append(hint)
    else:
        title = q
    for tok in re.split(r'\s+', q):
        if len(tok) >= 2 and tok not in hints:
            hints.append(tok)
    return title, hints


def _score_song(idx, s, q_title, q_hints, q_raw):
    """单条结果打分（权重同 Node 版）"""
    score = -idx * 3.0
    tags = []

    name = s.get('name', '')
    main = _main_title(name)
    n_main, n_full, n_q = _norm(main), _norm(name), _norm(q_title)
    artist_str = (s.get('artist') or '').lower()
    lower_full = n_full + ' ' + artist_str

    # 1) 歌名匹配
    n_variants = [n_q] + [_norm(h) for h in q_hints]
    if n_main and n_main in n_variants:
        score += 120; tags.append('歌名精确')
    elif n_q and (n_q in n_full or n_full in n_q):
        score += 40; tags.append('歌名包含')
    for alias in (s.get('alia') or []) + (s.get('tns') or []):
        na = _norm(alias)
        if na and na in n_variants:
            score += 100; tags.append('别名精确')
            break

    # 2) 歌手提示词
    for hint in q_hints:
        h = _norm(hint)
        if not h or h == n_main:
            continue
        if h in _norm(artist_str):
            score += 50; tags.append(f'歌手:{hint}')
        elif len(q_hints) > 1 and h in n_full:
            score += 50; tags.append(f'歌名带歌手:{hint}')

    # 3) 非原版标签降权
    q_lower = (q_raw or '').lower()
    for bad in _BAD_TAGS:
        if bad in lower_full and bad not in q_lower:
            score -= 45; tags.append(f'降权:{bad}')
    for inner in _BRACKET_INNER_RE.findall(name):
        il = inner.lower()
        is_variant = ('版' in inner or '改编' in inner or '变速' in inner
                      or re.search(r'0?\.\d+x|1\.\d+x', il))
        if is_variant and il not in q_lower:
            score -= 45; tags.append(f'降权括号:{inner[:10]}')

    # 4) 时长合理性
    dur = s.get('duration', 0)
    if dur:
        if 100 <= dur <= 600:
            score += 8
        else:
            score -= 35; tags.append(f'时长异常:{dur}s')

    # 5) 版权费
    fee = s.get('fee', -1)
    if fee == 1:
        score += 6; tags.append('VIP完整版优先')
    elif fee == 8:
        score -= 8; tags.append('免费剪辑版降权')
    elif fee == 0:
        score += 2
    elif fee == 4:
        score -= 8

    return score, tags


def _fallback_multi(keyword):
    """网易云全灭 → 多端融合降级（酷狗/酷我/QQ）"""
    q_title, q_hints = _parse_query(keyword)
    candidates = merge_search(keyword, limit=15)
    if not candidates:
        return {"ok": False, "msg": f"多端也搜不到: {keyword}"}

    logger.info(f'[fallback] merge_search 返回 {len(candidates)} 条')

    normalized = []
    for i, c in enumerate(candidates):
        s = {
            'platform': c.get('platform', 'unknown'),
            'id': c.get('id'),
            'name': c.get('name', ''),
            'artist': c.get('artist', ''),
            'duration': c.get('duration', 0),
            'fee': c.get('fee', 0),
            'album': c.get('album', ''),
            'cover': c.get('cover', ''),
            'extra': c.get('extra', {}),
        }
        normalized.append(s)

    ranked = sorted(
        ((_score_song(i, s, q_title, q_hints, keyword), s) for i, s in enumerate(normalized)),
        key=lambda x: x[0][0], reverse=True
    )

    logger.info('[fallback] 多端候选排序:')
    for (sc, why), s in ranked[:5]:
        logger.info(f'    {sc:6.0f} | [{s["platform"]}] {s["name"][:24]} - {s["artist"][:18]} '
                    f'{s.get("duration",0)}s [{" ".join(why)}]')

    pool = [x for x in ranked if not x[1].get('duration') or x[1]['duration'] >= 60] or ranked

    pick = None
    for (_, why), s in pool[:4]:
        url = multi_play_url(s['name'], s.get('artist', ''), s.get('duration', 0))
        if url:
            pick = (s, url)
            logger.info(f'[fallback] ✅ 多端直链命中: [{s["platform"]}] {s["name"]} - {s["artist"]}')
            break
        else:
            logger.info(f'[fallback] {s["platform"]} {s["name"]} 直链失败，试下一条')

    if not pick:
        return {"ok": False, "msg": f"多端搜到 {len(candidates)} 条但直链全灭: {keyword}"}

    s, url = pick

    lrc, tlyric, yrc = '', '', ''
    lyric_netease = search(f"{s['name']} {s.get('artist', '')}", limit=1)
    if lyric_netease:
        lyric_src = lyric_netease[0]
        logger.info(f'[fallback] 歌词来源: 网易云 id={lyric_src["id"]} ({lyric_src["name"]} - {lyric_src.get("artist","")})')
        lyric = get_lyric(lyric_src['id'])
        lrc, tlyric, yrc = lyric.get('lrc', ''), lyric.get('tlyric', ''), lyric.get('yrc', '')
    else:
        logger.info('[fallback] 网易云也搜不到歌词源 → 无歌词')

    logger.info(f'[_fallback_multi] ✅ [{s["platform"]}] {s["name"]} - {s.get("artist","")} '
                f'(多端降级完整 FLAC)')

    return {
        "ok": True,
        "song": {
            "netease_id": None,
            "id": s['id'],
            "platform": s['platform'],
            "name": s['name'],
            "artist": s.get('artist', ''),
            "album": s.get('album', ''),
            "cover": s.get('cover', ''),
            "url": url,
            "fee": s.get('fee', -1),
            "type": 'flac',
            "br": 0,
            "is_trial": False,
            "unblocked": True,
            "from_fallback": True,
            "lrc": lrc,
            "tlyric": tlyric,
            "yrc": yrc,
        }
    }


def search_and_play(keyword):
    """
    一键点歌：网易云优先 → 多端融合降级。
    策略（与 Node 版完全相同）：
      1. 网易云搜 15 条，打分排序
      2. 前 4 候选先试官方完整直链 → VIP/下架用 bodian 解灰 → 试听兜底
      3. 网易云搜不到 → merge_search 并发酷狗/酷我/QQ → multi_play_url 拿直链
    """
    results = search(keyword, limit=15)

    if not results:
        return _fallback_multi(keyword)

    q_title, q_hints = _parse_query(keyword)
    ranked = sorted(
        ((_score_song(i, s, q_title, q_hints, keyword), s) for i, s in enumerate(results)),
        key=lambda x: x[0][0], reverse=True
    )

    logger.info(f'[search] 关键词={keyword!r} 候选排序:')
    for (sc, why), s in ranked[:6]:
        logger.info(f'    {sc:6.0f} | {s["name"][:28]} - {s["artist"][:20]} '
                    f'fee={s.get("fee")} {s.get("duration")}s [{" ".join(why)}]')

    top = ranked[:4]
    try:
        urls = get_play_urls([s['id'] for _, s in top])
    except Exception as e:
        logger.warning(f'[search] 批量直链失败，退回逐首验证: {e}')
        urls = {}

    is_trial = False
    unblocked = False
    pick = None

    pool = [x for x in top if not x[1].get('duration') or x[1]['duration'] >= 60] or top

    def _full_official(song):
        u = urls.get(song['id'])
        return u if u and u.get('url') and not u.get('freeTrialInfo') else None

    if urls:
        # 第一轮：官方完整直链 → 解灰完整版
        for (_, why), s in pool:
            u = _full_official(s)
            if u:
                pick = (s, u)
                break
            ub = unblock_url(s['id'])
            if ub:
                pick = (s, {
                    'url': ub,
                    'br': 0,
                    'fee': (urls.get(s['id']) or {}).get('fee', s.get('fee', -1)),
                    'type': 'flac',
                    'freeTrialInfo': None,
                })
                unblocked = True
                logger.info(f'[search] 🔓 解灰完整版命中: {s["name"]} - {s["artist"]} [{" ".join(why)}]')
                break

        # 第二轮：完整版全灭 → 试听
        if not pick:
            for (_, _why), s in pool:
                u = urls.get(s['id'])
                if u and u.get('url') and u.get('freeTrialInfo'):
                    pick = (s, u)
                    is_trial = True
                    logger.warning(f'[search] ⚠️ 解灰也失败，使用试听版（45s）: {s["name"]} - {s["artist"]}')
                    break

    if not pick:
        s = pool[0][1]
        try:
            play = get_play_url(s['id'], level='exhigh')
        except Exception as e:
            return {"ok": False, "msg": str(e)}
        if play.get('url') and not play.get('freeTrialInfo'):
            pick = (s, play)
            if '126.net' not in urllib.parse.urlparse(play['url']).netloc:
                unblocked = True
                logger.info(f'[search] 🔓 解灰命中: {s["name"]} - {s["artist"]}')
        elif play.get('url'):
            ub = unblock_url(s['id'])
            if ub:
                pick = (s, {
                    'url': ub, 'br': 0, 'fee': play.get('fee', -1),
                    'type': 'flac', 'freeTrialInfo': None,
                })
                unblocked = True
            else:
                pick = (s, play)
                is_trial = True
                logger.warning(f'[search] ⚠️ 解灰失败，使用试听版（45s）: {s["name"]} - {s["artist"]}')

    if not pick:
        logger.warning('[search] 网易云全灭 → 降级多端融合搜索')
        fallback = _fallback_multi(keyword)
        if fallback:
            return fallback
        return {"ok": False, "msg": f"无直链（前 4 首均不可播且解灰失败）: {pool[0][1]['name']}"}

    s, play = pick

    sid = s['id']
    lyric = get_lyric(sid)
    fee = play.get('fee', -1)

    if not s.get('cover'):
        detail = get_song_detail(sid)
        if detail.get('cover'):
            s['cover'] = detail['cover']

    logger.info(f'[search_and_play] ✅ {s["name"]} - {s["artist"]} '
                f'fee={fee} trial={is_trial} unblocked={unblocked}')

    return {
        "ok": True,
        "song": {
            "netease_id": sid,
            "id": sid,
            "name": s['name'],
            "artist": s['artist'],
            "album": s['album'],
            "cover": s['cover'],
            "url": play['url'],
            "fee": fee,
            "type": play.get('type', ''),
            "br": play.get('br', 0),
            "is_trial": is_trial,
            "unblocked": unblocked,
            "lrc": lyric['lrc'],
            "tlyric": lyric['tlyric'],
            "yrc": lyric['yrc'],
        }
    }
