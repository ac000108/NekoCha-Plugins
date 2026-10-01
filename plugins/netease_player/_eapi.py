# -*- coding: utf-8 -*-
"""
_eapi —— 网易云 weapi 加密 + 请求（pycryptodome, 系统自带）
===========================================================

正确的双层 AES 加密（参考 window.asrsea + Electron 源码）：
  1. 第一层 AES-CBC(json_data_bytes, key="0CoJUm6Qyw8W8jud", iv="0102030405060708")
     → 输出 raw ciphertext → base64 编码成字符串
  2. 第二层 AES-CBC(第一层 base64_str.utf-8, key="4JknCzx6uEXUwxpU", iv="0102030405060708")
     → 输出 raw → base64 → params
  3. encSecKey 固定（对应固定 key="4JknCzx6uEXUwxpU"），跳过 RSA

零 Node 依赖，纯标准库 + pycryptodome（Python 系统自带）。
"""
import base64
import json
import logging
import urllib.parse
import urllib.request

from Crypto.Cipher import AES

logger = logging.getLogger(__name__)

# ==================== weapi 加密常量 ====================
_CONST_KEY = b"0CoJUm6Qyw8W8jud"          # 固定 AES key（"云音乐的密钥"）
_IV = b"0102030405060708"                 # 16 bytes
_FIXED_I = '4JknCzx6uEXUwxpU'             # 固定随机 key（对应固定 encSecKey）
_ENC_SEC_KEY = (
    "01ec48cb405730aa77f993a988cc1f5bc1938511d75f49eddc581f2fe2aaf1898"
    "8853200564b2d4b1312cf6e0bb344425addce5a4c81b38b89a5973900946bd100"
    "b0f1865d22d2a8e5dd8be208eb5d6eb2f71309a165daeffe95355e1e44edd65b"
    "df28088fe4f5e835a7d9f7569fc2530f9d17c00b51cfafbe421eb462247ea3"
)


def _aes_cbc_encrypt(plain_bytes, key, iv):
    """AES-128-CBC + PKCS7"""
    pad = 16 - (len(plain_bytes) % 16)
    plain_bytes += bytes([pad] * pad)
    return AES.new(key, AES.MODE_CBC, iv).encrypt(plain_bytes)


def _encrypt_params(data_dict):
    """
    正确的 weapi 参数加密：
      1. AES-CBC(json_bytes, _CONST_KEY, _IV) → raw → base64 str
      2. AES-CBC(base64_str.utf-8, _FIXED_I, _IV) → raw → base64 = params
      3. encSecKey = 固定值（与 _FIXED_I 配对）
    """
    s = json.dumps(data_dict, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    first_raw = _aes_cbc_encrypt(s, _CONST_KEY, _IV)
    first_b64 = base64.b64encode(first_raw)  # bytes
    second_raw = _aes_cbc_encrypt(first_b64, _FIXED_I.encode('utf-8'), _IV)
    params = base64.b64encode(second_raw).decode()
    return params, _ENC_SEC_KEY


def _weapi_request(path, data_dict, timeout=10):
    """
    POST https://music.163.com/weapi/{path}?csrf_token=xxx
    返回: json dict 或 {}
    """
    params, encSec = _encrypt_params(data_dict)
    csrf = data_dict.get('csrf_token', '')
    url = f'https://music.163.com/weapi/{path}?csrf_token={csrf}'
    body = urllib.parse.urlencode({'params': params, 'encSecKey': encSec}).encode('utf-8')
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120',
        'Referer': 'https://music.163.com/',
    }
    req = urllib.request.Request(url, data=body, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if not raw:
                logger.warning(f'[weapi] /{path} 空响应（加密可能不匹配）')
                return {}
            return json.loads(raw)
    except Exception as e:
        logger.warning(f'[weapi] /{path} 失败: {e}')
        return {}


# ==================== 高阶接口 ====================

def eapi_search(keywords, limit=15):
    """搜索 weapi/search/get"""
    csrf = ''  # 空就行（固定 encSecKey 不需要真实 csrf）
    return _weapi_request('search/get', {
        's': keywords, 'type': 1, 'limit': limit, 'offset': 0, 'total': True,
        'csrf_token': csrf,
    })


def eapi_song_url(song_id, level='exhigh'):
    """直链 weapi/song/enhance/player/url/v1"""
    return _weapi_request('song/enhance/player/url/v1', {
        'ids': [int(song_id)], 'level': level,
        'encodeType': 'aac', 'immerseType': 'c51',
        'csrf_token': '',
    })


def eapi_song_url_batch(song_ids, level='exhigh'):
    """批量直链"""
    ids = [int(x) for x in song_ids if x]
    if not ids: return {}
    return _weapi_request('song/enhance/player/url/v1', {
        'ids': ids, 'level': level,
        'encodeType': 'aac', 'immerseType': 'c51',
        'csrf_token': '',
    })


def eapi_song_detail(song_id):
    """详情 v3/song/detail（也用 weapi 拿完整数据）"""
    # 详情用 weapi v3/song/detail 或用明文 GET api/v3/song/detail
    # 明文更稳定
    return _api_get('v3/song/detail', {'c': json.dumps([{'id': int(song_id)}])})


def eapi_lyric(song_id):
    """
    歌词：先 weapi/song/lyric/v1 拿逐字 yrc，不行再退明文 /api/song/lyric。
    """
    # 先试 weapi v1（逐字 yrc）
    d1 = _weapi_request('song/lyric/v1', {
        'id': int(song_id), 'cp': True, 'tv': 0, 'lv': 0, 'kv': 0, 'yr': True,
        'csrf_token': '',
    })
    yrc = ''
    yrc_data = d1.get('yrc')
    if isinstance(yrc_data, dict):
        yrc = yrc_data.get('lyric', '') or ''
    elif isinstance(yrc_data, str):
        yrc = yrc_data
    
    lrc = ''
    tlyric = ''
    lrc_data = d1.get('lrc')
    if isinstance(lrc_data, dict):
        lrc = lrc_data.get('lyric', '') or ''
    
    if not lrc:
        # 退明文
        d2 = _api_get('song/lyric', {'id': int(song_id), 'lv': -1, 'tv': -1, 'kv': -1})
        lrc = (d2.get('lrc') or {}).get('lyric', '') or ''
        tlyric = (d2.get('tlyric') or {}).get('lyric', '') or ''
    
    logger.info(f'[netease] lyric id={song_id} lrc={len(lrc)}b yrc={len(yrc)}b')
    return {'lrc': lrc, 'tlyric': tlyric, 'yrc': yrc}


# ==================== 明文 GET 备用 ====================
def _api_get(path, params, timeout=10):
    qs = urllib.parse.urlencode(params)
    url = f'https://music.163.com/api/{path}?{qs}'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120',
        'Referer': 'https://music.163.com/',
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if not raw: return {}
            return json.loads(raw)
    except Exception as e:
        logger.warning(f'[netease] GET /api/{path} 失败: {e}')
        return {}
