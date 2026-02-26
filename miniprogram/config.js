// miniprogram/config.js - 全局配置
const config = {
  // 开发阶段：填写运行后端的局域网 IP
  // 上线后替换为 HTTPS 域名（微信要求线上必须 HTTPS）
  API_BASE: 'http://192.168.1.6:8000',

  // 静态图片服务地址（与 API_BASE 同源）
  STATIC_BASE: 'http://192.168.1.6:8000/static/images',
}
module.exports = config
