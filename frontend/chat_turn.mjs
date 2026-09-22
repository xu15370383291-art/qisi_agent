import { renderChatMessage } from './chat_render.mjs';

/**
 * Create one user/assistant turn and return the exact assistant node created
 * for it.  Callers must retain this reference instead of querying a shared
 * id: a chat can contain several completed assistant messages.
 */
export function appendPendingChatTurn(stream, userMessageHtml) {
  stream.insertAdjacentHTML(
    'beforeend',
    `<article class="message user"><span class="message-label">你</span><div class="message-bubble">${userMessageHtml}</div></article><article class="message assistant"><span class="message-label">启思学伴</span><div class="message-bubble">正在理解你的问题…</div></article>`,
  );
  return stream.lastElementChild;
}

export function setChatMessageText(messageNode, text) {
  renderChatMessage(messageNode, text);
}
