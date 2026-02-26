// miniprogram/pages/chat/chat.js
const { streamChat, fetchHistory, clearHistory, fetchSessions, createSession, deleteSession, renameSession } = require('../../utils/request')
const { mdToHtml } = require('../../utils/markdown')
const { STATIC_BASE, API_BASE } = require('../../config')

const recorderManager = wx.getRecorderManager()

const IMAGE_MAP = {
  soft_rot:      'soft_rot.jpg',
  black_spot:    'black_spot.jpg',
  stem_nematode: 'stem_nematode.jpg',
  root_rot:      'root_rot.jpg',
  virus:         'virus.jpg',
  scab:          'scab.jpg',
  hornworm:      'hornworm.jpg',
  weevil:        'weevil.jpg',
  armyworm:      'armyworm.jpg',
  fertilizer:    'fertilizer.jpg',
  storage:       'storage.jpg',
  irrigation:    'irrigation.jpg',
}

const IMG_TAG_RE = /\[图片:\w+\]/g

function buildSegments(segments) {
  return segments.map(seg => {
    if (seg.type === 'image') {
      const filename = IMAGE_MAP[seg.id]
      return { type: 'image', url: filename ? `${STATIC_BASE}/${filename}` : '' }
    }
    return { type: 'text', html: mdToHtml(seg.content) }
  }).filter(seg => seg.type !== 'image' || seg.url)
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
  },

  onLoad() {
    this._initSessions()
    this._initVoice()
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

  // ── 输入框 ────────────────────────────────────────────────────────────────
  onInputChange(e) {
    this.setData({ inputText: e.detail.value })
  },

  // ── 发送消息 ──────────────────────────────────────────────────────────────
  async onSend() {
    const question = this.data.inputText.trim()
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
