// 自动生成 anonymous_token（app.js 里也有，但我们不走它的 bin）
const fs = require('fs');
const path = require('path');
const tmpPath = require('os').tmpdir();
const tokenPath = path.resolve(tmpPath, 'anonymous_token');
if (!fs.existsSync(tokenPath)) {
  fs.writeFileSync(tokenPath, '', 'utf-8');
}

// 加载 dotenv（让 .env 文件里的配置生效）
require('dotenv').config();

// 直接 require 包内的 server，不走 app.js 的 checkVersion
const { serveNcmApi } = require('@neteasecloudmusicapienhanced/api/server');

serveNcmApi({
  port: Number(process.env.PORT || 3000),
  host: process.env.HOST || '127.0.0.1',
  checkVersion: false,  // 禁用版本检查（省掉 exec npm info 的延迟）
}).then((app) => {
  // ===== 多端融合路由（挂在同一 Express app 上，同端口 3000）=====
  require('./multi_router')(app);
}).catch((err) => {
  console.error('serveNcmApi 启动失败:', err);
  process.exit(1);
});
