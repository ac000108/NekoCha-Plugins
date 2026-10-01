# -*- coding: utf-8 -*-
"""
netease_api —— 纯 Node API 封装（网易云音乐）
===============================================

架构:  所有请求 → Node API（localhost:3000，插件启动时自动拉起）
       Node API 增强版 v4.40.1，eapi 加密 + 匿名 token + 解灰
       依赖: 标准库（urllib.request），不需要 curl_cffi / requests

接口:
  搜索:   /search?keywords=X&limit=N
  直链:   /song/url/v1?id=X&level=exhigh
  解灰:   /song/url/match?id=X（VIP/灰歌用网易云ID去酷狗/波点/酷我/B站匹配完整音频）
  歌词:   /lyric?id=X
"""
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

# 默认 localhost:3000（插件启动时自动拉起）
# 可通过环境变量 NODE_API_URL 覆盖（远程服务器部署）
NODE_API_BASE = os.environ.get('NODE_API_URL', 'http://127.0.0.1:3000')

def _check_node_api(timeout=2):
    """检测 Node API 是否响应（每次都尝试，不永久缓存 False）"""
    try:
        urllib.request.urlopen(
            f"{NODE_API_BASE}/search?keywords=test&limit=1", timeout=timeout
        )
        logger.info(f'[node-api] ✅ 可用 ({NODE_API_BASE})')
        return True
    except Exception as e:
        logger.info(f'[node-api] ❌ 不可用: {e}')
        return False


def _require_node_api(max_wait=30):
    """必须可用，否则最多等 max_wait 秒（Node API 冷启动需要时间）"""
    # 先快速 ping 一次
    if _check_node_api(timeout=2):
        return True
    # 冷启动等待（弹幕先到但 Node API 还在拉的情况）
    logger.warning(f'[node-api] 等待 Node API 启动（最多 {max_wait}s）...')
    for i in range(max_wait):
        time.sleep(1)
        if _check_node_api(timeout=2):
            logger.info(f'[node-api] ✅ 等待后可用（等了 {i+1}s）')
            return True
    raise RuntimeError(
        f'Node API 启动超时（{max_wait}s）！'
        f'请检查 plugins/netease_player/node_runtime/node_api.log'
    )


def _node_get(path, params, timeout=5):
    """统一 Node API GET（URL encode 中文参数）"""
    _require_node_api()
    qs = urllib.parse.urlencode(params)
    url = f"{NODE_API_BASE}{path}?{qs}"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


# ============ 对外接口 ============

