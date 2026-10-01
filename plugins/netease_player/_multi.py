# -*- coding: utf-8 -*-
"""
_multi —— 多端融合搜索（酷狗/酷我/QQ）+ bodian 直链（酷我完整版）
===============================================================

所有接口都是公开 HTTP API，不需要加密，纯 urllib + hashlib 即可。
这些接口来自 UnblockNeteaseMusic/server 的公开 provider 实现（已验证可用）。

酷狗搜索：http://mobilecdn.kugou.com/api/v3/search/song?keyword=X&page=1&pagesize=N
酷我搜索：http://search.kuwo.cn/r.s?correct=1&vipver=1&stype=comprehensive&rformat=json&...&all=X
QQ  搜索：https://c.y.qq.com/soso/fcgi-bin/client_search_cp?w=X&p=1&n=N&format=json

bodian 直链（酷我完整版，免 cookie 完整 FLAC）：
  1. search.kuwo.cn 搜索匹配歌曲 → 拿到 MUSICRID
  2. 构造 sign（MD5("kuwotest" + 字符排序(query_string) + pathname)）
  3. POST bd-api.kuwo.cn/api/service/advert/watch（刷广告换完整版权限）
  4. GET bd-api.kuwo.cn/api/play/music/v2/audioUrl?br=2000kflac&musicId=X&timestamp=...&sign=...
"""
import hashlib
import json
import logging
import random
import re
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

TIMEOUT = 8

# ==================== 酷我 sign 生成 ====================
def _get_random_device_id():
    return str(random.randint(0, 10**12))

def _generate_sign(url_str):
    """
    bodian provider 的签名算法（UnblockNeteaseMusic/server/src/provider/bodian.js 的 generateSign）。
    url_str: 带 scheme 的完整 URL（不含 sign 参数）
    返回: 加了 timestamp 和 sign 的完整 URL
    """
    from urllib.parse import urlparse
    url = urlparse(url_str)
    current_time = int(time.time() * 1000)
    url_str += f'&timestamp={current_time}'

    # 取 query 部分 → 去掉非字母数字 → 排序 → join
    query_start = url_str.index('?') + 1
    filtered_chars = re.sub(r'[^a-zA-Z0-9]', '', url_str[query_start:])
    sorted_chars = ''.join(sorted(filtered_chars))

    data_to_encrypt = f'kuwotest{sorted_chars}{url.path}'
    sign = hashlib.md5(data_to_encrypt.encode('utf-8')).hexdigest()
    return f'{url_str}&sign={sign}'


# ==================== 酷狗搜索 ====================
def kugou_search(keyword, limit=10):
    """
    mobilecdn.kugou.com 公开搜索接口。
    返回: [{'platform','id','name','artist','duration','fee','cover','album','extra'}, ...]
    """
    try:
        url = f'http://mobilecdn.kugou.com/api/v3/search/song?keyword={urllib.parse.quote(keyword)}&page=1&pagesize={limit}'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10) kuwo/3.0.0',
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read())
        items = (data.get('data') or {}).get('info') or []
        return [
            {
                'platform': 'kugou',
                'id': s.get('hash', ''),
                'name': s.get('songname', ''),
                'artist': re.sub(r'<[^>]+>', '', s.get('singername', '')),
                'duration': round(s.get('duration', 0)),
                'fee': 0,
                'cover': s.get('imgurl', ''),
                'album': s.get('album_name', ''),
                'extra': {'hash': s.get('hash', ''), 'album_id': s.get('album_id', '')},
            }
            for s in items
        ]
    except Exception as e:
        logger.warning(f'[multi] 酷狗搜索失败: {e}')
        return []


