// miniprogram/pages/index/index.js
// 启动页：自动登录后跳转到聊天页

const app = getApp()

Page({
  onLoad() {
    this._doLogin()
  },

  async _doLogin() {
    try {
      await app.login()
      wx.redirectTo({ url: '/pages/chat/chat' })
    } catch (e) {
      console.error('登录失败', e)
      wx.showModal({
        title: '登录失败',
        content: '无法连接到服务器，请检查网络后重试',
        showCancel: false,
        confirmText: '重试',
        success: () => this._doLogin(),
      })
    }
  },
})
