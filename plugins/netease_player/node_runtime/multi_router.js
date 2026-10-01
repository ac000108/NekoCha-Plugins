// multi_router.js —— 多端融合搜索 + 直链路由
// 直链层统一走 @unblockneteasemusic/server 的 provider/bodian（已验证通）
// 搜索层用公开 API 拿元数据（mobilecdn.kugou.com / search.kuwo.cn）
const axios = require('axios');
const path = require('path');

const PROVIDER_ROOT = path.join(__dirname, 'node_modules/@unblockneteasemusic/server/src/provider');
const TIMEOUT = 5000;

// ============ 统一格式 ============
// { platform, id, name, artist, duration, fee, cover, extra }

// ============ 搜索层（公开 API 拿元数据）============
async function kugouSearch(keyword, limit = 10) {
  try {
    const url = `http://mobilecdn.kugou.com/api/v3/search/song?keyword=${encodeURIComponent(keyword)}&page=1&pagesize=${limit}`;
    const r = await axios.get(url, { timeout: TIMEOUT });
    const items = (r.data?.data?.info) || [];
    return items.map((s) => ({
      platform: 'kugou',
      id: s.hash,
      name: s.songname || '',
      artist: (s.singername || '').replace(/<[^>]+>/g, ''),
      duration: Math.round(s.duration || 0),
      fee: 0,
      cover: s.imgurl || '',
      album: s.album_name || '',
      extra: { hash: s.hash, album_id: s.album_id || '' },
    }));
  } catch (e) {
    console.error('[multi] 酷狗搜索失败:', e.message?.slice(0, 120));
    return [];
  }
}

async function kuwoSearch(keyword, limit = 10) {
  try {
    const url = `http://search.kuwo.cn/r.s?&correct=1&vipver=1&stype=comprehensive&encoding=utf8&rformat=json&mobi=1&show_copyright_off=1&searchapi=6&all=${encodeURIComponent(keyword)}`;
    const r = await axios.get(url, { timeout: TIMEOUT });
    const abslist = r.data?.content?.[1]?.musicpage?.abslist || [];
    return abslist.slice(0, limit).map((s) => ({
      platform: 'kuwo',
      id: String(s.MUSICRID).split('_').pop(),
      name: s.SONGNAME || '',
      artist: (s.ARTIST || '').split('&').join(' / '),
      duration: Math.round(s.DURATION || 0) / 1000,
      fee: 0,
      cover: s.ALBUMIMG || '',
      album: s.ALBUM || '',
      extra: { rid: String(s.MUSICRID).split('_').pop() },
    }));
  } catch (e) {
    console.error('[multi] 酷我搜索失败:', e.message?.slice(0, 120));
    return [];
  }
}

async function qqSearch(keyword, limit = 10) {
  try {
    const url = `https://c.y.qq.com/soso/fcgi-bin/client_search_cp?w=${encodeURIComponent(keyword)}&p=1&n=${limit}&cr=1&format=json`;
    const r = await axios.get(url, { timeout: TIMEOUT, headers: { referer: 'https://y.qq.com/' } });
    const items = (r.data?.data?.song?.list) || [];
    return items.map((s) => ({
      platform: 'qq',
      id: s.songmid,
      name: s.songname || '',
      artist: (s.singer || []).map((x) => x.name).join(' / '),
      duration: Math.round((s.interval || 0)),
      fee: s.fee === 1 ? 1 : 0,
      cover: s.albummid ? `https://y.gtimg.cn/music/photo_new/T002R300x300M000${s.albummid}.jpg` : '',
      album: s.albumname || '',
      extra: { songmid: s.songmid },
    }));
  } catch (e) {
    console.error('[multi] QQ搜索失败:', e.message?.slice(0, 120));
    return [];
  }
}

// ============ 直链层（统一走 bodian provider，免 cookie 完整版）============
let _bodianProvider = null;
function getBodianProvider() {
  if (!_bodianProvider) {
    try {
      _bodianProvider = require(path.join(PROVIDER_ROOT, 'bodian'));
    } catch (e) {
      console.error('[multi] bodian provider 加载失败:', e.message?.slice(0, 100));
    }
  }
  return _bodianProvider;
}

/**
 * 拿直链：传歌名+歌手+时长 → 内部打 search.kuwo.cn 匹配 → 拿 bd-api.kuwo.cn 完整 FLAC
 * 这就是我们解灰链路已验证的通路径
 */
