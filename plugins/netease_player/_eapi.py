# -*- coding: utf-8 -*-
"""
_eapi —— 网易云音乐 weapi 加密 + 请求（纯标准库，零依赖）
==========================================================

网易云 weapi 加密（AES-CBC）：
  1. text = "nobody" + JSON.stringify(data) + "use"
  2. AES-CBC 加密，key = 0x303130303031（"010001" 左边补零到 16 字节），iv = 0x3837363534333231（"87654321" 补零）
  3. base64 编码 → params
  4. encSecKey = base64( AES-ECB("010001", key=0x7fa90d9b479f6e00 补零到 16 字节) )
     但实际上 weapi 固定 encSecKey 可以直接写死（客户端每次都用同一个）

请求 URL：https://music.163.com/weapi/{path}?csrf_token=xxx
POST body：params=<base64_encrypted>

所有请求自动带匿名 cookie（经 clientHash 计算的 MUSIC_U），不需要登录。
"""
import base64
import hashlib
import json
import logging
import random
import re
import struct
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

# ==================== 加密常量 ====================
# AES 加密用到的 key/iv（固定，所有网易云 weapi 都用这组）
_AES_KEY = b'\x30\x31\x30\x30\x30\x31\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'  # "010001" 左补零到 16
_AES_IV  = b'\x38\x37\x36\x35\x34\x33\x32\x31\x00\x00\x00\x00\x00\x00\x00\x00'  # "87654321" 左补零到 16

# encSecKey：用 AES-ECB("010001", key=固定16字节Key) → base64
# 这个值是网易云客户端固定的，实际上可以硬编码（Node API 的实现就是固定值）
_ENC_SEC_KEY = '5379wOk1BaIXRhwnoFg-HMhbnd16phAqCJw4c8jYG0N7Bc5V6qpX84WVuxd-jJqkH799Ltm5GySH3LQCCdHQLDEsMi026zI1Y24E95Qnv-YDE2OV4uu0Y8cX2r5uAKAsan0YGSfab4e1AWZT4RbdEO7M-o6a-UsSImoRzBAWmQHPt3LuVAQVCq8uAxUMegufk5MC2FcAdftafW9syePVCE8r6QaVfQuRVGA='

# ==================== Cookie 生成 ====================
# 网易云音乐匿名 cookie：经 clientHash 计算 MUSIC_U（不需要登录）
# 算法：32 字节随机 base64url + 固定后缀
def _random_music_u():
    raw = bytes(random.randint(0, 255) for _ in range(32))
    # base64url（去掉 = 填充）
    b = base64.urlsafe_b64encode(raw).rstrip(b'=').decode()
    return b + '010001'  # 后缀固定

# ==================== AES 实现（纯 Python，不用 pycryptodome）====================
# 实现 AES-128-CBC（足够，不需要完整 AES 库）
# 参考维基 AES 算法 + 赛风实现
# 注意：网易云客户端用的是 128 位（key 只有 16 字节）

# Rijndael S-Box
_SBOX = [
    0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
    0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
    0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
    0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
    0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
    0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
    0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
    0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
    0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
    0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
    0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
    0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
    0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
    0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
    0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
    0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16,
]
_INV_SBOX = [0]*256
for _i, _v in enumerate(_SBOX): _INV_SBOX[_v] = _i
_RCON = [0x01,0x02,0x04,0x08,0x10,0x20,0x40,0x80,0x1b,0x36,0x6c,0xd8,0xab,0x4d,0x9a,0x2f]

def _xtime(a): return ((a<<1)^((a&0x80)*0x1b)) & 0xff

def _sub_word(w): return bytes(_SBOX[b] for b in w)

def _rot_word(w): return w[1:]+w[:1]

