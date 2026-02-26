// miniprogram/app.js
const { API_BASE } = require('./config')

App({
  globalData: {
    token: '',
    apiBase: API_BASE,
  },

  onLaunch() {
    // 从本地存储恢复 Token
    const token = wx.getStorageSync('token')
    if (token) {
      this.globalData.token = token
    }
  },

  /**
   * 微信登录闭环：wx.login() → /api/login → 存储 JWT
   * 返回 Promise<token>
   */
  login() {
    return new Promise((resolve, reject) => {
      wx.login({
        success: ({ code }) => {
          wx.request({
            url: `${API_BASE}/api/login`,
            method: 'POST',
            header: { 'Content-Type': 'application/json' },
            data: { code },
            success: (res) => {
              if (res.statusCode === 200 && res.data.access_token) {
                const token = res.data.access_token
                this.globalData.token = token
                wx.setStorageSync('token', token)
                resolve(token)
              } else {
                reject(res.data)
              }
            },
            fail: reject,
          })
        },
        fail: reject,
      })
    })
  },

  /** 清除登录态 */
  logout() {
    this.globalData.token = ''
    wx.removeStorageSync('token')
  },
})
