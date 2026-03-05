// miniprogram/utils/markdown.js
// 轻量 Markdown → rich-text nodes 转换器
// 覆盖 LLM 常用格式：标题、加粗、斜体、行内代码、代码块、无序/有序列表、段落

/**
 * 将 Markdown 字符串转换为微信 rich-text 组件可用的 nodes 数组
 * @param {string} md
 * @returns {Array} rich-text nodes
 */
function mdToNodes(md) {
  if (!md) return []
  // 先转为 HTML，再用 rich-text 的 html 模式渲染
  return [{ type: 'node', name: 'div', attrs: { style: 'word-break:break-all;' },
    children: parseInline(md) }]
}

/**
 * Markdown → HTML 字符串（用于 rich-text innerHTML 模式）
 */
function mdToHtml(md) {
  if (!md) return ''

  const lines = md.split('\n')
  const result = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]

    // 代码块
    if (line.startsWith('```')) {
      const lang = line.slice(3).trim()
      const codeLines = []
      i++
      while (i < lines.length && !lines[i].startsWith('```')) {
        codeLines.push(escapeHtml(lines[i]))
        i++
      }
      result.push(
        `<pre style="background:#f6f8fa;padding:16rpx;border-radius:8rpx;overflow-x:auto;font-size:24rpx;line-height:1.5;">` +
        `<code>${codeLines.join('\n')}</code></pre>`
      )
      i++
      continue
    }

    const h4 = line.match(/^####\s+(.+)/)
    const h3 = line.match(/^###\s+(.+)/)
    const h2 = line.match(/^##\s+(.+)/)
    const h1 = line.match(/^#\s+(.+)/)
    if (h1) { result.push(`<div style="color:#2e7d32;margin:12rpx 0 6rpx;font-weight:bold;font-size:32rpx;">${inlineFormat(h1[1])}</div>`); i++; continue }
    if (h2) { result.push(`<div style="color:#388e3c;margin:10rpx 0 4rpx;font-weight:bold;font-size:30rpx;">${inlineFormat(h2[1])}</div>`); i++; continue }
    if (h3) { result.push(`<div style="color:#43a047;margin:8rpx 0 4rpx;font-weight:bold;font-size:28rpx;">${inlineFormat(h3[1])}</div>`); i++; continue }
    if (h4) { result.push(`<div style="color:#66bb6a;margin:6rpx 0 2rpx;font-weight:bold;font-size:26rpx;">${inlineFormat(h4[1])}</div>`); i++; continue }

    // 引用块（> ）
    if (line.match(/^>\s*/)) {
      const items = []
      while (i < lines.length && lines[i].match(/^>\s*/)) {
        items.push(inlineFormat(lines[i].replace(/^>\s*/, '')))
        i++
      }
      result.push(`<blockquote style="border-left:4rpx solid #4CAF50;padding:8rpx 16rpx;margin:8rpx 0;background:#f1f8f1;color:#388e3c;border-radius:0 8rpx 8rpx 0;">${items.join('<br/>')}</blockquote>`)
      continue
    }

    // 无序列表
    if (line.match(/^[-*+]\s+/)) {
      const items = []
      while (i < lines.length && lines[i].match(/^[-*+]\s+/)) {
        items.push(`<li style="margin:4rpx 0;">${inlineFormat(lines[i].replace(/^[-*+]\s+/, ''))}</li>`)
        i++
      }
      result.push(`<ul style="padding-left:40rpx;margin:8rpx 0;">${items.join('')}</ul>`)
      continue
    }

    // 有序列表
    if (line.match(/^\d+\.\s+/)) {
      const items = []
      while (i < lines.length && lines[i].match(/^\d+\.\s+/)) {
        items.push(`<li style="margin:4rpx 0;">${inlineFormat(lines[i].replace(/^\d+\.\s+/, ''))}</li>`)
        i++
      }
      result.push(`<ol style="padding-left:40rpx;margin:8rpx 0;">${items.join('')}</ol>`)
      continue
    }

    // 分割线
    if (line.match(/^---+$/) || line.match(/^\*\*\*+$/)) {
      result.push('<hr style="border:none;border-top:1px solid #e0e0e0;margin:12rpx 0;"/>')
      i++
      continue
    }

    // 空行 → 段落间距
    if (line.trim() === '') {
      result.push('<br/>')
      i++
      continue
    }

    // 普通段落
    result.push(`<p style="margin:6rpx 0;line-height:1.7;">${inlineFormat(line)}</p>`)
    i++
  }

  return result.join('')
}

/** 行内格式：加粗、斜体、行内代码 */
function inlineFormat(text) {
  return escapeHtml(text)
    // 先转义，再处理 markdown（因此下面用已转义后的符号）
    .replace(/\$\\rightarrow\$/g, '→')
    .replace(/\$\\Rightarrow\$/g, '⇒')
    // 加粗
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/__(.+?)__/g, '<strong>$1</strong>')
    // 斜体
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/_(.+?)_/g, '<em>$1</em>')
    // 行内代码
    .replace(/`([^`]+)`/g,
      '<code style="background:#f0f4f0;padding:2rpx 8rpx;border-radius:4rpx;font-size:24rpx;">$1</code>')
}

function parseInline(text) {
  // 简单包装，直接返回 HTML 字符串形式供 rich-text 使用
  return [{ type: 'node', name: 'span', attrs: {}, children: [{ type: 'text', text: text }] }]
}

function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

module.exports = { mdToHtml, mdToNodes }
