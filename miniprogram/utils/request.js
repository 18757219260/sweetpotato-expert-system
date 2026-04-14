
const app = getApp()
const { ensureLogin } = require('./auth')
const { API_BASE } = require('../config')


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
  
      }
    }
  }
  return events
}


async function streamChat({ question, mode = 'pro', sessionId = null, onText, onDone, onError }) {
  let token
  try {
    token = await ensureLogin()
  } catch (e) {
    onError && onError('登录失败，请重试')
    return
  }


  let buffer = ''
  let pendingBytes = []

  function flushBuffer() {
    if (!buffer.trim()) return
    const events = parseSSEChunk(buffer)
    buffer = ''
    for (const event of events) {
      if (event.type === 'text') onText && onText(event.content)
      else if (event.type === 'done') onDone && onDone({ images: event.images || [], segments: event.segments || [], cleanAnswer: event.clean_answer || '' })
      else if (event.type === 'error') onError && onError(event.detail || '服务器错误')
    }
  }

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
      
      flushBuffer()
     
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
      flushBuffer()
      onError && onError('网络连接失败，请检查网络后重试')
      console.error('[streamChat] fail', err)
    },
  })


  task.onChunkReceived((res) => {
 
    const arr = new Uint8Array(res.data)
  
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

 
    const parts = buffer.split('\n\n')

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


async function fetchHistory(limit = 20, sessionId = null) {
  const { authRequest } = require('./auth')
  const params = `limit=${limit}${sessionId ? `&session_id=${sessionId}` : ''}`
  const res = await authRequest({
    url: `${API_BASE}/api/history?${params}`,
    method: 'GET',
  })
  return res.data
}



async function clearHistory(sessionId = null) {
  const { authRequest } = require('./auth')
  const params = sessionId ? `?session_id=${sessionId}` : ''
  const res = await authRequest({
    url: `${API_BASE}/api/history/clear${params}`,
    method: 'POST',
  })
  return res.data
}


async function fetchSessions() {
  const { authRequest } = require('./auth')
  const res = await authRequest({ url: `${API_BASE}/api/sessions`, method: 'GET' })
  return res.data
}



async function createSession(title = '新对话') {
  const { authRequest } = require('./auth')
  const res = await authRequest({
    url: `${API_BASE}/api/sessions`,
    method: 'POST',
    data: { title },
  })
  return res.data
}


async function deleteSession(sessionId) {
  const { authRequest } = require('./auth')
  const res = await authRequest({
    url: `${API_BASE}/api/sessions/${sessionId}`,
    method: 'DELETE',
  })
  return res.data
}

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