def search(keyword, limit=5):
    """
    搜索歌曲（纯网易云，Node API eapi 加密）
    返回: [{'id', 'name', 'artist', 'album', 'cover', 'duration', ...}]
    """
    d = _node_get('/search', {'keywords': keyword, 'limit': limit})
    songs = (d.get('result') or {}).get('songs') or []
    out = []
    for s in songs:
        # 兼容两种返回结构：
        #   eapi 新版: ar[] / al{} / dt / alia
        #   weapi 旧版（增强版 Node API 实际返回）: artists[] / album{} / duration / alias
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
    播放直链（Node API eapi 加密）
    level: standard / higher / exhigh / lossless / hires / jyeffect / sky / jymaster
    返回: {'url', 'br'(kbps), 'fee', 'type'} 或 {}
    注意: 免费歌 fee=0/8 拿完整 CDN；VIP 歌 fee=1 拿 45 秒试听片段
    """
    d = _node_get('/song/url/v1', {'id': int(song_id), 'level': level})
    items = d.get('data') or []
    if not items:
        return {}
    item = items[0] if isinstance(items, list) else items
    url = item.get('url', '') or ''
    if url.startswith('http://'):
        url = 'https://' + url[7:]
    logger.info(f'[node-api] play id={song_id} fee={item.get("fee")} br={item.get("br")} url={url[:80]}')
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
    """
    歌词（Node API）
    /lyric → lrc + tlyric + 可能有旧格式 yrc
    /lyric/new → 新格式 yrc（逐字歌词，部分歌曲才有）
    返回: {'lrc', 'tlyric', 'yrc'}
    """
    # 基础歌词（lrc + tlyric + 可能的旧 yrc）
    d = _node_get('/lyric', {'id': int(song_id)})
    lrc = (d.get('lrc') or {}).get('lyric', '')
    yrc_old = (d.get('yrc') or {}).get('lyric', '')
    tlyric = (d.get('tlyric') or {}).get('lyric', '')

    # 逐字歌词（/lyric/new 专门返回 yrc，旧接口经常 yrc=0b）
    yrc_new = ''
    try:
        d2 = _node_get('/lyric/new', {'id': int(song_id)})
        yrc_new = (d2.get('yrc') or {}).get('lyric', '')
    except Exception as e:
        logger.debug(f'[node-api] /lyric/new 失败: {e}')

    # 优先用新接口的 yrc（更全），没有再回退旧接口
    yrc = yrc_new if yrc_new else yrc_old

    logger.info(f'[node-api] lyric id={song_id} lrc={len(lrc)}b yrc={len(yrc)}b(new={len(yrc_new)}b,old={len(yrc_old)}b)')
    return {'lrc': lrc, 'tlyric': tlyric, 'yrc': yrc}


def get_play_urls(song_ids, level='exhigh'):
    """
    批量直链（一次请求验证多首歌的真实可播性）
    /song/url/v1 的 id 支持逗号分隔。返回 {song_id: play_dict}
    """
    ids = [int(x) for x in song_ids if x][:4]
    if not ids:
        return {}
    d = _node_get('/song/url/v1', {'id': ','.join(map(str, ids)), 'level': level}, timeout=8)
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
    logger.info(f'[node-api] batch play ids={ids} → 可播 {[i for i in ids if out.get(i, {}).get("url")]}')
    return out


def get_song_detail(song_id):
    """歌曲详情：旧版 /search 没有封面 URL，用 /song/detail 补全。失败返回 {}。"""
    try:
        d = _node_get('/song/detail', {'ids': int(song_id)}, timeout=5)
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
        logger.debug(f'[node-api] song/detail {song_id} 失败: {e}')
        return {}


def unblock_url(song_id, timeout=25):
    """
    解灰：/song/url/match 用网易云歌曲ID去第三方音源（酷狗>波点>酷我>B站）
    匹配同一首歌的完整音频。成功返回 https 直链，失败返回 None。
    注意：该接口成功时 data 是 URL 字符串（不是对象），失败时 code=500。
    """
    try:
        d = _node_get('/song/url/match', {'id': int(song_id)}, timeout=timeout)
    except Exception as e:
        logger.warning(f'[node-api] 解灰请求异常 id={song_id}: {e}')
        return None
    url = d.get('data')
    if d.get('code') == 200 and isinstance(url, str) and url.startswith('http'):
        if url.startswith('http://'):
            url = 'https://' + url[7:]
        logger.info(f'[node-api] 🔓 解灰成功 id={song_id} host={urllib.parse.urlparse(url).netloc}')
        return url
    logger.info(f'[node-api] 解灰失败 id={song_id} msg={d.get("msg") or d.get("message")}')
    return None


# ============ 多端融合降级 ============
def merge_search(keyword, limit=15, platforms='kugou,kuwo,qq'):
    """
    多端融合搜索（网易云搜不到时的降级路径）。
    调 Node API 的 /merge/search，并发打酷狗/酷我/QQ 公开搜索 API。
    返回: [{'platform', 'id', 'name', 'artist', 'duration', 'fee', 'extra'}]
    """
    try:
        d = _node_get('/merge/search', {
            'keyword': keyword, 'limit': limit, 'platforms': platforms,
        }, timeout=8)
    except Exception as e:
        logger.warning(f'[node-api] merge/search 失败: {e}')
        return []
    return d.get('data') or []


def multi_play_url(name, artist='', duration=0, timeout=20):
    """
    多端统一直链：走 Node API 的 /multi/url（内部用 bodian provider 匹配）。
    成功返回 https 直链，失败返回 None。
    """
    try:
        d = _node_get('/multi/url', {
            'name': name, 'artist': artist, 'duration': duration,
        }, timeout=timeout)
    except Exception as e:
        logger.warning(f'[node-api] multi/url 失败: {e}')
        return None
    url = d.get('url')
    if d.get('code') == 200 and isinstance(url, str) and url.startswith('http'):
        return url
    return None


def _fallback_multi(keyword):
    """
    策略 C 降级：网易云全灭 → 多端融合搜索 → 多端统一直链。
    返回和 search_and_play 相同结构的 dict（ok=True/False, song={...}）。
    """
    q_title, q_hints = _parse_query(keyword)
    candidates = merge_search(keyword, limit=15)
    if not candidates:
        return {"ok": False, "msg": f"多端也搜不到: {keyword}"}

    logger.info(f'[fallback] merge_search 返回 {len(candidates)} 条')

    # 统一格式（让多端结果能喂给 _score_song）
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

    # 复用现有打分逻辑
    ranked = sorted(
        ((_score_song(i, s, q_title, q_hints, keyword), s) for i, s in enumerate(normalized)),
        key=lambda x: x[0][0], reverse=True
    )

    logger.info('[fallback] 多端候选排序:')
    for (sc, why), s in ranked[:5]:
        logger.info(f'    {sc:6.0f} | [{s["platform"]}] {s["name"][:24]} - {s["artist"][:18]} '
                    f'{s.get("duration",0)}s [{" ".join(why)}]')

    # 硬门槛：短于 60s 的基本是片段
    pool = [x for x in ranked if not x[1].get('duration') or x[1]['duration'] >= 60] or ranked

    # 逐首调 multi_play_url 拿直链（bodian provider）
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

    # 歌词：非网易云源 → 用歌名+歌手搜网易云 lyric（同歌名可能在网易云有元数据）
    # 先尝试用网易云搜索找同名歌曲 ID 拿歌词
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


# ============ 搜索结果智能排序 ============
_BRACKET_RE = re.compile(r'[\(（\[【][^\)）\]】]*[\)）\]】]')
_BRACKET_INNER_RE = re.compile(r'[\(（\[【]([^\)）\]】]*)[\)）\]】]')
_NONWORD_RE = re.compile(r'[\s\-—_·.,，。、!！?？&×:：;；/\\]+')

# 用户没有主动提及时要降权的"非原版"标签（命中即扣分）
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
    """
    拆 "歌名 歌手" / "歌名 - 歌手"，返回 (title, [歌手提示词])
    """
    q = (keyword or '').strip()
    hints = []
    m = re.split(r'\s+[-–—]{1,2}\s+', q, maxsplit=1)
    if len(m) == 2 and 0 < len(m[1]) <= 12:
        title, hint = m
        hints.append(hint)
    else:
        title = q
    # 空格分词：每段都可能是歌手名（"晴天 周杰伦"），用于打分命中
    for tok in re.split(r'\s+', q):
        if len(tok) >= 2 and tok not in hints:
            hints.append(tok)
    return title, hints


def _score_song(idx, s, q_title, q_hints, q_raw):
    """
    给单条搜索结果打分。idx = 网易云默认排序（0 最好），作为兜底基准。
    q_raw = 用户原始关键词（小写），用于判断"非原版"标签是不是用户主动要的
    """
    score = -idx * 3.0
    tags = []

    name = s.get('name', '')
    main = _main_title(name)
    n_main, n_full, n_q = _norm(main), _norm(name), _norm(q_title)
    artist_str = (s.get('artist') or '').lower()
    lower_full = n_full + ' ' + artist_str

    # 1) 歌名匹配（权重最高）
    # 整体词和每个分词都尝试精确匹配——覆盖 "晴天 周杰伦" 这种"歌名 歌手"空格格式
    n_variants = [n_q] + [_norm(h) for h in q_hints]
    if n_main and n_main in n_variants:
        score += 120; tags.append('歌名精确')
    elif n_q and (n_q in n_full or n_full in n_q):
        score += 40; tags.append('歌名包含')
    # 别名/译名命中（很多外语歌搜中文名只有别名能对上）
    for alias in (s.get('alia') or []) + (s.get('tns') or []):
        na = _norm(alias)
        if na and na in n_variants:
            score += 100; tags.append('别名精确')
            break

    # 2) 歌手提示词命中
    # 多词查询（"晴天 周杰伦"）：分词若等于该候选歌名本体，它就是标题而非歌手，跳过
    for hint in q_hints:
        h = _norm(hint)
        if not h or h == n_main:
            continue
        if h in _norm(artist_str):
            score += 50; tags.append(f'歌手:{hint}')
        elif len(q_hints) > 1 and h in n_full:
            # 歌名括号里带歌手名，如 "晴天 (原唱 周杰伦)" —— 官方版下架时这是最接近的版本
            score += 50; tags.append(f'歌名带歌手:{hint}')

    # 3) "非原版"标签降权（用户关键词里自己带了就不罚）
    q_lower = (q_raw or '').lower()
    for bad in _BAD_TAGS:
        if bad in lower_full and bad not in q_lower:
            score -= 45; tags.append(f'降权:{bad}')
    # 通用规则：括号注释里出现"xx版/xx改编/变速"等一律视为非原版（深情版/R&B版/抖音热搜版…）
    for inner in _BRACKET_INNER_RE.findall(name):
        il = inner.lower()
        is_variant = ('版' in inner or '改编' in inner or '变速' in inner
                      or re.search(r'0?\.\d+x|1\.\d+x', il))
        if is_variant and il not in q_lower:
            score -= 45; tags.append(f'降权括号:{inner[:10]}')

    # 4) 时长合理性：100s~10min 像完整歌曲，过短多半是片段/剪辑（流行歌极少短于 100s）
    dur = s.get('duration', 0)
    if dur:
        if 100 <= dur <= 600:
            score += 8
        else:
            score -= 35; tags.append(f'时长异常:{dur}s')

    # 5) 版权费：fee=1(VIP完整版)优先于fee=8(免费剪辑版)
    # VIP完整版通常有完整元数据(作词/作曲)和准确时间轴，免费版经常是电台剪辑/重新上传
    # 但 VIP 歌需要解灰才能播，所以这里只微调打分（差 14 分刚好让 VIP 完整版排前）
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


def search_and_play(keyword):
    """
    一键点歌：网易云优先 → 多端融合降级（酷狗/酷我/QQ）
    策略 C：
      1. 网易云搜 15 条，打分排序，前 4 候选先试官方完整直链 → 解灰完整 → 试听兜底
      2. 网易云搜不到 / 前 4 全灭 → 调 merge_search 并发搜酷狗/酷我/QQ
      3. 多端候选统一格式，复用现有 _score_song 打分，用 multi_play_url 拿直链
      4. 非网易云源的歌词仍尝试走网易云 lyric 接口（同歌名可能有元数据）
    """
    results = search(keyword, limit=15)

    # === 策略 C：网易云搜不到 → 直接降级多端 ===
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

    # 批量拿前 4 条官方直链
    # 注意：批量请求时 Node 端通用解灰只处理 data[0] 且把逗号拼接ID传给匹配器（必然失败），
    # 所以批量结果里 VIP 歌仍是试听，解灰在下面逐首走 /song/url/match
    top = ranked[:4]
    try:
        urls = get_play_urls([s['id'] for _, s in top])
    except Exception as e:
        logger.warning(f'[search] 批量直链失败，退回逐首验证: {e}')
        urls = {}

    is_trial = False
    unblocked = False
    pick = None

    # 硬门槛：短于 60s 的基本是铃声/片段，分数再高也不选；全是短曲时才放行
    pool = [x for x in top if not x[1].get('duration') or x[1]['duration'] >= 60] or top

    def _full_official(song):
        u = urls.get(song['id'])
        return u if u and u.get('url') and not u.get('freeTrialInfo') else None

    if urls:
        # 第一轮：严格按打分顺序，官方完整直链优先；VIP/下架候选逐个解灰（第三方同曲完整版）
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

        # 第二轮：完整版（含解灰）全灭 → 按打分顺序退而取试听版
        if not pick:
            for (_, _why), s in pool:
                u = urls.get(s['id'])
                if u and u.get('url') and u.get('freeTrialInfo'):
                    pick = (s, u)
                    is_trial = True
                    logger.warning(f'[search] ⚠️ 解灰也失败，使用试听版（45s）: {s["name"]} - {s["artist"]}')
                    break

    if not pick:
        # 批量整体失败 / 前 4 连试听链接都没有：单独请求打分最高的一首
        # （单首请求会触发 Node 端通用解灰，VIP 歌可能直接拿到第三方完整链接）
        s = pool[0][1]
        try:
            play = get_play_url(s['id'], level='exhigh')
        except RuntimeError as e:
            return {"ok": False, "msg": str(e)}
        if play.get('url') and not play.get('freeTrialInfo'):
            pick = (s, play)
            if '126.net' not in urllib.parse.urlparse(play['url']).netloc:
                unblocked = True
                logger.info(f'[search] 🔓 服务端通用解灰命中: {s["name"]} - {s["artist"]}')
        elif play.get('url'):
            # 官方只有试听 → 再显式解灰一次，失败才认试听
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

    # === 策略 C 降级：网易云全灭（连试听都没有）→ 多端融合 ===
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

    # 旧版 /search 没有封面，用详情接口补（失败不影响播放）
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