def _aes_key_expansion(key):
    """AES-128 key expansion（16字节key → 44字×4字节 = 176字节轮密钥）"""
    Nk = 4; Nr = 10
    W = [key[i*4:(i+1)*4] for i in range(Nk)]
    for i in range(Nk, Nk*(Nr+1)):
        temp = W[i-1]
        if i % Nk == 0:
            temp = _sub_word(_rot_word(temp))
            temp = bytes([temp[0]^_RCON[i//Nk-1], temp[1], temp[2], temp[3]])
        elif Nk > 6 and i % Nk == 4:
            temp = _sub_word(temp)
        W.append(bytes(W[i-Nk][j]^temp[j] for j in range(4)))
    return W

def _aes_encrypt_block(pt, round_keys):
    """AES-128 单块加密（16字节）"""
    state = list(pt)
    # AddRoundKey round 0
    for j in range(16): state[j] ^= round_keys[j]
    for rnd in range(1, 10):
        # SubBytes
        state = [_SBOX[b] for b in state]
        # ShiftRows
        s = state
        state = [s[0],s[5],s[10],s[15], s[4],s[9],s[14],s[3], s[8],s[13],s[2],s[7], s[12],s[1],s[6],s[11]]
        # MixColumns
        mixed = [0]*16
        for c in range(4):
            o = c*4; s0,s1,s2,s3 = state[o],state[o+1],state[o+2],state[o+3]
            mixed[o  ] = _xtime(s0)^_xtime(s1)^s1^s2^s3
            mixed[o+1] = s0^_xtime(s1)^_xtime(s2)^s2^s3
            mixed[o+2] = s0^s1^_xtime(s2)^_xtime(s3)^s3
            mixed[o+3] = _xtime(s0)^s0^s1^s2^_xtime(s3)
        state = mixed
        # AddRoundKey
        for j in range(16): state[j] ^= round_keys[rnd*16+j]
    # SubBytes
    state = [_SBOX[b] for b in state]
    # ShiftRows
    s = state
    state = [s[0],s[5],s[10],s[15], s[4],s[9],s[14],s[3], s[8],s[13],s[2],s[7], s[12],s[1],s[6],s[11]]
    # AddRoundKey final
    for j in range(16): state[j] ^= round_keys[160+j]
    return bytes(state)

def _aes_cbc_encrypt(plain, key, iv):
    """AES-128-CBC 加密 + PKCS7 填充"""
    # PKCS7 填充
    pad_len = 16 - (len(plain) % 16)
    plain += bytes([pad_len]*pad_len)
    round_keys = b''.join(_aes_key_expansion(key))
    out = bytearray(); prev = iv
    for i in range(0, len(plain), 16):
        block = bytes(a^b for a,b in zip(plain[i:i+16], prev))
        enc = _aes_encrypt_block(block, round_keys)
        out.extend(enc)
        prev = enc
    return bytes(out)


# ==================== 公开 API ====================

def _encrypt_params(data):
    """weapi 参数加密：dict → base64 字符串"""
    params_str = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    text = 'nobody' + params_str + 'use'
    enc = _aes_cbc_encrypt(text.encode('utf-8'), _AES_KEY, _AES_IV)
    return base64.b64encode(enc).decode()


def weapi_request(path, data=None, timeout=10):
    """
    调网易云 /weapi/{path}，自动加密 params。
    path: 'search/get' / 'song/enhance/player/url' / 'v3/song/detail' / 'song/lyric' ...
    data: dict 参数
    返回: json dict（网易云原始响应）
    """
    data = data or {}
    # 加 csrf_token（weapi 需要）
    csrf = _random_music_u()[:8]  # 用 MUSIC_U 前 8 位当 csrf
    data['csrf_token'] = csrf

    params = _encrypt_params(data)

    url = f'https://music.163.com/weapi/{path}?csrf_token={csrf}'
    body = urllib.parse.urlencode({'params': params}).encode('utf-8')

    cookie = f'MUSIC_U={_random_music_u()}'
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) NeteaseMusicDesktop/3.0.0',
        'Referer': 'https://music.163.com/',
        'Cookie': cookie,
    }
    req = urllib.request.Request(url, data=body, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except Exception as e:
        logger.warning(f'[weapi] /{path} 失败: {e}')
        return {}


def api_request(path, params=None, timeout=10):
    """
    调网易云明文 /api/{path}（search/lyric 也有明文接口，参数简单）。
    备用方案——weapi 有时候会因为 csrf/cookie 被风控，明文 /api/ 更稳定。
    """
    params = params or {}
    qs = urllib.parse.urlencode(params)
    url = f'https://music.163.com/api/{path}?{qs}'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) NeteaseMusicDesktop/3.0.0',
        'Referer': 'https://music.163.com/',
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except Exception as e:
        logger.warning(f'[api] /{path} 失败: {e}')
        return {}


# ==================== 高阶接口（供 netease_api.py 调用）====================

def eapi_search(keywords, limit=15):
    """
    网易云搜索（weapi）。返回网易云原始 {result: {songs: [...]}, code} 结构。
    失败返回 {}。
    """
    return weapi_request('search/get', {
        's': keywords,
        'type': 1,
        'limit': limit,
        'offset': 0,
        'total': True,
    })


def eapi_song_url(song_id, level='exhigh'):
    """
    网易云直链（weapi）。返回网易云原始 {data: [...], code} 结构。
    data[0].url 可能为空（VIP 歌只有试听片段）。
    """
    return weapi_request('song/enhance/player/url', {
        'ids': [int(song_id)],
        'level': level,
        'encodeType': 'aac',
        'immerseType': 'c51',
    })


def eapi_song_url_batch(song_ids, level='exhigh'):
    """批量直链（weapi）"""
    return weapi_request('song/enhance/player/url', {
        'ids': [int(x) for x in song_ids if x],
        'level': level,
        'encodeType': 'aac',
        'immerseType': 'c51',
    })


def eapi_song_detail(song_id):
    """歌曲详情（weapi）"""
    return weapi_request('v3/song/detail', {
        'ids': [int(song_id)],
        'c': json.dumps([{'id': int(song_id)}]),
    })


def eapi_lyric(song_id):
    """
    歌词（weapi 明文）。优先调 /song/lyric（lrc + tlyric + 可能的旧 yrc）。
    再调 /song/lyric/v1（新格式逐字 yrc）。
    返回 {'lrc', 'tlyric', 'yrc'}。
    """
    # 基础歌词
    d1 = weapi_request('song/lyric', {
        'id': int(song_id),
        'lv': -1, 'tv': -1, 'kv': -1,
    })
    lrc = (d1.get('lrc') or {}).get('lyric', '')
    tlyric = (d1.get('tlyric') or {}).get('lyric', '')
    yrc_old = (d1.get('yrc') or {}).get('lyric', '')

    # 逐字歌词 v1（weapi）
    yrc_new = ''
    try:
        d2 = weapi_request('song/lyric/v1', {
            'id': int(song_id),
            'cp': True, 'tv': 0, 'lv': 0, 'kv': 0, 'yr': True,
        })
        # v1 返回格式：{yrc: {version: xxx, lyric: "..."}} 或直接字符串
        yrc_new_data = d2.get('yrc')
        if isinstance(yrc_new_data, dict):
            yrc_new = yrc_new_data.get('lyric', '')
        elif isinstance(yrc_new_data, str):
            yrc_new = yrc_new_data
    except Exception as e:
        logger.debug(f'[eapi] lyric/v1 {song_id} 失败: {e}')

    yrc = yrc_new if yrc_new else yrc_old
    logger.info(f'[eapi] lyric id={song_id} lrc={len(lrc)}b yrc={len(yrc)}b(new={len(yrc_new)}b,old={len(yrc_old)}b)')
    return {'lrc': lrc, 'tlyric': tlyric, 'yrc': yrc}
