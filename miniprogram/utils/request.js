// miniprogram/utils/request.js
// 流式 SSE 请求（微信小程序专用）
// 微信不支持标准 EventSource，使用 enableChunked + onChunkReceived

const app = getApp()
const { ensureLogin } = require('./auth')
const { API_BASE } = require('../config')

/**
 * 解析 SSE 数据帧，提取所有 data: 行的 JSON
 * 支持单次 onChunkReceived 中包含多个事件帧的情况
 */
function parseSSEChunk(rawText) {
  const events = []
  const lines = rawText.split('\n')
  for (const line of lines) {
    const trimmed = line.trim()
    if (trimmed.startsWith('data: ')) {
      try {
        const json = JSON.parse(trimmed.slice(6))
        events.push(json)
      } catch {
        // 不完整帧，忽略（由调用方拼接）
      }
    }
  }
  return events
}

/**
 * 发起流式问答请求
 *
 * @param {string} question - 用户提问
 * @param {function} onText - 每次收到文本增量时回调 (text: string) => void
 * @param {function} onDone - 完成时回调 ({ images: string[] }) => void
 * @param {function} onError - 出错时回调 (msg: string) => void
 */
async function streamChat({ question, mode = 'pro', sessionId = null, onText, onDone, onError }) {
  let token
  try {
    token = await ensureLogin()
  } catch (e) {
    onError && onError('登录失败，请重试')
    return
  }

  // 用于跨多个 chunk 拼接不完整的 SSE 帧
  let buffer = ''
  let pendingBytes = []

  // 1. 发起请求并保存 task 对象
  const task = wx.request({
    url: `${API_BASE}/api/chat/stream`,
    method: 'POST',
    enableChunked: true,
    header: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    data: JSON.stringify({ question, mode, session_id: sessionId }),

    success(res) {
      // 兜底：开发者工具不支持 enableChunked，完整响应会在 success.data 里一次性返回
      // 真机上 onChunkReceived 已处理，这里只处理 onChunkReceived 未触发的情况
      if (res.statusCode === 200 && res.data && typeof res.data === 'string' && res.data.length > 0) {
        const fullText = buffer + res.data
        const parts = fullText.split('\n\n')
        for (const part of parts) {
          const events = parseSSEChunk(part)
          for (const event of events) {
            if (event.type === 'text') {
              onText && onText(event.content)
            } else if (event.type === 'done') {
              onDone && onDone({ images: event.images || [], segments: event.segments || [], cleanAnswer: event.clean_answer || '' })
            } else if (event.type === 'error') {
              onError && onError(event.detail || '服务器错误')
            }
          }
        }
        return
      }
      if (res.statusCode === 401) {
        app.logout()
        onError && onError('登录已过期，请重新进入小程序')
      } else if (res.statusCode === 429) {
        onError && onError('今日提问次数已用完，请明天再试')
      }
    },

    fail(err) {
      onError && onError('网络连接失败，请检查网络后重试')
      console.error('[streamChat] fail', err)
    },
  })

  // 🌟 2. 核心修复：把流式监听器绑在 task 对象外面！
  task.onChunkReceived((res) => {
    // 微信小程序不支持 TextDecoder，手动解码 UTF-8 ArrayBuffer
    const arr = new Uint8Array(res.data)
    // 合并上次未完成的字节
    const bytes = new Uint8Array(pendingBytes.length + arr.length)
    bytes.set(pendingBytes)
    bytes.set(arr, pendingBytes.length)
    pendingBytes = []

    let text = ''
    let i = 0
    while (i < bytes.length) {
      const b = bytes[i]
      let charLen = 1
      if (b >= 0xF0) charLen = 4
      else if (b >= 0xE0) charLen = 3
      else if (b >= 0xC0) charLen = 2

      if (i + charLen > bytes.length) {
        // 多字节字符跨 chunk，暂存剩余字节
        pendingBytes = Array.from(bytes.slice(i))
        break
      }

      if (charLen === 1) {
        text += String.fromCharCode(b)
      } else {
        let code = b & (0xFF >> (charLen + 1))
        for (let j = 1; j < charLen; j++) {
          code = (code << 6) | (bytes[i + j] & 0x3F)
        }
        text += String.fromCodePoint(code)
      }
      i += charLen
    }

    buffer += text

    // 按双换行切分完整 SSE 帧
    const parts = buffer.split('\n\n')
    // 最后一段可能不完整，留在 buffer 中
    buffer = parts.pop()

    for (const part of parts) {
      const events = parseSSEChunk(part)
      for (const event of events) {
        if (event.type === 'text') {
          onText && onText(event.content)
        } else if (event.type === 'done') {
          onDone && onDone({ images: event.images || [], segments: event.segments || [], cleanAnswer: event.clean_answer || '' })
        } else if (event.type === 'error') {
          onError && onError(event.detail || '服务器错误')
        }
      }
    }
  })

  return task
}

/**
 * 拉取历史记录
 */
async function fetchHistory(limit = 20, sessionId = null) {
  const { authRequest } = require('./auth')
  const params = `limit=${limit}${sessionId ? `&session_id=${sessionId}` : ''}`
  const res = await authRequest({
    url: `${API_BASE}/api/history?${params}`,
    method: 'GET',
  })
  return res.data
}

/**
 * 清空历史记录
 */
async function clearHistory(sessionId = null) {
  const { authRequest } = require('./auth')
  const params = sessionId ? `?session_id=${sessionId}` : ''
  const res = await authRequest({
    url: `${API_BASE}/api/history/clear${params}`,
    method: 'POST',
  })
  return res.data
}

/**
 * 获取会话列表
 */
async function fetchSessions() {
  const { authRequest } = require('./auth')
  const res = await authRequest({ url: `${API_BASE}/api/sessions`, method: 'GET' })
  return res.data
}

/**
 * 新建会话
 */
async function createSession(title = '新对话') {
  const { authRequest } = require('./auth')
  const res = await authRequest({
    url: `${API_BASE}/api/sessions`,
    method: 'POST',
    data: { title },
  })
  return res.data
}

/**
 * 删除会话
 */
async function deleteSession(sessionId) {
  const { authRequest } = require('./auth')
  const res = await authRequest({
    url: `${API_BASE}/api/sessions/${sessionId}`,
    method: 'DELETE',
  })
  return res.data
}

/**
 * 重命名会话
 */
async function renameSession(sessionId, title) {
  const { authRequest } = require('./auth')
  const res = await authRequest({
    url: `${API_BASE}/api/sessions/${sessionId}`,
    method: 'PATCH',
    data: { title },
  })
  return res.data
}

module.exports = { streamChat, fetchHistory, clearHistory, fetchSessions, createSession, deleteSession, renameSession }