# ==================== 酷我搜索 ====================
def kuwo_search(keyword, limit=10):
    """search.kuwo.cn 公开搜索接口（Comprehensive 综合模式）"""
    try:
        kw = urllib.parse.quote(keyword)
        url = (f'http://search.kuwo.cn/r.s?&correct=1&vipver=1&stype=comprehensive'
               f'&encoding=utf8&rformat=json&mobi=1&show_copyright_off=1&searchapi=6&all={kw}')
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10) kuwo/3.0.0',
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read())
        abslist = (data.get('content') or [{}])[1].get('musicpage', {}).get('abslist', []) if len(data.get('content') or []) > 1 else []
        return [
            {
                'platform': 'kuwo',
                'id': str(s.get('MUSICRID', '')).split('_')[-1],
                'name': s.get('SONGNAME', ''),
                'artist': (s.get('ARTIST', '') or '').split('&')[0].strip(),
                'duration': round((s.get('DURATION', 0) or 0) / 1000),
                'fee': 0,
                'cover': s.get('ALBUMIMG', ''),
                'album': s.get('ALBUM', ''),
                'extra': {'rid': str(s.get('MUSICRID', '')).split('_')[-1]},
            }
            for s in abslist[:limit]
        ]
    except Exception as e:
        logger.warning(f'[multi] 酷我搜索失败: {e}')
        return []


# ==================== QQ 搜索 ====================
def qq_search(keyword, limit=10):
    """c.y.qq.com 公开搜索接口"""
    try:
        kw = urllib.parse.quote(keyword)
        url = f'https://c.y.qq.com/soso/fcgi-bin/client_search_cp?w={kw}&p=1&n={limit}&cr=1&format=json'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) QQMusic/18.0.0',
            'Referer': 'https://y.qq.com/',
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read())
        items = (data.get('data') or {}).get('song', {}).get('list', [])
        return [
            {
                'platform': 'qq',
                'id': s.get('songmid', ''),
                'name': s.get('songname', ''),
                'artist': ' / '.join(x.get('name', '') for x in (s.get('singer') or [])),
                'duration': round((s.get('interval') or 0)),
                'fee': 1 if s.get('fee') == 1 else 0,
                'cover': f'https://y.gtimg.cn/music/photo_new/T002R300x300M000{s.get("albummid", "")}.jpg' if s.get('albummid') else '',
                'album': s.get('albumname', ''),
                'extra': {'songmid': s.get('songmid', '')},
            }
            for s in items
        ]
    except Exception as e:
        logger.warning(f'[multi] QQ 搜索失败: {e}')
        return []


# ==================== bodian 直链（酷我完整版 FLAC）====================
def _bodian_search(info):
    """
    在酷我上搜索匹配歌曲，返回 MUSICRID（数字ID）。
    info: {'keyword', 'name', 'artists', 'duration'}（毫秒）
    """
    keyword = (info.get('keyword') or '').replace(' - ', ' ')
    keyword = urllib.parse.quote(keyword)
    url = (f'http://search.kuwo.cn/r.s?&correct=1&vipver=1&stype=comprehensive'
           f'&encoding=utf8&rformat=json&mobi=1&show_copyright_off=1&searchapi=6&all={keyword}')
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10) kuwo/3.0.0',
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read())
        abslist = (data.get('content') or [{}])[1].get('musicpage', {}).get('abslist', []) if len(data.get('content') or []) > 1 else []
        if not abslist:
            return None
        # 简单匹配：前 5 条里时长差 5s 内的第一条，没有就第一条
        target_dur_ms = (info.get('duration') or 0)
        for s in abslist[:5]:
            rid = str(s.get('MUSICRID', '')).split('_')[-1]
            s_dur = (s.get('DURATION') or 0)
            if rid and s_dur and (not target_dur_ms or abs(s_dur - target_dur_ms) < 5000):
                return rid
        rid = str(abslist[0].get('MUSICRID', '')).split('_')[-1]
        return rid if rid else None
    except Exception as e:
        logger.debug(f'[bodian] search 失败: {e}')
        return None


