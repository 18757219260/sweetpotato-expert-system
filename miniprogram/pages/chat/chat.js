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
    inputMode: 'keyboard',  
    recording: false,

    sessions: [],
    currentSessionId: null,
    drawerOpen: false,

    pendingImage: null,  

    userAvatarUrl: '',  
  },

  onLoad() {
    this._initSessions()
    this._initVoice()
    this._loadUserAvatar()
  },

  // --- 新增：获取带有标志位的系统欢迎消息 ---
  _getWelcomeMsg() {
    const welcomeText = '你好！我是甘薯问答助手。解答种植疑难、诊断病虫害我都是可以的嚒。秒识17种常见害虫，其他的也能为您分析，请提问或发图吧！';
    return {
      id: 'welcome_msg_0',
      role: 'assistant',
      segments: [{ type: 'text', html: mdToHtml(welcomeText) }],
      rawText: welcomeText,
      done: true,
      showSuggestions: true // 控制农场档案和快捷按钮是否显示在气泡下方
    };
  },

  _loadUserAvatar() {
    const avatarUrl = wx.getStorageSync('userAvatarUrl')
    if (avatarUrl) {
      this.setData({ userAvatarUrl: avatarUrl })
    }
  },

  onChooseAvatar(e) {
    const { avatarUrl } = e.detail
    this.setData({ userAvatarUrl: avatarUrl })
    wx.setStorageSync('userAvatarUrl', avatarUrl)
    wx.showToast({ title: '头像已更新', icon: 'success' })
  },

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

  onToggleInputMode() {
    const inputMode = this.data.inputMode === 'keyboard' ? 'voice' : 'keyboard'
    this.setData({ inputMode })
  },

  onVoiceStart() {
    console.log('[voice] onVoiceStart called')
    this.setData({ recording: true })
    recorderManager.start({ format: 'wav', sampleRate: 16000, numberOfChannels: 1 })
    console.log('[voice] recorderManager.start called')
  },

  onVoiceEnd() {
    console.log('[voice] onVoiceEnd called')
    this.setData({ recording: false })
    recorderManager.stop()
    console.log('[voice] recorderManager.stop called')
  },

  async _initSessions() {
    try {
      const data = await fetchSessions()
      const sessions = data.sessions || []
      if (sessions.length === 0) {
        const s = await createSession('新对话')
        this.setData({ sessions: [s], currentSessionId: s.id, messages: [this._getWelcomeMsg()] })
      } else {
        this.setData({ sessions, currentSessionId: sessions[0].id })
        await this._loadHistory(sessions[0].id)
      }
    } catch (e) {
      console.warn('会话初始化失败', e)
    }
  },

  async _loadHistory(sessionId) {
    try {
      const data = await fetchHistory(50, sessionId)
      const messages = (data.messages || []).map((m, i) => ({
        id: `hist_${sessionId}_${i}`,
        role: m.role,
        segments: m.role === 'assistant'
          ? [{ type: 'text', html: mdToHtml(m.content) }]
          : [{ type: 'text', html: m.content }],
        rawText: m.content,
        done: true,
      }))
      
      if (messages.length === 0) {
        messages.push(this._getWelcomeMsg());
      }

      this.setData({ messages })
      this._scrollToBottom()
    } catch (e) {
      console.warn('历史加载失败', e)
    }
  },

  onCloseDrawer() { this.setData({ drawerOpen: false }) },
  onOpenDrawer() { this.setData({ drawerOpen: true }) },
  async onSelectSession(e) {
    const id = e.currentTarget.dataset.id
    if (id === this.data.currentSessionId) {
      this.setData({ drawerOpen: false })
      return
    }
    this.setData({ currentSessionId: id, messages: [], drawerOpen: false })
    await this._loadHistory(id)
  },

  async onNewSession() {
    try {
      const s = await createSession('新对话')
      this.setData({ sessions: [s, ...this.data.sessions], currentSessionId: s.id, messages: [this._getWelcomeMsg()], drawerOpen: false })
    } catch (e) {
      wx.showToast({ title: '新建失败', icon: 'none' })
    }
  },

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
              sessions = [s]; currentSessionId = s.id; messages = [this._getWelcomeMsg()]
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

  onOpenFarm() {
    wx.navigateTo({ url: '/pages/farm/farm' })
  },

  onInputChange(e) {
    this.setData({ inputText: e.detail.value })
  },

  async onSend() {
    const question = this.data.inputText.trim()
    const pendingImage = this.data.pendingImage

    // 发送消息时，把欢迎语下面的快捷按钮区域隐藏掉，保持界面清爽
    const updatedMessages = this.data.messages.map(m => {
      if (m.id === 'welcome_msg_0') {
        return { ...m, showSuggestions: false };
      }
      return m;
    });
    this.setData({ messages: updatedMessages });

    if (pendingImage) {
      this._uploadAndAnalyze(pendingImage, question)
      this.setData({ inputText: '', pendingImage: null })
      return
    }

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
          const already = this.data.sessions.some(s => s.id === session_id)
          const sessions = already
            ? this.data.sessions
            : [{ id: session_id, title, created_at: new Date().toISOString() }, ...this.data.sessions]
          this.setData({ currentSessionId: session_id, sessions })
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

  onChooseImage() {
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['album', 'camera'],
      success: (res) => {
        const tempFilePath = res.tempFiles[0].tempFilePath
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

    const aiMsgId = `a_${Date.now()}`
    const aiMsg = {
      id: aiMsgId,
      role: 'assistant',
      segments: [{ type: 'text', html: '<span style="color:#999;">正在识别图片...</span>' }],
      done: false,
    }
    this.setData({ messages: [...this.data.messages, aiMsg] })
    this._scrollToBottom()

    const token = wx.getStorageSync('token')
    const API_BASE = getApp().globalData.apiBase

    wx.uploadFile({
      url: `${API_BASE}/api/chat/upload_image`,
      filePath: filePath,
      name: 'file',
      formData: {
        mode: this.data.mode,
        session_id: this.data.currentSessionId,
        stream: 'false', 
        description: description || ''  
      },
      header: { Authorization: `Bearer ${token}` },
      success: (res) => {
        if (res.statusCode !== 200) {
          this._updateAiMsg(aiMsgId, [{ type: 'text', html: `<span style="color:#f44336;">识别失败：${res.data}</span>` }], true)
          this.setData({ sending: false })
          return
        }
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
    try {
      const result = typeof data === 'string' ? JSON.parse(data) : data

      if (result.type === 'error') {
        this._updateAiMsg(aiMsgId, [{ type: 'text', html: `<span style="color:#f44336;">${result.detail}</span>` }], true)
      } else if (result.type === 'low_confidence') {
        const segments = []
        if (result.segments && result.segments.length > 0) {
          const convertedSegments = buildSegments(result.segments)
          segments.push(...convertedSegments)
        } else if (result.llm_response) {
          segments.push({ type: 'text', html: mdToHtml(result.llm_response) })
        }
        this._updateAiMsg(aiMsgId, segments, true)
      } else if (result.type === 'success') {
        const segments = []
        if (result.cv_result) {
          segments.push({
            type: 'text',
            html: `<div style="background:#e3f2fd;padding:12px;border-radius:8px;margin-bottom:12px;"><strong>🔍 识别结果：</strong><br/>${result.cv_result.replace(/\n/g, '<br/>')}</div>`
          })
        }
        if (result.segments && result.segments.length > 0) {
          const convertedSegments = buildSegments(result.segments)
          segments.push(...convertedSegments)
        } else if (result.llm_response) {
          segments.push({ type: 'text', html: mdToHtml(result.llm_response) })
        }
        this._updateAiMsg(aiMsgId, segments, true)
      } else {
        this._updateAiMsg(aiMsgId, [{ type: 'text', html: result.content || data }], true)
      }
    } catch (e) {
      console.error('[parse] error:', e)
      this._updateAiMsg(aiMsgId, [{ type: 'text', html: `<span style="color:#f44336;">解析响应失败</span>` }], true)
    }
    this.setData({ sending: false })
    this._scrollToBottom()
  },

  onToggleMode() {
    this.setData({ mode: this.data.mode === 'pro' ? 'flash' : 'pro' })
  },

  onQuickAsk(e) {
    const q = e.currentTarget.dataset.q
    
    // 点击快捷问题时也隐藏按钮区域
    const updatedMessages = this.data.messages.map(m => {
      if (m.id === 'welcome_msg_0') {
        return { ...m, showSuggestions: false };
      }
      return m;
    });

    this.setData({ inputText: q, messages: updatedMessages }, () => this.onSend())
  },

  onClear() {
    wx.showModal({
      title: '清空对话', content: '确定清除当前会话的所有记录吗？', confirmColor: '#f44336',
      success: async ({ confirm }) => {
        if (!confirm) return
        try {
          await clearHistory(this.data.currentSessionId)
          this.setData({ messages: [this._getWelcomeMsg()] })
          wx.showToast({ title: '已清空', icon: 'success' })
        } catch (e) {
          wx.showToast({ title: '清空失败', icon: 'none' })
        }
      },
    })
  },

  onPreviewImage(e) {
    const { url } = e.currentTarget.dataset
    const allUrls = this.data.messages
      .flatMap(m => m.segments.filter(s => s.type === 'image').map(s => s.url))
    wx.previewImage({ current: url, urls: allUrls.length ? allUrls : [url] })
  },

  onImageError(e) {
    const { url } = e.currentTarget.dataset
    console.error('[image] load error:', url)
    wx.showToast({
      title: '图片加载失败，请检查网络连接',
      icon: 'none',
      duration: 2000
    })
  },

  onImageLoad(e) {
    const { url } = e.currentTarget.dataset
    console.log('[image] load success:', url, 'size:', e.detail.width, 'x', e.detail.height)
  },

  _scrollToBottom() {
    const messages = this.data.messages
    if (messages.length === 0) return
    this.setData({ scrollToId: messages[messages.length - 1].id })
  },
})