// miniprogram/pages/chat/chat.js
const { streamChat, fetchHistory, clearHistory, fetchSessions, createSession, deleteSession, renameSession } = require('../../utils/request')
const { mdToHtml } = require('../../utils/markdown')
const { STATIC_BASE, API_BASE } = require('../../config')

const recorderManager = wx.getRecorderManager()



const IMG_TAG_RE = /\[图片:\w+\]/g


function buildSegments(segments) {
  return segments.map(seg => {
    if (seg.type === 'image') {
      return { type: 'image', url: `${STATIC_BASE}/${seg.id}` }
    }
    return { type: 'text', html: mdToHtml(seg.content) }
  })
}
Page({
  data: {
    messages: [],
    inputText: '',
    sending: false,
    scrollToId: '',
    mode: 'pro',
    inputMode: 'keyboard',  // 'keyboard' | 'voice'
    recording: false,
    // 多会话
    sessions: [],
    currentSessionId: null,
    drawerOpen: false,
    // 图片上传
    pendingImage: null,  // 暂存待发送的图片路径
    // 用户信息
    userAvatarUrl: '/images/user-avatar.png',  // 默认头像
  },

  onLoad() {
    this._initSessions()
    this._initVoice()
    this._getUserInfo()
  },

  // ── 获取用户信息 ──────────────────────────────────────────────────────────
  _getUserInfo() {
    wx.getUserProfile({
      desc: '用于显示用户头像',
      success: (res) => {
        console.log('[userInfo] getUserProfile success:', res.userInfo)
        this.setData({
          userAvatarUrl: res.userInfo.avatarUrl
        })
      },
      fail: (err) => {
        console.log('[userInfo] getUserProfile fail:', err)
        // 使用默认头像
      }
    })
  },

  // ── 语音初始化 ────────────────────────────────────────────────────────────
  _initVoice() {
    recorderManager.onStart(() => {
      console.log('[voice] onStart: recording started OK')
    })
    recorderManager.onStop((res) => {
      console.log('[voice] onStop triggered, tempFilePath:', res.tempFilePath)
      if (!res.tempFilePath) {
        wx.showToast({ title: '录音文件为空', icon: 'none' })
        return
      }
      wx.showLoading({ title: '识别中...' })
      wx.uploadFile({
        url: `${API_BASE}/api/voice/recognize`,
        filePath: res.tempFilePath,
        name: 'file',
        success: (r) => {
          console.log('[voice] uploadFile statusCode:', r.statusCode, 'data:', r.data)
          wx.hideLoading()
          try {
            const json = JSON.parse(r.data)
            if (r.statusCode === 200 && json.text) {
              this.setData({ inputText: json.text }, () => this.onSend())
            } else {
              console.error('[voice] ASR error:', json)
              wx.showToast({ title: json.detail || '识别失败', icon: 'none' })
            }
          } catch (e) {
            console.error('[voice] parse error:', e, r.data)
            wx.showToast({ title: '识别失败', icon: 'none' })
          }
        },
        fail: (err) => {
          console.error('[voice] uploadFile fail:', err)
          wx.hideLoading()
          wx.showToast({ title: '识别失败', icon: 'none' })
        },
      })
    })
    recorderManager.onError((err) => {
      console.error('[voice] onError:', err)
      this.setData({ recording: false })
      wx.showToast({ title: '录音失败', icon: 'none' })
    })
  },

  // ── 切换输入模式 ──────────────────────────────────────────────────────────
  onToggleInputMode() {
    const inputMode = this.data.inputMode === 'keyboard' ? 'voice' : 'keyboard'
    this.setData({ inputMode })
  },

  // ── 语音输入：按下开始 ────────────────────────────────────────────────────
  onVoiceStart() {
    console.log('[voice] onVoiceStart called')
    this.setData({ recording: true })
    recorderManager.start({ format: 'wav', sampleRate: 16000, numberOfChannels: 1 })
    console.log('[voice] recorderManager.start called')
  },

  // ── 语音输入：松开结束 ────────────────────────────────────────────────────
  onVoiceEnd() {
    console.log('[voice] onVoiceEnd called')
    this.setData({ recording: false })
    recorderManager.stop()
    console.log('[voice] recorderManager.stop called')
  },

  // ── 初始化会话列表 ────────────────────────────────────────────────────────
  async _initSessions() {
    try {
      const data = await fetchSessions()
      const sessions = data.sessions || []
      if (sessions.length === 0) {
        const s = await createSession('新对话')
        this.setData({ sessions: [s], currentSessionId: s.id })
      } else {
        this.setData({ sessions, currentSessionId: sessions[0].id })
        await this._loadHistory(sessions[0].id)
      }
    } catch (e) {
      console.warn('会话初始化失败', e)
    }
  },

  // ── 加载指定会话的历史 ────────────────────────────────────────────────────
  async _loadHistory(sessionId) {
    try {
      const data = await fetchHistory(20, sessionId)
      const messages = (data.messages || []).map((m, i) => ({
        id: `hist_${sessionId}_${i}`,
        role: m.role,
        segments: m.role === 'assistant'
          ? [{ type: 'text', html: mdToHtml(m.content) }]
          : [{ type: 'text', html: m.content }],
        rawText: m.content,
        done: true,
      }))
      this.setData({ messages })
      this._scrollToBottom()
    } catch (e) {
      console.warn('历史加载失败', e)
    }
  },

  // ── 抽屉开关 ──────────────────────────────────────────────────────────────
  onOpenDrawer() { this.setData({ drawerOpen: true }) },
  onCloseDrawer() { this.setData({ drawerOpen: false }) },

  // ── 切换会话 ──────────────────────────────────────────────────────────────
  async onSelectSession(e) {
    const id = e.currentTarget.dataset.id
    if (id === this.data.currentSessionId) { this.setData({ drawerOpen: false }); return }
    this.setData({ currentSessionId: id, messages: [], drawerOpen: false })
    await this._loadHistory(id)
  },

  // ── 新建会话 ──────────────────────────────────────────────────────────────
  async onNewSession() {
    try {
      const s = await createSession('新对话')
      this.setData({ sessions: [s, ...this.data.sessions], currentSessionId: s.id, messages: [], drawerOpen: false })
    } catch (e) {
      wx.showToast({ title: '新建失败', icon: 'none' })
    }
  },

  // ── 重命名会话 ────────────────────────────────────────────────────────────
  onRenameSession(e) {
    const { id, title } = e.currentTarget.dataset
    wx.showModal({
      title: '重命名',
      editable: true,
      placeholderText: title,
      success: async ({ confirm, content }) => {
        if (!confirm || !content.trim()) return
        try {
          await renameSession(id, content.trim())
          const sessions = this.data.sessions.map(s =>
            s.id === id ? { ...s, title: content.trim() } : s
          )
          this.setData({ sessions })
        } catch (e) {
          wx.showToast({ title: '重命名失败', icon: 'none' })
        }
      },
    })
  },

  // ── 删除会话 ──────────────────────────────────────────────────────────────
  onDeleteSession(e) {
    const id = e.currentTarget.dataset.id
    wx.showModal({
      title: '删除会话', content: '确定删除该会话及其所有记录吗？', confirmColor: '#f44336',
      success: async ({ confirm }) => {
        if (!confirm) return
        try {
          await deleteSession(id)
          let sessions = this.data.sessions.filter(s => s.id !== id)
          let currentSessionId = this.data.currentSessionId
          let messages = this.data.messages
          if (currentSessionId === id) {
            if (sessions.length === 0) {
              const s = await createSession('新对话')
              sessions = [s]; currentSessionId = s.id; messages = []
            } else {
              currentSessionId = sessions[0].id; messages = []
              await this._loadHistory(currentSessionId)
            }
          }
          this.setData({ sessions, currentSessionId, messages })
        } catch (e) {
          wx.showToast({ title: '删除失败', icon: 'none' })
        }
      },
    })
  },

  // ── 打开农场档案 ────────────────────────────────────────────────────────────
  onOpenFarm() {
    wx.navigateTo({ url: '/pages/farm/farm' })
  },

  // ── 输入框 ────────────────────────────────────────────────────────────────
  onInputChange(e) {
    this.setData({ inputText: e.detail.value })
  },

  // ── 发送消息 ──────────────────────────────────────────────────────────────
  async onSend() {
    const question = this.data.inputText.trim()
    const pendingImage = this.data.pendingImage

    // 如果有图片，调用图片上传接口
    if (pendingImage) {
      this._uploadAndAnalyze(pendingImage, question)
      this.setData({ inputText: '', pendingImage: null })
      return
    }

    // 纯文本消息
    if (!question || this.data.sending) return

    this.setData({ inputText: '', sending: true })

    const userMsg = {
      id: `u_${Date.now()}`,
      role: 'user',
      segments: [{ type: 'text', html: question }],
      rawText: question,
      done: true,
    }

    const aiMsgId = `a_${Date.now()}`
    const aiMsg = {
      id: aiMsgId,
      role: 'assistant',
      segments: [{ type: 'text', html: '<span style="color:#999;">正在思考...</span>' }],
      rawText: '',
      done: false,
    }

    this.setData({ messages: [...this.data.messages, userMsg, aiMsg] })
    this._scrollToBottom()

    let rawAccum = ''
    const sessionId = this.data.currentSessionId

    await streamChat({
      question,
      mode: this.data.mode,
      sessionId,

      onText: (text) => {
        rawAccum += text
        const displayText = rawAccum.replace(IMG_TAG_RE, '')
        this._updateAiMsg(aiMsgId, [{ type: 'text', html: mdToHtml(displayText) }], false, '')
        this._scrollToBottom()
      },

      onDone: ({ segments, session_id }) => {
        const renderedSegments = buildSegments(segments)
        const rawText = segments
          .filter(s => s.type === 'text')
          .map(s => s.content)
          .join('\n')
        this._updateAiMsg(aiMsgId, renderedSegments, true, rawText)
        this.setData({ sending: false })
        // 若会话标题还是"新对话"，自动用问题前20字更新
        const curSession = this.data.sessions.find(s => s.id === this.data.currentSessionId)
        if (curSession && curSession.title === '新对话') {
          const newTitle = question.slice(0, 20)
          renameSession(this.data.currentSessionId, newTitle).catch(() => {})
          const sessions = this.data.sessions.map(s =>
            s.id === this.data.currentSessionId ? { ...s, title: newTitle } : s
          )
          this.setData({ sessions })
        }
        if (session_id && session_id !== this.data.currentSessionId) {
          const title = question.slice(0, 20)
          const newSession = { id: session_id, title, created_at: new Date().toISOString() }
          this.setData({ currentSessionId: session_id, sessions: [newSession, ...this.data.sessions] })
        }
        this._scrollToBottom()
      },

      onError: (msg) => {
        this._updateAiMsg(aiMsgId, [{ type: 'text', html: `<span style="color:#f44336;">⚠️ ${msg}</span>` }], true, '')
        this.setData({ sending: false })
        wx.showToast({ title: msg, icon: 'none', duration: 3000 })
      },
    })
  },

  _updateAiMsg(msgId, segments, done, rawText) {
    const messages = this.data.messages.map(m => {
      if (m.id !== msgId) return m
      return { ...m, segments, done, rawText: rawText !== undefined ? rawText : m.rawText }
    })
    this.setData({ messages })
  },

  // ── 图片上传 ──────────────────────────────────────────────────────────────
  onChooseImage() {
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['album', 'camera'],
      success: (res) => {
        const tempFilePath = res.tempFiles[0].tempFilePath
        // 暂存图片路径，不立即上传
        this.setData({ pendingImage: tempFilePath })
        wx.showToast({ title: '图片已选择，可添加描述后发送', icon: 'none', duration: 2000 })
      },
      fail: (err) => {
        console.error('[image] chooseMedia fail:', err)
        wx.showToast({ title: '选择图片失败', icon: 'none' })
      }
    })
  },

  onClearImage() {
    this.setData({ pendingImage: null })
  },

  async _uploadAndAnalyze(filePath, description) {
    if (this.data.sending) return
    this.setData({ sending: true })

    // 1. 添加用户消息（显示图片和描述）
    const segments = [{ type: 'image', url: filePath }]
    if (description) {
      segments.push({ type: 'text', html: description })
    }
    const userMsg = {
      id: `u_${Date.now()}`,
      role: 'user',
      segments: segments,
      done: true,
    }
    this.setData({ messages: [...this.data.messages, userMsg] })
    this._scrollToBottom()

    // 2. 添加 AI 消息占位
    const aiMsgId = `a_${Date.now()}`
    const aiMsg = {
      id: aiMsgId,
      role: 'assistant',
      segments: [{ type: 'text', html: '<span style="color:#999;">正在识别图片...</span>' }],
      done: false,
    }
    this.setData({ messages: [...this.data.messages, aiMsg] })
    this._scrollToBottom()

    // 3. 上传图片并获取流式响应
    const token = wx.getStorageSync('token')
    const API_BASE = getApp().globalData.apiBase

    wx.uploadFile({
      url: `${API_BASE}/api/chat/upload_image`,
      filePath: filePath,
      name: 'file',
      formData: {
        mode: this.data.mode,
        session_id: this.data.currentSessionId,
        stream: 'false',  // 微信小程序不支持流式响应
        description: description || ''  // 传递用户的文字描述
      },
      header: { Authorization: `Bearer ${token}` },
      success: (res) => {
        if (res.statusCode !== 200) {
          this._updateAiMsg(aiMsgId, [{ type: 'text', html: `<span style="color:#f44336;">识别失败：${res.data}</span>` }], true)
          this.setData({ sending: false })
          return
        }

        // 解析完整 JSON 响应
        this._parseUploadResponse(res.data, aiMsgId)
      },
      fail: (err) => {
        console.error('[upload] fail:', err)
        this._updateAiMsg(aiMsgId, [{ type: 'text', html: '<span style="color:#f44336;">上传失败，请重试</span>' }], true)
        this.setData({ sending: false })
      }
    })
  },

  _parseUploadResponse(data, aiMsgId) {
    // 处理完整 JSON 响应
    console.log('[DEBUG] _parseUploadResponse called, data type:', typeof data)
    console.log('[DEBUG] data:', data)

    try {
      // 如果 data 已经是对象，直接使用；否则解析 JSON
      const result = typeof data === 'string' ? JSON.parse(data) : data

      console.log('[DEBUG] result.type:', result.type)
      console.log('[DEBUG] result.llm_response length:', result.llm_response ? result.llm_response.length : 0)

      if (result.type === 'error') {
        this._updateAiMsg(aiMsgId, [{ type: 'text', html: `<span style="color:#f44336;">${result.detail}</span>` }], true)
      } else if (result.type === 'low_confidence') {
        // 置信度过低 - 直接显示 LLM 响应（不显示 VL 分析框）
        const segments = []

        // 添加 LLM 响应
        if (result.segments && result.segments.length > 0) {
          // 使用 buildSegments 函数转换格式
          const convertedSegments = buildSegments(result.segments)
          segments.push(...convertedSegments)
        } else if (result.llm_response) {
          segments.push({ type: 'text', html: mdToHtml(result.llm_response) })
        }
        this._updateAiMsg(aiMsgId, segments, true)
      } else if (result.type === 'success') {
        // 置信度足够，显示识别结果和 LLM 响应
        const segments = []

        // 添加 CV 识别结果（带概率）
        if (result.cv_result) {
          segments.push({
            type: 'text',
            html: `<div style="background:#e3f2fd;padding:12px;border-radius:8px;margin-bottom:12px;"><strong>🔍 识别结果：</strong><br/>${result.cv_result.replace(/\n/g, '<br/>')}</div>`
          })
        }

        // 添加 LLM 诊断建议
        // 优先使用 segments（包含图片），否则使用 llm_response（纯文本）
        if (result.segments && result.segments.length > 0) {
          console.log('[DEBUG] Using segments from backend:', result.segments.length)
          // 使用 buildSegments 函数转换格式
          const convertedSegments = buildSegments(result.segments)
          segments.push(...convertedSegments)
        } else if (result.llm_response) {
          console.log('[DEBUG] Using llm_response (no segments)')
          segments.push({ type: 'text', html: mdToHtml(result.llm_response) })
        } else {
          console.log('[DEBUG] LLM response is empty!')
        }

        this._updateAiMsg(aiMsgId, segments, true)
      } else {
        // 兼容旧格式
        this._updateAiMsg(aiMsgId, [{ type: 'text', html: result.content || data }], true)
      }
    } catch (e) {
      console.error('[parse] error:', e)
      this._updateAiMsg(aiMsgId, [{ type: 'text', html: `<span style="color:#f44336;">解析响应失败</span>` }], true)
    }
    this.setData({ sending: false })
    this._scrollToBottom()
  },

  // ── 模式切换 ──────────────────────────────────────────────────────────────
  onToggleMode() {
    this.setData({ mode: this.data.mode === 'pro' ? 'flash' : 'pro' })
  },

  // ── 快捷问题 ──────────────────────────────────────────────────────────────
  onQuickAsk(e) {
    const q = e.currentTarget.dataset.q
    this.setData({ inputText: q }, () => this.onSend())
  },

  // ── 清空当前会话 ──────────────────────────────────────────────────────────
  onClear() {
    wx.showModal({
      title: '清空对话', content: '确定清除当前会话的所有记录吗？', confirmColor: '#f44336',
      success: async ({ confirm }) => {
        if (!confirm) return
        try {
          await clearHistory(this.data.currentSessionId)
          this.setData({ messages: [] })
          wx.showToast({ title: '已清空', icon: 'success' })
        } catch (e) {
          wx.showToast({ title: '清空失败', icon: 'none' })
        }
      },
    })
  },

  // ── 图片预览 ──────────────────────────────────────────────────────────────
  onPreviewImage(e) {
    const { url } = e.currentTarget.dataset
    const allUrls = this.data.messages
      .flatMap(m => m.segments.filter(s => s.type === 'image').map(s => s.url))
    wx.previewImage({ current: url, urls: allUrls.length ? allUrls : [url] })
  },

  // ── 滚动到底部 ────────────────────────────────────────────────────────────
  _scrollToBottom() {
    const messages = this.data.messages
    if (messages.length === 0) return
    this.setData({ scrollToId: messages[messages.length - 1].id })
  },
})