def _bodian_ad_free():
    """
    POST bd-api.kuwo.cn/api/service/advert/watch 刷广告换完整版权限。
    这是酷我 App（dart:io）每次取完整版之前的必做步骤（看广告=解锁完整版）。
    """
    device_id = _get_random_device_id()
    url = f'http://bd-api.kuwo.cn/api/service/advert/watch?uid=-1&token=&timestamp={int(time.time()*1000)}&sign=15a676d66285117ad714e8c8371691da'
    headers = {
        'User-Agent': 'Dart/2.19 (dart:io)',
        'plat': 'ar',
        'channel': 'aliopen',
        'devid': device_id,
        'ver': '3.9.0',
        'host': 'bd-api.kuwo.cn',
        'qimei36': '1e9970cbcdc20a031dee9f37100017e1840e',
        'Content-Type': 'application/json; charset=utf-8',
    }
    data = json.dumps({'type': 5, 'subType': 5, 'musicId': 0, 'adToken': ''}).encode('utf-8')
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method='POST')
        urllib.request.urlopen(req, timeout=TIMEOUT).read()
    except Exception as e:
        logger.debug(f'[bodian] ad_free 失败(可忽略): {e}')


def bodian_track(music_id):
    """
    取酷我完整版 FLAC 直链。
    music_id: _bodian_search 返回的 MUSICRID（数字）
    返回: https URL 字符串，或 None
    """
    device_id = _get_random_device_id()
    # 构造带 sign 的 audioUrl
    base_url = f'http://bd-api.kuwo.cn/api/play/music/v2/audioUrl?&br=2000kflac&musicId={music_id}'
    signed_url = _generate_sign(base_url)

    headers = {
        'User-Agent': 'Dart/2.19 (dart:io)',
        'plat': 'ar',
        'channel': 'aliopen',
        'devid': device_id,
        'ver': '3.9.0',
        'host': 'bd-api.kuwo.cn',
        'X-Forwarded-For': '1.0.1.114',
    }
    try:
        # 先刷广告
        _bodian_ad_free()
        req = urllib.request.Request(signed_url, headers=headers)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read())
        if data.get('code') != 200:
            logger.warning(f'[bodian] track code={data.get("code")}')
            return None
        audio_url = (data.get('data') or {}).get('audioUrl', '')
        if not audio_url:
            return None
        if audio_url.startswith('http://'):
            audio_url = 'https://' + audio_url[7:]
        return audio_url
    except Exception as e:
        logger.warning(f'[bodian] track {music_id} 失败: {e}')
        return None


def bodian_check(info):
    """
    bodian 的 check()：搜索 + 直链（UnblockNeteaseMusic/server/provider/bodian.js 的 check）。
    info: {'keyword', 'name', 'artists', 'duration'}（毫秒）
    返回: https URL 或 None
    """
    rid = _bodian_search(info)
    if not rid:
        return None
    return bodian_track(rid)


# ==================== 对外统一接口 ====================

def merge_search(keyword, limit=15, platforms=('kugou', 'kuwo', 'qq')):
    """
    多端融合搜索（并发概念，但 Python 这里顺序执行也快，因为都是公开接口）。
    返回统一格式的候选列表。
    """
    results = []
    if 'kugou' in platforms:
        results.extend(kugou_search(keyword, limit))
    if 'kuwo' in platforms:
        results.extend(kuwo_search(keyword, limit))
    if 'qq' in platforms:
        results.extend(qq_search(keyword, limit))
    return results


def multi_play_url(name, artist='', duration=0):
    """
    多端统一直链（走 bodian = 酷我完整版）。
    与原 Node API /multi/url 等价。
    name: 歌名
    artist: 歌手（可以空）
    duration: 时长（秒）
    返回: https URL 字符串，失败返回 None
    """
    if not name:
        return None
    info = {
        'keyword': (f'{name} - {artist}' if artist else name).strip(),
        'name': name,
        'artists': [{'name': a.strip()} for a in artist.replace(' / ', ',').replace(',', '/').split('/') if a.strip()],
        'duration': duration * 1000,
    }
    return bodian_check(info)


def unblock_by_bodian(name, artist='', duration=0, netease_id=None):
    """
    解灰：给定歌名+歌手+时长，用 bodian 匹配酷我完整版。
    （Node API 的 /song/url/match 本质也是这个逻辑，只是多了一层网易云 song detail → 提取元数据）
    返回: https URL 或 None
    """
    url = multi_play_url(name, artist, duration)
    if url:
        logger.info(f'[multi] 🔓 bodian 直链命中: {name} - {artist}')
    return url
