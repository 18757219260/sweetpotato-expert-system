
const app = getApp()


async function ensureLogin() {
  if (app.globalData.token) return app.globalData.token
  return app.login()
}

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
