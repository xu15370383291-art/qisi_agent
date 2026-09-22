/** Safe, dependency-free rendering for model replies in the learning chat. */

const MATH_REPLACEMENTS = [
  [/\\parallel/g, '∥'], [/\\perp/g, '⊥'], [/\\angle/g, '∠'],
  [/\\triangle/g, '△'], [/\\square/g, '□'], [/\\circ/g, '°'],
  [/\\cdot/g, '·'], [/\\times/g, '×'], [/\\div/g, '÷'],
  [/\\leq/g, '≤'], [/\\geq/g, '≥'], [/\\neq/g, '≠'],
  [/\\rightarrow/g, '→'], [/\\Rightarrow/g, '⇒'], [/\\sim/g, '∽'],
];

export function normalizeMathNotation(value) {
  let text = String(value ?? '');
  for (const [pattern, replacement] of MATH_REPLACEMENTS) text = text.replace(pattern, replacement);
  return text
    .replace(/\$([^$\n]+)\$/g, '$1')
    .replace(/\\text\{([^}]*)\}/g, '$1')
    .replace(/[{}]/g, '');
}

function appendInline(parent, value) {
  const text = normalizeMathNotation(value);
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > cursor) parent.append(document.createTextNode(text.slice(cursor, match.index)));
    const token = match[0];
    const node = document.createElement(token.startsWith('**') ? 'strong' : token.startsWith('`') ? 'code' : 'em');
    node.textContent = token.startsWith('**') ? token.slice(2, -2) : token.slice(1, -1);
    parent.append(node);
    cursor = match.index + token.length;
  }
  if (cursor < text.length) parent.append(document.createTextNode(text.slice(cursor)));
}

function appendParagraph(parent, value, className = '') {
  const node = document.createElement('p');
  if (className) node.className = className;
  appendInline(node, value);
  parent.append(node);
}

export function renderMarkdownInto(container, value) {
  container.replaceChildren();
  const fragment = document.createDocumentFragment();
  let list = null;
  for (const rawLine of String(value ?? '').replace(/\r\n?/g, '\n').split('\n')) {
    const line = rawLine.trim();
    if (!line) { list = null; continue; }
    if (/^(-{3,}|\*{3,})$/.test(line)) {
      fragment.append(document.createElement('hr')); list = null; continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      const node = document.createElement(`h${Math.min(4, heading[1].length + 2)}`);
      appendInline(node, heading[2]); fragment.append(node); list = null; continue;
    }
    const quote = line.match(/^>\s?(.*)$/);
    if (quote) { appendParagraph(fragment, quote[1], 'chat-quote'); list = null; continue; }
    const bullet = line.match(/^[-*+]\s+(.+)$/);
    const ordered = line.match(/^\d+[.)]\s+(.+)$/);
    if (bullet || ordered) {
      const kind = ordered ? 'ol' : 'ul';
      if (!list || list.tagName.toLowerCase() !== kind) {
        list = document.createElement(kind); list.className = 'chat-list'; fragment.append(list);
      }
      const item = document.createElement('li'); appendInline(item, (bullet || ordered)[1]); list.append(item); continue;
    }
    appendParagraph(fragment, line); list = null;
  }
  if (!fragment.childNodes.length) appendParagraph(fragment, '');
  container.append(fragment);
}

export function renderChatMessage(messageNode, text) {
  const bubble = messageNode?.querySelector('.message-bubble');
  if (!bubble) return;
  if (messageNode.classList?.contains('assistant')) renderMarkdownInto(bubble, text);
  else bubble.textContent = text;
}
