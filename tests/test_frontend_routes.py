"""Regression checks for the frontend application's deep-link contract."""

import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from qisi_agent.api import create_app
from qisi_agent.auth import AuthStore


ROOT = Path(__file__).parents[1]


class _StaticIndex:
    chunks: list = []
    embedding_model = "fixture"


class _StaticRag:
    index = _StaticIndex()


def test_role_deep_links_return_the_frontend_application():
    app = create_app(_StaticRag(), auth=AuthStore())
    client = TestClient(app)

    for path in (
        "/",
        "/student/home",
        "/student/chat/example-session",
        "/teacher/overview",
        "/teacher/students/example-student",
        "/admin/login",
        "/admin/content",
        "/admin/quality/retrieval",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert 'id="app"' in response.text
        assert "/static/app.js" in response.text


def test_frontend_static_assets_are_available_without_external_font_services():
    app = create_app(_StaticRag(), auth=AuthStore())
    client = TestClient(app)

    index = client.get("/").text
    styles = client.get("/static/styles.css").text
    script = client.get("/static/app.js").text
    chat_turn = client.get("/static/chat_turn.mjs").text
    chat_render = client.get("/static/chat_render.mjs").text

    assert "fonts.googleapis" not in index
    assert "--ink:#17324d" in styles
    assert "function renderPractice" in script
    assert "const chunk = item.chunk || {}" in script
    assert "chunk.text || item.excerpt" in script
    assert "function roleForPath" in script
    assert "function safeReturnPath" in script
    assert "TOKEN_STORAGE_KEY" in script
    assert "sessionStorage.getItem(TOKEN_STORAGE_KEY)" in script
    assert "remember_me" in script
    assert "记住登录状态" in script
    assert "if (path === '/') return navigate(firstPathFor(state.user.role), true);" in script
    assert "logoutAllDevices" in script
    assert "showPasswordModal" in script
    assert "ADMIN_IDLE_MS" in script
    assert "roleForPath() || state.authRole" in script
    assert "function forbiddenPanel" in script
    assert "function notFoundPanel" in script
    assert "role-picker" in script
    assert "data-toggle-password" in script
    assert "aria-live=\"polite\"" in script
    assert "if (path === '/student/home') return renderHome();" in script
    assert "function updateLearningContext" in script
    assert "function emitLearningEvent" in script
    assert "function saveQuizDraft" in script
    assert "qisi_quiz_draft" in script
    assert "emitLearningEvent('QUIZ_SUBMITTED'" in script
    assert "questions: (state.quiz.questions || [])" in script
    assert "item.selected" in script
    assert "function practicePath" in script
    assert "function chatPath" in script
    assert "function restoreQuiz" in script
    assert "function mistakeDetail" in script
    assert "function mistakeChatQuestion" in script
    assert "function resumeQuizDraft" in script
    assert "id=\"practice-resume\"" in script
    assert "id=\"mistake-point\"" in script
    assert "data-retry" in script
    assert "const draftQuestion = sessionStorage.getItem('qisi_chat_draft') || ''" in script
    assert "paintChat(root, conversation.messages || [], draftQuestion)" in script
    assert "已根据这道错题准备问题，你可以修改后再发送。" in script
    assert "input.value = draftQuestion" in script
    assert "data-chat-draft" in script
    assert "sessionStorage.setItem('qisi_chat_draft'" in script
    assert "data-chat-template" in script
    assert "function retryChat" in script
    assert "state.lastChatQuery" in script
    assert "new TextDecoder('utf-8', { fatal: true })" in script
    assert "buffer += decoder.decode();" in script
    assert "buffer.split(/\\r?\\n\\r?\\n/)" in script
    assert "setChatMessageText(pendingAnswer, answer)" in script
    assert "renderChatMessage(messageNode, text)" in chat_turn
    assert "import { renderChatMessage } from './chat_render.mjs';" in script
    assert "function renderMarkdownInto" in chat_render
    assert "normalizeMathNotation" in chat_render
    assert "document.createTextNode" in chat_render
    assert "innerHTML" not in chat_render
    assert ".chat-stream {" in styles
    assert "flex-direction: column;" in styles
    assert ".message.user {" in styles
    assert "align-self: flex-end;" in styles
    assert "import { appendPendingChatTurn, setChatMessageText } from './chat_turn.mjs';" in script
    assert "id=\"pending-answer\"" not in script
    assert "appendPendingChatTurn(stream, textLines(query))" in script
    send_chat_source = script[script.index("async function sendChat"):]
    assert "innerHTML = textLines(answer)" not in send_chat_source
    assert "const sessions = data.sessions || []" in script
    assert "学生最近问过什么" in script
    assert "继续同知识点" in script
    assert "MISTAKE_REVIEWED" in script
    assert "MEMORY_REVIEWED" in script
    assert "USER_STATUS_UPDATED" in script
    assert "CONTENT_IMPORT_PROGRESS" in script
    assert "qisi-learning-event" in script
    assert ".context-note" in styles
    assert "app.js?v=chat-display-21" in index
    assert "api('/api/documents/jobs?limit=12')" in script
    assert "function renderDocumentJobs" in script
    assert "version_count" in script
    assert "retry_count" in script
    assert "/retry`, { method: 'POST' }" in script
    assert "data-document-retry" in script
    assert "CONTENT_IMPORT_RETRY_QUEUED" in script
    assert "function renderAdminContentPreview" in script
    assert "/versions/${encodeURIComponent(versionId)}/preview" in script
    assert "查看解析后的知识片段" in script
    assert "function renderVersionHistory" in script
    assert "/publish`" in script
    assert "设为当前" in script
    assert ".preview-list" in styles
    assert ".version-history" in styles
    assert "teacher-point-filter" in script
    assert "需要关注的学生已优先排列" in script
    assert "filter-result-note" in styles
    assert "content-capabilities" in script
    assert "document-summary" in script
    assert "content-summary-grid" in styles


def test_two_chat_turns_keep_each_streamed_answer_on_its_own_message_node():
    helper = (ROOT / "frontend" / "chat_turn.mjs").as_uri()
    command = f'''
import {{ appendPendingChatTurn, setChatMessageText }} from "{helper}";
const messages = [];
const stream = {{
  lastElementChild: null,
  insertAdjacentHTML(_position, html) {{
    const node = {{
      html,
      dataset: {{}},
      bubble: {{ textContent: '' }},
      querySelector(selector) {{ return selector === '.message-bubble' ? this.bubble : null; }},
    }};
    messages.push(node);
    this.lastElementChild = node;
  }},
}};
const first = appendPendingChatTurn(stream, '第一问');
setChatMessageText(first, '第一轮回答');
const second = appendPendingChatTurn(stream, '第二问');
setChatMessageText(second, '第二轮回答');
if (messages.length !== 2 || first === second) throw new Error('两轮回答没有独立节点');
if (first.bubble.textContent !== '第一轮回答') throw new Error('第二轮覆盖了第一轮回答');
if (second.bubble.textContent !== '第二轮回答') throw new Error('第二轮回答没有写入自己的节点');
if (first.html.indexOf('message user') > first.html.indexOf('message assistant')) throw new Error('问题和回答的插入顺序错误');
'''
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_chat_renderer_formats_markdown_and_latex_without_injecting_html():
    renderer = (ROOT / "frontend" / "chat_render.mjs").as_uri()
    sample = "**例题**\n如图，$AB \\parallel CD$，且 $\\angle A = \\angle C$。\n---\n- $\\triangle ABC$\n<script>alert(1)</script>"
    command = f'''
import {{ renderChatMessage }} from "{renderer}";
class Node {{
  constructor(tagName, text = '') {{ this.tagName = tagName.toUpperCase(); this.children = []; this.className = ''; this._text = text; }}
  append(...nodes) {{ this.children.push(...nodes); }}
  replaceChildren(...nodes) {{ this.children = nodes; this._text = ''; }}
  get childNodes() {{ return this.children; }}
  get textContent() {{ return this._text + this.children.map((node) => node.textContent).join(''); }}
  set textContent(value) {{ this._text = String(value); this.children = []; }}
}}
globalThis.document = {{
  createElement: (tag) => new Node(tag),
  createTextNode: (text) => new Node('#text', text),
  createDocumentFragment: () => new Node('#fragment'),
}};
const bubble = new Node('div');
const message = {{ classList: {{ contains: (name) => name === 'assistant' }}, querySelector: () => bubble }};
renderChatMessage(message, {json.dumps(sample, ensure_ascii=False)});
const rendered = bubble.textContent;
const tags = [];
const walk = (node) => {{ tags.push(node.tagName); node.children.forEach(walk); }};
walk(bubble);
if (!rendered.includes('例题') || !rendered.includes('AB ∥ CD') || !rendered.includes('∠ A = ∠ C') || !rendered.includes('△ ABC')) throw new Error(rendered);
if (rendered.includes('**') || rendered.includes('$') || rendered.includes('\\\\parallel') || rendered.includes('\\\\angle')) throw new Error('raw formatting leaked: ' + rendered);
if (tags.includes('SCRIPT')) throw new Error('unsafe HTML was created');
if (!tags.includes('STRONG') || !tags.includes('HR') || !tags.includes('UL')) throw new Error('markdown blocks were not rendered');
'''
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_content_management_api_routes_are_registered():
    app = create_app(_StaticRag(), auth=AuthStore())
    paths = {(route.path, tuple(sorted(getattr(route, "methods", set()) or ()))) for route in app.routes}
    assert ("/api/documents/jobs/{job_id}/retry", ("POST",)) in paths
    assert ("/api/documents/{document_id}/versions/{version_id}/preview", ("GET",)) in paths
    assert ("/api/documents/{document_id}/versions", ("GET",)) in paths
    assert ("/api/documents/{document_id}/versions/{version_id}/publish", ("POST",)) in paths