async function resolveUrl(name, artist, durationSec = 0) {
  const bodian = getBodianProvider();
  if (!bodian) return null;

  // 构造 bodian 需要的 info 对象（和 unblockneteasemusic 的 match.js 同款）
  const info = {
    keyword: `${name} - ${artist}`.trim(),
    name,
    artists: artist ? artist.split(' / ').map((a) => ({ name: a.trim() })) : [],
    duration: (durationSec || 0) * 1000,  // 毫秒
  };

  try {
    const url = await bodian.check(info);
    if (!url) return null;
    let u = typeof url === 'string' ? url : null;
    if (u && u.startsWith('http://')) u = 'https://' + u.slice(7);
    return u;
  } catch (e) {
    console.error('[multi] bodian 直链失败:', e.message?.slice(0, 200));
    return null;
  }
}

// ============ 入口函数 ============
module.exports = function mountMultiRoutes(app) {
  // 酷狗搜索
  app.get('/kugou/search', async (req, res) => {
    const keyword = (req.query.keyword || '').trim();
    const limit = parseInt(req.query.limit || 10);
    if (!keyword) return res.json({ code: 400, msg: 'keyword 不能为空' });
    const list = await kugouSearch(keyword, Math.min(limit, 20));
    res.json({ code: 200, data: list });
  });

  // 酷我搜索
  app.get('/kuwo/search', async (req, res) => {
    const keyword = (req.query.keyword || '').trim();
    const limit = parseInt(req.query.limit || 10);
    if (!keyword) return res.json({ code: 400, msg: 'keyword 不能为空' });
    const list = await kuwoSearch(keyword, Math.min(limit, 20));
    res.json({ code: 200, data: list });
  });

  // QQ 搜索
  app.get('/qq/search', async (req, res) => {
    const keyword = (req.query.keyword || '').trim();
    const limit = parseInt(req.query.limit || 10);
    if (!keyword) return res.json({ code: 400, msg: 'keyword 不能为空' });
    const list = await qqSearch(keyword, Math.min(limit, 20));
    res.json({ code: 200, data: list });
  });

  // ===== 融合搜索（并发多平台元数据）=====
  app.get('/merge/search', async (req, res) => {
    const keyword = (req.query.keyword || '').trim();
    const limit = parseInt(req.query.limit || 15);
    const platforms = (req.query.platforms || 'kugou,kuwo,qq')
      .split(',').map((s) => s.trim()).filter(Boolean);

    if (!keyword) return res.json({ code: 400, msg: 'keyword 不能为空' });

    const workers = [];
    if (platforms.includes('kugou')) workers.push(kugouSearch(keyword, limit));
    if (platforms.includes('kuwo')) workers.push(kuwoSearch(keyword, limit));
    if (platforms.includes('qq')) workers.push(qqSearch(keyword, limit));

    const results = await Promise.allSettled(workers);
    const all = [];
    results.forEach((r) => {
      if (r.status === 'fulfilled') all.push(...(r.value || []));
    });

    res.json({ code: 200, data: all, total: all.length });
  });

  // ===== 多端统一直链（全部走 bodian provider）=====
  app.get('/multi/url', async (req, res) => {
    const name = (req.query.name || '').trim();
    const artist = (req.query.artist || '').trim();
    const dur = parseInt(req.query.duration || 0);
    if (!name) return res.json({ code: 400, msg: 'name 不能为空' });

    const url = await resolveUrl(name, artist, dur);
    if (!url) return res.json({ code: 500, msg: '直链获取失败' });
    res.json({ code: 200, url, type: 'flac', source: 'bodian' });
  });

  // 兼容旧路由别名
  app.get('/kugou/url', async (req, res) => {
    const name = (req.query.name || '').trim();
    const artist = (req.query.artist || '').trim();
    const dur = parseInt(req.query.duration || 0);
    if (!name) return res.json({ code: 400, msg: '需要 name 参数' });
    const url = await resolveUrl(name, artist, dur);
    if (!url) return res.json({ code: 500, msg: '直链获取失败' });
    res.json({ code: 200, url, type: 'flac' });
  });

  console.log('[multi_router] ✅ 多端路由已挂载 (直链走 bodian provider)');
};
