
const { API_BASE } = require('./config')

App({
  globalData: {
    token: '',
    apiBase: API_BASE,
  },

  onLaunch() {

    const token = wx.getStorageSync('token')
    if (token) {
      this.globalData.token = token
    }
  },

 
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


  logout() {
    this.globalData.token = ''
    wx.removeStorageSync('token')
  },
})
