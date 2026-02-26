// miniprogram/utils/auth.js
// 登录态管理：自动处理 Token 过期重试

const app = getApp()

/**
 * 确保有有效 Token，若无则发起登录。
 * 所有需要鉴权的请求前调用此函数。
 */
async function ensureLogin() {
  if (app.globalData.token) return app.globalData.token
  return app.login()
}

/**
 * 带鉴权头的标准请求封装
 */
function authRequest(options) {
  return ensureLogin().then((token) => {
    return new Promise((resolve, reject) => {
      wx.request({
        ...options,
        header: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
          ...(options.header || {}),
        },
        success(res) {
          if (res.statusCode === 401) {
            // Token 失效，清除后重新登录
            app.logout()
            reject({ code: 401, msg: '登录已过期，请重新进入' })
          } else {
            resolve(res)
          }
        },
        fail: reject,
      })
    })
  })
}

module.exports = { ensureLogin, authRequest }
