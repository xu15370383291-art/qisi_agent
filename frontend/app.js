const app = document.querySelector('#app');
const modalRoot = document.querySelector('#modal-root');
const toastNode = document.querySelector('#toast');
const state = {
  token: localStorage.getItem('qisi_access_token') || '',
  user: null,
  grade: localStorage.getItem('qisi_grade') || 'grade7',
  sessions: [],
  selectedSession: '',
  citations: [],
  chatAbort: null,
  quiz: null,
  quizAnswers: {},
  quizIndex: 0,
  quizResult: null,
  mistakes: [],
  authMode: 'login',
  authRole: 'student',
};

const gradeName = { grade7: '七年级', grade8: '八年级', grade9: '九年级' };
const roleName = { student: '学生', teacher: '教师', admin: '管理员' };
const $ = (selector, root = document) => root.querySelector(selector);
const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
const textLines = (value) => esc(value).replace(/\n/g, '<br />');
const dateText = (value) => {
  if (!value) return '刚刚';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value).slice(0, 10) : date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
};
const icon = (name) => {
  const paths = {
    home: '<path d="M3 10.5 12 3l9 7.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 19.5z"/><path d="M9 21v-6h6v6"/>',
    chat: '<path d="M20 15a4 4 0 0 1-4 4H8l-5 3V8a4 4 0 0 1 4-4h9a4 4 0 0 1 4 4z"/><path d="M8 10h8M8 14h5"/>',
    practice: '<path d="M8 3h8l3 3v15H5V6z"/><path d="M8 3v4h8M8 12h8M8 16h6"/>',
    mistakes: '<path d="M4 4h16v13H8l-4 4z"/><path d="m9 9 6 6m0-6-6 6"/>',
    growth: '<path d="M4 19V5m0 14h16"/><path d="m7 15 4-4 3 2 5-6"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.2 2.2-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.55V20.4h-3.1v-.11A1.7 1.7 0 0 0 10.5 18.7a1.7 1.7 0 0 0-1.88.34l-.06.06-2.2-2.2.06-.06A1.7 1.7 0 0 0 6.76 15a1.7 1.7 0 0 0-1.55-1.03H5.1v-3.1h.11A1.7 1.7 0 0 0 6.76 9.84a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.2-2.2.06.06a1.7 1.7 0 0 0 1.88.34 1.7 1.7 0 0 0 1.03-1.55V4.4h3.1v.11a1.7 1.7 0 0 0 1.03 1.55 1.7 1.7 0 0 0 1.88-.34l.06-.06 2.2 2.2-.06.06a1.7 1.7 0 0 0-.34 1.88 1.7 1.7 0 0 0 1.55 1.03h.11v3.1h-.11A1.7 1.7 0 0 0 19.4 15Z"/>',
    users: '<path d="M16 20v-1.5a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4V20"/><circle cx="9" cy="7" r="4"/><path d="M22 20v-1.5a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
    book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z"/>',
    review: '<path d="m20 6-11 11-5-5"/><path d="M20 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h10"/>',
    activity: '<path d="M3 12h3l2-7 4 14 2-7h7"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
    logout: '<path d="M10 17l5-5-5-5M15 12H3"/><path d="M21 19V5a2 2 0 0 0-2-2h-6"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    empty: '<path d="M5 4h14v16H5z"/><path d="M8 8h8M8 12h5"/>',
    copy: '<rect x="8" y="8" width="11" height="11" rx="1"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/>',
  };
  return `<svg class="icon" aria-hidden="true" viewBox="0 0 24 24">${paths[name] || paths.empty}</svg>`;
};

function showToast(message) {
  toastNode.textContent = message;
  toastNode.classList.add('show');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toastNode.classList.remove('show'), 2800);
}

function setModal(title, message, onConfirm, confirmLabel = '确认') {
  modalRoot.innerHTML = `<div class="modal-backdrop" role="presentation"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><h2 id="modal-title">${esc(title)}</h2><p>${esc(message)}</p><div class="modal-actions"><button class="btn secondary" data-modal-cancel>取消</button><button class="btn ${confirmLabel === '删除' ? 'danger' : 'primary'}" data-modal-confirm>${esc(confirmLabel)}</button></div></section></div>`;
  $('[data-modal-cancel]', modalRoot).focus();
  $('[data-modal-cancel]', modalRoot).onclick = () => { modalRoot.innerHTML = ''; };
  $('[data-modal-confirm]', modalRoot).onclick = async () => { modalRoot.innerHTML = ''; await onConfirm(); };
}

function headers(options = {}) {
  return { ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}), ...(options.headers || {}) };
}

async function api(url, options = {}) {
  const response = await fetch(url, { ...options, headers: headers(options) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.detail || '请求失败，请稍后重试');
    error.status = response.status;
    if (response.status === 401 && state.token) {
      localStorage.setItem('qisi_return_to', location.pathname);
      localStorage.removeItem('qisi_access_token');
      state.token = ''; state.user = null;
      renderAuth('登录已失效，请重新登录。');
    }
    throw error;
  }
  return data;
}

function navigate(path, replace = false) {
  if (location.pathname === path) return render();
  history[replace ? 'replaceState' : 'pushState']({}, '', path);
  render();
}

function route() {
  return location.pathname.replace(/\/+$/, '') || '/';
}

function firstPathFor(role) {
  return role === 'student' ? '/student/home' : role === 'teacher' ? '/teacher/overview' : '/admin/overview';
}

function roleForPath(path = route()) {
  if (path.startsWith('/admin')) return 'admin';
  if (path.startsWith('/teacher')) return 'teacher';
  if (path.startsWith('/student')) return 'student';
  return '';
}

function navFor(role) {
  const student = [
    ['/student/home', 'home', '今日学习'], ['/student/chat', 'chat', 'AI 学伴'], ['/student/practice', 'practice', '练习中心'],
    ['/student/mistakes', 'mistakes', '错题本'], ['/student/growth', 'growth', '学习成长'], ['/student/settings', 'settings', '设置'],
  ];
  const teacher = [
    ['/teacher/overview', 'home', '教学概览'], ['/teacher/students', 'users', '学生学情'], ['/teacher/knowledge', 'growth', '知识点学情'],
    ['/teacher/review', 'review', '线索审核'], ['/teacher/activity', 'activity', '教学活动'], ['/teacher/settings', 'settings', '设置'],
  ];
  const admin = [
    ['/admin/overview', 'home', '系统概览'], ['/admin/users', 'users', '用户与权限'], ['/admin/content', 'book', '教材与题库'],
    ['/admin/activity/questions', 'activity', '提问记录'], ['/admin/activity/mistakes', 'mistakes', '错题记录'], ['/admin/quality/retrieval', 'search', '检索质量'],
  ];
  return role === 'student' ? student : role === 'teacher' ? teacher : admin;
}

function activeNav(path, link) {
  return path === link || (link !== '/student/home' && path.startsWith(`${link}/`)) || (link === '/admin/activity/questions' && path === '/admin/activity/questions');
}

function shell(content, title) {
  const path = route();
  const nav = navFor(state.user.role);
  const initials = (state.user.display_name || state.user.username || '启').slice(0, 1);
  const navItems = nav.map(([link, glyph, label]) => `<a class="nav-link ${activeNav(path, link) ? 'active' : ''}" href="${link}">${icon(glyph)}<span>${label}</span></a>`).join('');
  const mobile = nav.slice(0, 5).map(([link, glyph, label]) => `<a class="mobile-link ${activeNav(path, link) ? 'active' : ''}" href="${link}">${icon(glyph)}<span>${label}</span></a>`).join('');
  app.innerHTML = `<div class="app-shell"><aside class="side-nav"><a class="brand" href="${firstPathFor(state.user.role)}"><span class="brand-mark">启</span><span><b>启思学伴</b><small>${roleName[state.user.role]}工作台</small></span></a><span class="nav-label">${state.user.role === 'student' ? '学习路径' : state.user.role === 'teacher' ? '教学管理' : '系统管理'}</span><nav class="nav-list" aria-label="主导航">${navItems}</nav><div class="role-note"><small>${esc(state.user.display_name || state.user.username)} · ${roleName[state.user.role]}</small><button type="button" data-action="logout">退出登录</button></div></aside><section class="page"><header class="topbar"><div class="crumb">启思学伴　/　<strong>${esc(title)}</strong></div><div class="top-actions">${state.user.role === 'student' ? `<label class="grade-control">年级 <select id="grade-switch"><option value="grade7">七年级</option><option value="grade8">八年级</option><option value="grade9">九年级</option></select></label>` : ''}<span class="avatar" aria-label="当前用户">${esc(initials)}</span></div></header><main id="main-content" class="main" tabindex="-1">${content}</main></section><nav class="mobile-nav" aria-label="移动端主导航">${mobile}</nav></div>`;
  const gradeSwitch = $('#grade-switch');
  if (gradeSwitch) { gradeSwitch.value = state.grade; gradeSwitch.onchange = () => { state.grade = gradeSwitch.value; localStorage.setItem('qisi_grade', state.grade); render(); }; }
}

function loading() {
  return '<div class="panel"><div class="skeleton" style="width:32%"></div><div class="skeleton" style="width:88%"></div><div class="skeleton" style="width:73%"></div></div>';
}

function errorPanel(error, retry) {
  return `<div class="empty"><div>${icon('empty')}</div><b>暂时无法加载</b><p>${esc(error.message || '网络连接异常，请检查网络后再试。')}</p><button class="btn secondary" data-retry="${retry}">重新加载</button></div>`;
}

function renderAuth(message = '') {
  const pathRole = roleForPath();
  if (pathRole) state.authRole = pathRole;
  const isRegister = state.authMode === 'register';
  const roles = [['student', '学生'], ['teacher', '教师'], ['admin', '管理员']];
  app.innerHTML = `<main class="auth-page"><section class="auth-card" aria-labelledby="auth-heading"><div class="auth-brand"><span class="brand-mark">启</span><span><h1>启思学伴</h1><p>把每一步想明白</p></span></div><h2 id="auth-heading">${isRegister ? '创建学习账号' : '进入学习空间'}</h2><p>${isRegister ? '选择身份后创建账号。管理员账号需要专用邀请码。' : '选择你的入口，继续上次的学习。'}</p><div class="role-tabs" role="tablist">${roles.map(([value, label]) => `<button type="button" data-auth-role="${value}" class="${state.authRole === value ? 'active' : ''}" role="tab" aria-selected="${state.authRole === value}">${label}</button>`).join('')}</div><form id="auth-form" class="auth-form"><label class="field">用户名<input name="username" autocomplete="username" minlength="3" maxlength="64" required placeholder="至少 3 个字符" /></label>${isRegister ? '<label class="field">显示名称<input name="display_name" maxlength="80" placeholder="例如：林同学" /></label>' : ''}<label class="field">密码<input name="password" type="password" autocomplete="${isRegister ? 'new-password' : 'current-password'}" minlength="6" maxlength="128" required placeholder="至少 6 个字符" /></label>${state.authRole === 'admin' && isRegister ? '<label class="field">管理员邀请码<input name="application_code" required placeholder="请输入邀请码" /></label>' : ''}<p class="form-error" id="auth-error">${esc(message)}</p><button class="btn primary" type="submit">${isRegister ? '创建账号' : '登录'} ${icon('arrow')}</button></form><p class="auth-switch">${isRegister ? '已有账号？' : '还没有账号？'} <button type="button" data-action="switch-auth">${isRegister ? '去登录' : '注册一个'}</button></p></section></main>`;
  document.querySelectorAll('[data-auth-role]').forEach((button) => { button.onclick = () => { state.authRole = button.dataset.authRole; renderAuth(); }; });
  $('[data-action="switch-auth"]').onclick = () => { state.authMode = isRegister ? 'login' : 'register'; renderAuth(); };
  $('#auth-form').onsubmit = submitAuth;
}

async function submitAuth(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const payload = { username: form.get('username').trim(), password: form.get('password'), role: roleForPath() || state.authRole };
  if (state.authMode === 'register') payload.display_name = form.get('display_name').trim();
  const button = $('button[type="submit"]', event.currentTarget);
  button.disabled = true;
  try {
    const endpoint = state.authMode === 'register' && state.authRole === 'admin' ? '/api/auth/admin-register' : `/api/auth/${state.authMode === 'register' ? 'register' : 'login'}`;
    if (endpoint.endsWith('admin-register')) { payload.application_code = form.get('application_code').trim(); delete payload.role; }
    const data = await api(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    state.token = data.access_token; state.user = data.user; localStorage.setItem('qisi_access_token', state.token);
    const returnTo = localStorage.getItem('qisi_return_to'); localStorage.removeItem('qisi_return_to');
    const destination = returnTo && returnTo.startsWith(`/${state.user.role === 'admin' ? 'admin' : state.user.role}`) ? returnTo : firstPathFor(state.user.role);
    history.replaceState({}, '', destination);
    await render();
  } catch (error) {
    $('#auth-error').textContent = error.message;
    button.disabled = false;
  }
}

function renderHome() {
  shell(`<div class="page-intro"><span class="eyebrow">今日学习</span><h1>先完成一个清晰的动作。</h1><p>从理解、练习、订正到掌握，每一步都知道接下来做什么。</p></div><div id="home-data">${loading()}</div>`, '今日学习');
  loadHome();
}

async function loadHome() {
  const root = $('#home-data');
  try {
    const [profile, mistakes, sessions] = await Promise.all([
      api(`/api/students/${encodeURIComponent(state.user.user_id)}/learning-profile`),
      api(`/api/students/${encodeURIComponent(state.user.user_id)}/mistakes`),
      api('/api/conversations'),
    ]);
    state.mistakes = mistakes.items || []; state.sessions = sessions.items || [];
    const pending = state.mistakes.filter((item) => item.status !== 'reviewed');
    const mastery = profile.mastery || [];
    const weak = mastery[0];
    const task = pending.length ? { title: `订正「${pending[0].knowledge_point_id || '待归类知识点'}」`, copy: `有 ${pending.length} 道错题等待复习。先回到其中一题，找出当时卡住的步骤。`, href: '/student/mistakes' } : weak ? { title: `巩固「${weak.knowledge_point}」`, copy: `当前掌握度 ${weak.score}%。完成一组同知识点练习，验证这次是否真正理解。`, href: `/student/practice/setup?focus=${encodeURIComponent(weak.knowledge_point)}` } : { title: '完成第一组学习练习', copy: '从 5 道题开始，系统会根据你的答题结果给出下一步建议。', href: '/student/practice/setup' };
    const avg = mastery.length ? Math.round(mastery.reduce((total, item) => total + Number(item.score || 0), 0) / mastery.length) : '—';
    root.innerHTML = `<section class="task-card"><div><span class="eyebrow">今天先做什么</span><h2>${esc(task.title)}</h2><p>${esc(task.copy)}</p><a class="btn primary" href="${task.href}">开始这一步 ${icon('arrow')}</a></div><div class="task-meta"><span>${pending.length ? `${pending.length} 道待复习` : '预计 8–10 分钟'}</span><br />完成后会更新学习建议</div></section><div class="learning-path" aria-label="学习路径"><div class="path-step done"><i class="path-dot">1</i><span>理解</span></div><i class="path-line done"></i><div class="path-step ${state.sessions.length ? 'done' : 'current'}"><i class="path-dot">2</i><span>练习</span></div><i class="path-line"></i><div class="path-step ${pending.length ? 'current' : ''}"><i class="path-dot">3</i><span>订正</span></div><i class="path-line"></i><div class="path-step"><i class="path-dot">4</i><span>掌握</span></div></div><div class="stat-grid"><article class="stat"><span>待复习错题</span><strong>${pending.length}</strong><span>优先解决已出现的错误</span></article><article class="stat yellow"><span>学习线索</span><strong>${profile.memory_count || 0}</strong><span>来自提问和练习</span></article><article class="stat blue"><span>平均掌握度</span><strong>${avg}${avg === '—' ? '' : '%'}</strong><span>${mastery.length ? '基于现有学习记录' : '完成练习后生成'}</span></article><article class="stat red"><span>继续上次学习</span><strong>${state.sessions.length}</strong><span>${state.sessions.length ? '个学习会话可继续' : '还没有学习会话'}</span></article></div><div class="grid-two" style="margin-top:22px"><section class="panel"><div class="panel-head"><div><span class="eyebrow">建议复习</span><h2>先处理这些知识点</h2></div><a class="btn quiet small" href="/student/mistakes">查看错题本</a></div><div class="review-list">${pending.slice(0, 3).map((item) => `<div class="review-item"><span class="item-sign danger">${icon('mistakes')}</span><div class="item-copy"><b>${esc(item.knowledge_point_id || '待归类错题')}</b><small>${esc(item.prompt || '查看题目、答案与解析')}</small></div><a class="mini-link" href="/student/mistakes/${encodeURIComponent(item.mistake_id)}">复习</a></div>`).join('') || `<div class="empty"><div>${icon('practice')}</div><b>今天没有待复习错题</b><p>完成一组练习，系统会把答错的题目整理到这里。</p><a class="btn secondary small" href="/student/practice/setup">开始练习</a></div>`}</div></section><section class="panel"><div class="panel-head"><div><span class="eyebrow">学习成长</span><h2>掌握度变化</h2></div><a class="btn quiet small" href="/student/growth">查看全部</a></div><div class="mastery-list">${mastery.slice(0, 4).map(masteryMarkup).join('') || `<div class="empty"><div>${icon('growth')}</div><b>还没有足够的数据</b><p>完成一次对话或练习后，这里会解释哪个知识点发生了变化。</p></div>`}</div></section></div>`;
  } catch (error) { if (root) root.innerHTML = errorPanel(error, 'home'); }
}

function masteryMarkup(item) {
  const score = Number(item.score || 0); const tone = score < 45 ? 'low' : score < 80 ? 'mid' : '';
  return `<div class="mastery-row"><b>${esc(item.knowledge_point)}</b><span class="meter"><i class="${tone}" style="width:${Math.max(3, score)}%"></i></span><span class="meter-score">${score}%</span></div>`;
}

function renderChat() {
  shell(`<div class="page-intro"><span class="eyebrow">AI 学伴</span><h1>把卡住的那一步说出来。</h1><p>讲解会标明教材依据；完成后，可以继续做同知识点练习。</p></div><div id="chat-root">${loading()}</div>`, 'AI 学伴');
  loadChat();
}

async function loadChat() {
  const root = $('#chat-root');
  try {
    const sessions = await api('/api/conversations');
    state.sessions = sessions.items || [];
    const parts = route().split('/');
    const inPath = parts.length > 3 ? decodeURIComponent(parts[3]) : '';
    state.selectedSession = state.sessions.some((item) => item.session_id === inPath) ? inPath : (state.sessions[0]?.session_id || '');
    if (!state.selectedSession) {
      const created = await api('/api/conversations', { method: 'POST' });
      state.selectedSession = created.session_id; state.sessions = [created];
    }
    const conversation = await api(`/api/conversations/${encodeURIComponent(state.selectedSession)}`);
    paintChat(root, conversation.messages || []);
  } catch (error) { if (root) root.innerHTML = errorPanel(error, 'chat'); }
}

function paintChat(root, messages) {
  const messageHtml = messages.length ? messages.map((item, index) => `<article class="message ${item.role === 'user' ? 'user' : 'assistant'}"><span class="message-label">${item.role === 'user' ? '你' : '启思学伴'}</span><div class="message-bubble">${textLines(item.content || '')}</div>${item.role !== 'user' ? `<div class="message-actions"><button data-copy-message="${index}">复制</button><button data-chat-practice>练一道类似题</button></div>` : ''}</article>`).join('') : `<div class="empty"><div>${icon('chat')}</div><b>从一个具体问题开始</b><p>例如：“一元一次方程移项时，为什么符号要变？”</p></div>`;
  root.innerHTML = `<div class="chat-layout"><aside class="chat-sessions"><div class="panel-head"><div><span class="eyebrow">学习会话</span><h2>历史对话</h2></div><button class="btn quiet small" data-chat-new aria-label="新建对话">${icon('plus')}</button></div><div class="session-list">${state.sessions.map((item) => `<button class="session-item ${item.session_id === state.selectedSession ? 'active' : ''}" data-session-id="${esc(item.session_id)}"><b>${esc(item.title || '新学习对话')}</b><small>${item.message_count || 0} 条消息 · ${dateText(item.updated_at)}</small></button>`).join('')}</div>${state.sessions.length > 1 ? '<button class="btn quiet small" data-chat-delete>删除当前会话</button>' : ''}</aside><section class="chat-panel"><header class="chat-meta"><b>${esc(gradeName[state.grade])}数学</b><span id="chat-status">准备回答</span></header><div class="chat-stream" id="chat-stream">${messageHtml}</div><form id="chat-form" class="chat-composer"><textarea id="chat-input" rows="3" maxlength="1200" placeholder="写下你的问题，例如：这道题我不知道从哪一步开始。" aria-label="输入问题"></textarea><div class="composer-footer"><small>Enter 发送 · Shift + Enter 换行</small><div><button class="btn quiet small" type="button" id="chat-stop" hidden>停止</button><button class="btn primary" type="submit">发送问题 ${icon('arrow')}</button></div></div></form></section><aside class="chat-evidence"><span class="eyebrow">教材依据</span><h2 style="font-size:18px;margin:0">讲解过程</h2><ol class="stage-list"><li class="active" data-stage="understand"><i></i>理解问题</li><li data-stage="retrieve"><i></i>查找教材</li><li data-stage="answer"><i></i>组织讲解</li></ol><div class="citation-list" id="citation-list"><div class="empty"><div>${icon('book')}</div><b>依据会显示在这里</b><p>提出问题后，可展开查看教材章节和引用片段。</p></div></div></aside></div>`;
  const stream = $('#chat-stream'); stream.scrollTop = stream.scrollHeight;
  $('[data-chat-new]').onclick = createChat;
  const deleteButton = $('[data-chat-delete]'); if (deleteButton) deleteButton.onclick = deleteChat;
  document.querySelectorAll('[data-session-id]').forEach((button) => { button.onclick = () => navigate(`/student/chat/${encodeURIComponent(button.dataset.sessionId)}`); });
  $('#chat-form').onsubmit = sendChat;
  $('#chat-input').onkeydown = (event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); $('#chat-form').requestSubmit(); } };
  $('#chat-stop').onclick = () => state.chatAbort?.abort();
  document.querySelectorAll('[data-copy-message]').forEach((button) => { button.onclick = async () => { try { await navigator.clipboard.writeText(messages[Number(button.dataset.copyMessage)]?.content || ''); showToast('已复制回答'); } catch { showToast('无法访问剪贴板，请手动复制'); } }; });
  document.querySelectorAll('[data-chat-practice]').forEach((button) => { button.onclick = () => navigate('/student/practice/setup'); });
}

async function createChat() {
  try { const created = await api('/api/conversations', { method: 'POST' }); state.selectedSession = created.session_id; navigate(`/student/chat/${encodeURIComponent(created.session_id)}`); } catch (error) { showToast(error.message); }
}

function deleteChat() {
  setModal('删除当前会话？', '删除后无法恢复其中的对话记录。', async () => { try { await api(`/api/conversations/${encodeURIComponent(state.selectedSession)}`, { method: 'DELETE' }); showToast('会话已删除'); navigate('/student/chat', true); } catch (error) { showToast(error.message); } }, '删除');
}

function stage(name) {
  document.querySelectorAll('[data-stage]').forEach((node) => node.classList.toggle('active', node.dataset.stage === name || (name === 'answer' && node.dataset.stage !== 'answer')));
}

function renderCitations(items) {
  const root = $('#citation-list'); if (!root) return;
  root.innerHTML = items.length ? items.map((item) => `<article class="citation"><b>${esc(item.title || item.chapter || '教材片段')}</b><p>${esc(item.excerpt || item.content || '未提供片段')}</p><small>${esc(item.chapter || item.source || '教材依据')}</small></article>`).join('') : '<div class="empty"><p>没有检索到可展示的教材依据。</p></div>';
}

async function sendChat(event) {
  event.preventDefault();
  const input = $('#chat-input'); const query = input.value.trim(); if (!query || state.chatAbort) return;
  input.value = ''; state.citations = [];
  const stream = $('#chat-stream');
  stream.insertAdjacentHTML('beforeend', `<article class="message user"><span class="message-label">你</span><div class="message-bubble">${textLines(query)}</div></article><article class="message assistant" id="pending-answer"><span class="message-label">启思学伴</span><div class="message-bubble">正在理解你的问题…</div></article>`);
  stream.scrollTop = stream.scrollHeight; $('#chat-status').textContent = '正在生成'; $('#chat-stop').hidden = false; stage('understand');
  const controller = new AbortController(); state.chatAbort = controller;
  try {
    const response = await fetch('/api/chat/stream', { method: 'POST', headers: { ...headers(), 'Content-Type': 'application/json' }, body: JSON.stringify({ query, session_id: state.selectedSession, grade_id: state.grade, course_id: 'math' }), signal: controller.signal });
    if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.detail || '无法开始回答'); }
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''; let answer = '';
    while (true) {
      const result = await reader.read(); if (result.done) break; buffer += decoder.decode(result.value, { stream: true });
      const packets = buffer.split('\n\n'); buffer = packets.pop() || '';
      packets.forEach((packet) => {
        const eventName = (packet.match(/event: (.+)/) || [])[1]; const raw = (packet.match(/data: (.+)/) || [])[1]; if (!raw) return;
        let data; try { data = JSON.parse(raw); } catch { return; }
        if (eventName === 'retrieval') { stage('retrieve'); }
        if (eventName === 'citation') { state.citations.push(data); renderCitations(state.citations); }
        if (eventName === 'answer_delta') { stage('answer'); answer += data.delta || ''; const pending = $('#pending-answer'); if (pending) $('.message-bubble', pending).innerHTML = textLines(answer); stream.scrollTop = stream.scrollHeight; }
        if (eventName === 'error') throw new Error(data.message || '回答生成失败');
      });
    }
    if (!answer) { const pending = $('#pending-answer'); if (pending) $('.message-bubble', pending).textContent = '这次没有生成可用回答。请换一种问法后重试。'; }
    $('#chat-status').textContent = '回答完成';
  } catch (error) {
    const pending = $('#pending-answer'); if (pending) $('.message-bubble', pending).textContent = error.name === 'AbortError' ? '已停止生成。你可以修改问题后再次发送。' : `回答未完成：${error.message}`;
    $('#chat-status').textContent = error.name === 'AbortError' ? '已停止' : '可重试';
  } finally { state.chatAbort = null; const stop = $('#chat-stop'); if (stop) stop.hidden = true; }
}

function renderPractice() {
  const path = route();
  if (/\/result$/.test(path)) return renderPracticeResult();
  if (/^\/student\/practice\/[^/]+$/.test(path) && !path.endsWith('/setup')) return renderQuiz();
  shell(`<div class="page-intro"><span class="eyebrow">练习中心</span><h1>把刚学的内容用起来。</h1><p>先设置练习范围。作答时不会提前显示正确答案，提交后再逐题复盘。</p></div><section class="panel setup-card"><span class="eyebrow">组卷设置</span><h2 style="margin:0">开始一组专注练习</h2><div class="form-grid"><label class="field">年级<select id="practice-grade"><option value="grade7">七年级</option><option value="grade8">八年级</option><option value="grade9">九年级</option></select></label><label class="field">难度<select id="practice-difficulty"><option value="">智能推荐</option><option value="基础">基础</option><option value="进阶">进阶</option><option value="综合">综合</option><option value="易错">易错</option></select></label><label class="field">知识点<select id="practice-category"><option value="">全部知识点</option></select></label><label class="field">题量<select id="practice-count"><option value="5">5 题 · 约 10 分钟</option><option value="8">8 题 · 约 15 分钟</option><option value="10">10 题 · 约 20 分钟</option></select></label></div><p class="field-help">提示：完成练习后，错误题目会自动进入错题本，掌握度也会相应更新。</p><button class="btn primary" id="practice-start">生成练习 ${icon('arrow')}</button></section>`, '练习中心');
  $('#practice-grade').value = state.grade;
  $('#practice-grade').onchange = () => { state.grade = $('#practice-grade').value; localStorage.setItem('qisi_grade', state.grade); loadCategories(); };
  $('#practice-start').onclick = startPractice;
  loadCategories();
}

async function loadCategories() {
  const select = $('#practice-category'); if (!select) return;
  select.innerHTML = '<option value="">正在加载知识点…</option>';
  try { const data = await api(`/api/practice/categories?grade_id=${encodeURIComponent(state.grade)}`); select.innerHTML = `<option value="">全部知识点</option>${(data.items || []).map((item) => `<option value="${esc(item.knowledge_point)}">${esc(item.chapter ? `${item.chapter} · ` : '')}${esc(item.knowledge_point)}</option>`).join('')}`; const focus = new URLSearchParams(location.search).get('focus'); if (focus) select.value = focus; } catch (error) { select.innerHTML = '<option value="">暂时无法加载知识点</option>'; showToast(error.message); }
}

async function startPractice() {
  const button = $('#practice-start'); button.disabled = true; button.textContent = '正在生成…';
  try {
    const params = new URLSearchParams({ grade_id: $('#practice-grade').value, count: $('#practice-count').value });
    if ($('#practice-difficulty').value) params.set('difficulty', $('#practice-difficulty').value);
    if ($('#practice-category').value) params.set('knowledge_point', $('#practice-category').value);
    const quiz = await api(`/api/practice/quiz?${params}`); state.quiz = quiz; state.quizAnswers = {}; state.quizIndex = 0; state.quizResult = null;
    sessionStorage.setItem('qisi_quiz', JSON.stringify(quiz)); navigate(`/student/practice/${encodeURIComponent(quiz.quiz_id)}`);
  } catch (error) { showToast(error.message); button.disabled = false; button.textContent = '生成练习'; }
}

function restoreQuiz() {
  if (!state.quiz) { try { state.quiz = JSON.parse(sessionStorage.getItem('qisi_quiz') || 'null'); } catch { state.quiz = null; } }
  return state.quiz;
}

function renderQuiz() {
  const quiz = restoreQuiz();
  if (!quiz) { navigate('/student/practice/setup', true); return; }
  const question = quiz.questions[state.quizIndex]; const choices = question.options || [];
  shell(`<div class="page-intro"><span class="eyebrow">正在作答 · 第 ${state.quizIndex + 1} / ${quiz.questions.length} 题</span><h1>先独立想一想。</h1><p>标记不确定或暂时跳过都没关系，提交前可以回到任意一题检查。</p></div><div class="quiz-layout"><section class="panel question-card"><span class="tag">${esc(question.knowledge_point || '教材练习')}</span><h2>${esc(question.prompt || question.question || '')}</h2><div id="option-list">${choices.map((choice, index) => `<button class="option ${state.quizAnswers[question.question_id] === index ? 'selected' : ''}" data-answer="${index}"><b>${String.fromCharCode(65 + index)}</b><span>${esc(choice)}</span></button>`).join('')}</div><div class="quiz-actions"><button class="btn secondary" id="quiz-prev" ${state.quizIndex === 0 ? 'disabled' : ''}>上一题</button>${state.quizIndex === quiz.questions.length - 1 ? '<button class="btn primary" id="quiz-submit">提交并查看结果</button>' : '<button class="btn primary" id="quiz-next">下一题</button>'}</div></section><aside class="panel"><span class="eyebrow">答题导航</span><div class="quiz-nav">${quiz.questions.map((item, index) => `<button class="${index === state.quizIndex ? 'current' : ''} ${state.quizAnswers[item.question_id] !== undefined ? 'answered' : ''}" data-question-index="${index}" aria-label="第 ${index + 1} 题">${index + 1}</button>`).join('')}</div><p class="field-help" style="margin-top:16px">已回答 ${Object.keys(state.quizAnswers).length} / ${quiz.questions.length} 题</p></aside></div>`, '练习作答');
  document.querySelectorAll('[data-answer]').forEach((button) => { button.onclick = () => { state.quizAnswers[question.question_id] = Number(button.dataset.answer); sessionStorage.setItem('qisi_quiz_answers', JSON.stringify(state.quizAnswers)); renderQuiz(); }; });
  document.querySelectorAll('[data-question-index]').forEach((button) => { button.onclick = () => { state.quizIndex = Number(button.dataset.questionIndex); renderQuiz(); }; });
  $('#quiz-prev').onclick = () => { state.quizIndex -= 1; renderQuiz(); };
  const next = $('#quiz-next'); if (next) next.onclick = () => { state.quizIndex += 1; renderQuiz(); };
  const submit = $('#quiz-submit'); if (submit) submit.onclick = submitQuiz;
}

async function submitQuiz() {
  const total = state.quiz.questions.length; const answered = Object.keys(state.quizAnswers).length;
  if (answered < total) { setModal('还有题目未作答', `你已完成 ${answered}/${total} 题。未作答题目将按错误处理，仍要提交吗？`, sendQuiz, '仍要提交'); return; }
  await sendQuiz();
}

async function sendQuiz() {
  const button = $('#quiz-submit'); if (button) { button.disabled = true; button.textContent = '正在评分…'; }
  try { state.quizResult = await api('/api/practice/submit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ quiz_id: state.quiz.quiz_id, answers: state.quizAnswers }) }); sessionStorage.setItem('qisi_quiz_result', JSON.stringify(state.quizResult)); navigate(`/student/practice/${encodeURIComponent(state.quiz.quiz_id)}/result`); } catch (error) { showToast(error.message); if (button) { button.disabled = false; button.textContent = '提交并查看结果'; } }
}

function renderPracticeResult() {
  if (!state.quizResult) { try { state.quizResult = JSON.parse(sessionStorage.getItem('qisi_quiz_result') || 'null'); } catch { state.quizResult = null; } }
  const result = state.quizResult; if (!result) { navigate('/student/practice/setup', true); return; }
  const results = result.results || []; const correct = results.filter((item) => item.is_correct).length; const percent = results.length ? Math.round(correct / results.length * 100) : 0;
  shell(`<div class="page-intro"><span class="eyebrow">练习复盘</span><h1>这次完成了 ${correct} / ${results.length} 题。</h1><p>${percent >= 80 ? '掌握得不错。趁现在继续巩固，让正确的方法更稳定。' : '先看错因和解析，再练一次相同知识点。'}</p></div><section class="task-card"><div><span class="eyebrow">本次结果</span><h2>正确率 ${percent}%</h2><p>错题已加入错题本。查看解析后，可只练错题涉及的知识点。</p><div class="record-actions"><a class="btn primary" href="/student/mistakes">去复习错题</a><a class="btn secondary" href="/student/practice/setup">再做一组练习</a></div></div><div class="task-meta"><span>${results.length - correct} 道需要订正</span><br />掌握度已根据答题结果更新</div></section><section class="panel" style="margin-top:22px"><div class="panel-head"><div><span class="eyebrow">逐题解析</span><h2>找出下次可以做对的步骤</h2></div></div><div class="data-list">${results.map((item, index) => `<article class="record-card ${item.is_correct ? 'reviewed' : ''}"><div class="panel-head"><div><span class="tag ${item.is_correct ? '' : 'danger'}">${item.is_correct ? '回答正确' : '需要订正'}</span><h3>第 ${index + 1} 题 · ${esc(item.knowledge_point || '未归类')}</h3></div><span class="meter-score">${item.is_correct ? '正确' : '错误'}</span></div><p><b>你的答案：</b>${esc(item.student_answer ?? '未作答')}　<b>正确答案：</b>${esc(item.correct_answer ?? '—')}</p><p>${esc(item.explanation || '暂无解析')}</p>${item.citation ? `<p class="field-help">教材来源：${esc(item.citation.title || item.citation.chapter || '相关教材片段')}</p>` : ''}<div class="record-actions">${!item.is_correct ? `<a class="btn secondary small" href="/student/chat">问 AI 讲解</a>` : ''}</div></article>`).join('')}</div></section>`, '练习复盘');
}

function renderMistakes() {
  shell(`<div class="page-intro"><span class="eyebrow">错题本</span><h1>回到出错的地方。</h1><p>每一道错题都对应一个明确动作：查看解析、专项练习，或向 AI 追问。</p></div><div class="filter-bar"><label class="field">复习状态<select id="mistake-status"><option value="pending">待复习</option><option value="all">全部</option><option value="reviewed">已复习</option></select></label><label class="field grow">搜索题目或知识点<input id="mistake-search" type="search" placeholder="输入关键词" /></label><button class="btn secondary" id="mistake-refresh">刷新</button></div><div id="mistake-data">${loading()}</div>`, '错题本');
  $('#mistake-status').onchange = loadMistakes; $('#mistake-search').oninput = () => { clearTimeout(renderMistakes.timer); renderMistakes.timer = setTimeout(loadMistakes, 220); }; $('#mistake-refresh').onclick = loadMistakes; loadMistakes();
}

async function loadMistakes() {
  const root = $('#mistake-data'); if (!root) return;
  try {
    const data = await api(`/api/students/${encodeURIComponent(state.user.user_id)}/mistakes`); state.mistakes = data.items || [];
    const status = $('#mistake-status').value; const query = $('#mistake-search').value.trim().toLowerCase();
    const items = state.mistakes.filter((item) => (status === 'all' || (status === 'reviewed' ? item.status === 'reviewed' : item.status !== 'reviewed')) && (!query || `${item.prompt} ${item.knowledge_point_id}`.toLowerCase().includes(query)));
    const requestedId = route().split('/')[3]; const detail = requestedId ? items.find((item) => item.mistake_id === decodeURIComponent(requestedId)) || state.mistakes.find((item) => item.mistake_id === decodeURIComponent(requestedId)) : null;
    root.innerHTML = detail ? mistakeDetail(detail) : items.length ? `<div class="data-list">${items.map(mistakeCard).join('')}</div>` : `<div class="panel empty"><div>${icon('mistakes')}</div><b>${status === 'reviewed' ? '还没有已复习错题' : '没有匹配的错题'}</b><p>${status === 'pending' ? '完成练习后，答错的题目会自动保存在这里。' : '调整筛选条件后再试，或开始一组新的练习。'}</p><a class="btn secondary" href="/student/practice/setup">开始练习</a></div>`;
    root.querySelectorAll('[data-review-mistake]').forEach((button) => { button.onclick = () => reviewMistake(button.dataset.reviewMistake); });
  } catch (error) { root.innerHTML = errorPanel(error, 'mistakes'); }
}

function mistakeCard(item) {
  const pending = item.status !== 'reviewed';
  return `<article class="record-card ${pending ? '' : 'reviewed'}"><div class="panel-head"><div><span class="tag ${pending ? 'danger' : ''}">${pending ? '待复习' : '已复习'}</span><h3>${esc(item.prompt || '未命名错题')}</h3></div><span class="meter-score">${dateText(item.created_at)}</span></div><div class="record-meta"><span>${esc(item.knowledge_point_id || '待归类')}</span><span>${item.student_answer ? `我的答案：${esc(item.student_answer)}` : '未作答'}</span></div><div class="record-actions"><a class="btn secondary small" href="/student/mistakes/${encodeURIComponent(item.mistake_id)}">查看详情</a><a class="btn secondary small" href="/student/practice/setup?focus=${encodeURIComponent(item.knowledge_point_id || '')}">专项练习</a>${pending ? `<button class="btn primary small" data-review-mistake="${esc(item.mistake_id)}">标记已复习</button>` : ''}</div></article>`;
}

function mistakeDetail(item) {
  const pending = item.status !== 'reviewed';
  return `<section class="panel"><a class="btn quiet small" href="/student/mistakes">← 返回错题列表</a><div class="panel-head" style="margin-top:16px"><div><span class="eyebrow">${pending ? '待复习' : '已复习'} · ${esc(item.knowledge_point_id || '待归类')}</span><h2>完整错题与解析</h2></div><span class="tag ${pending ? 'danger' : ''}">${pending ? '待处理' : '已复习'}</span></div><article class="record-card ${pending ? '' : 'reviewed'}"><h3>${esc(item.prompt || '未命名错题')}</h3><p><b>我的答案：</b>${esc(item.student_answer || '未作答')}</p><p><b>正确答案：</b>${esc(item.correct_answer || '—')}</p><p><b>解析：</b>${esc(item.explanation || '暂无解析')}</p><p class="field-help">记录时间：${dateText(item.created_at)}</p><div class="record-actions"><a class="btn secondary" href="/student/practice/setup?focus=${encodeURIComponent(item.knowledge_point_id || '')}">专项练习</a><a class="btn secondary" href="/student/chat">问 AI</a>${pending ? `<button class="btn primary" data-review-mistake="${esc(item.mistake_id)}">标记已复习</button>` : ''}</div></article></section>`;
}

async function reviewMistake(id) {
  try { await api(`/api/mistakes/${encodeURIComponent(id)}/review`, { method: 'POST' }); showToast('已标记为复习完成'); loadMistakes(); } catch (error) { showToast(error.message); }
}

function renderGrowth() {
  shell(`<div class="page-intro"><span class="eyebrow">学习成长</span><h1>看见自己真正的变化。</h1><p>掌握度来自对话线索、练习结果和待复习错题；数据不足时会如实说明。</p></div><div id="growth-data">${loading()}</div>`, '学习成长');
  loadGrowth();
}

async function loadGrowth() {
  const root = $('#growth-data');
  try {
    const [profile, memories] = await Promise.all([api(`/api/students/${encodeURIComponent(state.user.user_id)}/learning-profile`), api(`/api/students/${encodeURIComponent(state.user.user_id)}/memories`)]);
    const mastery = profile.mastery || []; const weak = mastery.filter((item) => item.score < 80).slice(0, 3);
    root.innerHTML = `<div class="grid-two"><section class="panel"><div class="panel-head"><div><span class="eyebrow">掌握度概览</span><h2>按当前证据排序</h2><p>最后更新：本次页面加载时</p></div></div><div class="mastery-list">${mastery.map(masteryMarkup).join('') || `<div class="empty"><div>${icon('growth')}</div><b>还没有掌握度数据</b><p>完成练习或和 AI 讨论一个具体知识点后，系统会从真实记录中生成概览。</p></div>`}</div></section><section class="panel"><div class="panel-head"><div><span class="eyebrow">下一步建议</span><h2>最多三件事</h2></div></div><div class="review-list">${weak.map((item) => `<div class="review-item"><span class="item-sign warning">${icon('practice')}</span><div class="item-copy"><b>巩固「${esc(item.knowledge_point)}」</b><small>当前掌握度 ${item.score}% · ${item.mistakes || 0} 道待订正错题</small></div><a class="mini-link" href="/student/practice/setup?focus=${encodeURIComponent(item.knowledge_point)}">开始</a></div>`).join('') || `<div class="empty"><div>${icon('review')}</div><b>目前没有明显薄弱项</b><p>保持练习节奏，新的学习记录会继续更新建议。</p></div>`}</div></section></div><section class="panel" style="margin-top:22px"><div class="panel-head"><div><span class="eyebrow">学习事件</span><h2>最近留下的真实记录</h2></div></div><div class="timeline">${(memories.items || []).slice(0, 10).map((item) => `<article class="timeline-item"><b>${esc(item.knowledge_point_id || '学习线索')}</b><small>${dateText(item.occurred_at)} · 置信度 ${Math.round(Number(item.confidence || 0) * 100)}%</small><span>${esc(item.content)}</span></article>`).join('') || `<div class="empty"><b>还没有学习事件</b><p>完成一次对话或练习后，学习轨迹会显示在这里。</p></div>`}</div></section>`;
  } catch (error) { root.innerHTML = errorPanel(error, 'growth'); }
}

function renderStudentSettings() {
  shell(`<div class="page-intro"><span class="eyebrow">账号与偏好</span><h1>让学习空间更适合你。</h1><p>当前版本可设置本地显示偏好；账号安全与学习数据由系统统一保护。</p></div><section class="panel" style="max-width:720px"><div class="form-grid"><label class="field">当前年级<select id="setting-grade"><option value="grade7">七年级</option><option value="grade8">八年级</option><option value="grade9">九年级</option></select></label><label class="field">界面动效<select id="setting-motion"><option value="normal">正常</option><option value="reduce">减少动画</option></select></label></div><p class="field-help">年级会影响对话检索和练习范围。偏好暂存在当前浏览器，后续可接入账号偏好接口。</p><button class="btn primary" id="save-settings">保存显示偏好</button><hr style="border:0;border-top:1px solid var(--line);margin:28px 0"><h2 style="font-size:18px">账号与隐私</h2><p class="field-help">如需导出学习数据、修改密码或删除账号，请联系系统管理员。</p><button class="btn danger" data-action="logout">退出登录</button></section>`, '设置');
  $('#setting-grade').value = state.grade; $('#setting-motion').value = localStorage.getItem('qisi_motion') || 'normal';
  $('#save-settings').onclick = () => { state.grade = $('#setting-grade').value; localStorage.setItem('qisi_grade', state.grade); localStorage.setItem('qisi_motion', $('#setting-motion').value); document.documentElement.style.setProperty('--motion', $('#setting-motion').value); showToast('显示偏好已保存'); };
}

function teacherTitleFor(path) {
  if (path.includes('/students')) return '学生学情';
  if (path.includes('/knowledge')) return '知识点学情';
  if (path.includes('/review')) return '学习线索审核';
  if (path.includes('/activity')) return '教学活动';
  if (path.includes('/settings')) return '个人设置';
  return '教学概览';
}

function renderTeacherOverview() {
  shell(`<div class="page-intro"><span class="eyebrow">教学概览</span><h1>先关注真正需要帮助的学生。</h1><p>每条提示都带有来源和下一步动作，数据不足时不展示误导性的趋势。</p></div><div id="teacher-data">${loading()}</div>`, '教学概览');
  loadTeacherOverview();
}

function teacherRisk(item) {
  return Number(item.pending_review || 0) > 0 || Number(item.memory_count || 0) >= 4 ? '需关注' : '正常';
}

async function loadTeacherOverview() {
  const root = $('#teacher-data');
  try {
    const data = await api('/api/teacher/overview');
    const students = data.students || []; const points = data.knowledge_points || [];
    const attention = students.filter((item) => teacherRisk(item) === '需关注');
    root.innerHTML = `<div class="stat-grid"><article class="stat red"><span>今天需要关注</span><strong>${attention.length}</strong><span>带有待审核或较多学习线索</span></article><article class="stat"><span>有学习记录的学生</span><strong>${data.student_count || 0}</strong><span>来自现有学习事件</span></article><article class="stat yellow"><span>待审核线索</span><strong>${data.pending_review || 0}</strong><span>需要确认或修正</span></article><article class="stat blue"><span>涉及知识点</span><strong>${points.length}</strong><span>当前已采集范围</span></article></div><div class="teacher-layout" style="margin-top:22px"><section class="panel"><div class="panel-head"><div><span class="eyebrow">风险队列</span><h2>优先查看这些学生</h2><p>依据来自待审核学习线索和近期记录数量。</p></div><a class="btn quiet small" href="/teacher/students">全部学生</a></div><div class="data-list">${attention.slice(0, 5).map((item) => `<div class="review-item"><span class="item-sign danger">${icon('users')}</span><div class="item-copy"><b>${esc(item.student_id)}</b><small>${item.pending_review ? `${item.pending_review} 条线索待审核` : `${item.memory_count} 条学习线索`} · ${esc(item.recent_event || '等待更多学习记录')}</small></div><a class="mini-link" href="/teacher/students/${encodeURIComponent(item.student_id)}">查看依据</a></div>`).join('') || `<div class="empty"><div>${icon('review')}</div><b>当前没有需要特别关注的学生</b><p>当学生完成对话或练习后，这里会根据真实记录显示重点对象。</p></div>`}</div></section><section class="panel"><div class="panel-head"><div><span class="eyebrow">知识点热区</span><h2>班级当前涉及的知识点</h2></div><a class="btn quiet small" href="/teacher/knowledge">查看详情</a></div><div class="chip-grid">${points.slice(0, 8).map((point) => `<a class="knowledge-chip" href="/teacher/knowledge?point=${encodeURIComponent(point)}"><b>${esc(point)}</b><small>查看涉及学生与学习线索</small></a>`).join('') || '<div class="empty"><p>尚未收集到知识点记录。</p></div>'}</div></section></div><section class="panel" style="margin-top:22px"><div class="panel-head"><div><span class="eyebrow">最近学习活动</span><h2>从学生记录进入跟进</h2></div><a class="btn quiet small" href="/teacher/activity">查看活动</a></div><div class="data-list">${students.slice(0, 6).map((item) => `<div class="data-row"><span class="item-sign">${icon('activity')}</span><div class="item-copy"><b>${esc(item.student_id)} · ${teacherRisk(item)}</b><small>${esc(item.recent_event || '暂无最近活动')}</small></div><a class="mini-link" href="/teacher/students/${encodeURIComponent(item.student_id)}">查看学生</a></div>`).join('') || '<div class="empty"><p>还没有学生学习活动。</p></div>'}</div></section>`;
  } catch (error) { root.innerHTML = errorPanel(error, 'teacher-overview'); }
}

function renderTeacherStudents() {
  const parts = route().split('/');
  if (parts.length > 3) return renderTeacherStudentDetail(decodeURIComponent(parts[3]));
  shell(`<div class="page-intro"><span class="eyebrow">学生学情</span><h1>把时间留给需要跟进的人。</h1><p>按风险、学习线索或知识点筛选，打开学生详情后可以看到对应依据。</p></div><div class="filter-bar"><label class="field grow">搜索学生<input id="teacher-student-search" placeholder="学生 ID 或学习线索" /></label><label class="field">筛选<select id="teacher-risk-filter"><option value="all">全部学生</option><option value="attention">需要关注</option></select></label><button class="btn secondary" id="teacher-student-refresh">刷新</button></div><div id="teacher-students-data">${loading()}</div>`, '学生学情');
  $('#teacher-student-search').oninput = () => { clearTimeout(renderTeacherStudents.timer); renderTeacherStudents.timer = setTimeout(loadTeacherStudents, 180); };
  $('#teacher-risk-filter').onchange = loadTeacherStudents; $('#teacher-student-refresh').onclick = loadTeacherStudents; loadTeacherStudents();
}

async function loadTeacherStudents() {
  const root = $('#teacher-students-data'); if (!root) return;
  try {
    const data = await api('/api/teacher/overview'); const query = $('#teacher-student-search').value.trim().toLowerCase(); const onlyAttention = $('#teacher-risk-filter').value === 'attention';
    const students = (data.students || []).filter((item) => (!onlyAttention || teacherRisk(item) === '需关注') && (!query || `${item.student_id} ${item.recent_event} ${(item.knowledge_points || []).join(' ')}`.toLowerCase().includes(query)));
    root.innerHTML = students.length ? `<section class="panel table-wrap"><table class="teacher-table"><thead><tr><th>学生</th><th>最近学习</th><th>薄弱 / 涉及知识点</th><th>待处理</th><th>操作</th></tr></thead><tbody>${students.map((item) => `<tr><td><b>${esc(item.student_id)}</b><br><small class="${teacherRisk(item) === '需关注' ? 'risk-high' : 'risk-normal'}">${teacherRisk(item)}</small></td><td>${esc(item.recent_event || '暂无活动')}</td><td>${esc((item.knowledge_points || []).slice(0, 2).join('、') || '暂未归类')}</td><td>${item.pending_review || 0} 条待审核<br><small>${item.memory_count || 0} 条学习线索</small></td><td><a class="btn secondary small" href="/teacher/students/${encodeURIComponent(item.student_id)}">查看详情</a></td></tr>`).join('')}</tbody></table></section>` : '<section class="panel empty"><div>' + icon('users') + '</div><b>没有匹配的学生</b><p>修改搜索或筛选条件后再试。</p></section>';
  } catch (error) { root.innerHTML = errorPanel(error, 'teacher-students'); }
}

function renderTeacherStudentDetail(studentId) {
  shell(`<div class="page-intro"><a class="btn quiet small" href="/teacher/students">← 返回学生列表</a><span class="eyebrow" style="margin-top:14px">学生详情</span><h1>${esc(studentId)}</h1><p>以下信息来自已记录的学习事件、练习错题与最近对话，不展示无依据的推断。</p></div><div id="teacher-detail-data">${loading()}</div>`, '学生详情');
  loadTeacherStudentDetail(studentId);
}

async function loadTeacherStudentDetail(studentId) {
  const root = $('#teacher-detail-data');
  try {
    const data = await api(`/api/teacher/students/${encodeURIComponent(studentId)}`); const profile = data.profile || {}; const mistakes = data.mistakes || [];
    root.innerHTML = `<div class="grid-two"><section class="panel"><div class="panel-head"><div><span class="eyebrow">掌握度</span><h2>知识点状态</h2></div></div><div class="mastery-list">${(profile.mastery || []).map(masteryMarkup).join('') || '<div class="empty"><p>还没有可展示的掌握度数据。</p></div>'}</div></section><section class="panel"><div class="panel-head"><div><span class="eyebrow">待跟进事项</span><h2>错题与学习线索</h2></div></div><div class="review-list"><div class="review-item"><span class="item-sign danger">${icon('mistakes')}</span><div class="item-copy"><b>${mistakes.filter((item) => item.status !== 'reviewed').length} 道待复习错题</b><small>可在下方查看题目和解析。</small></div></div><div class="review-item"><span class="item-sign">${icon('activity')}</span><div class="item-copy"><b>${profile.memory_count || 0} 条学习线索</b><small>系统只展示已有记录。</small></div></div></div></section></div><section class="panel" style="margin-top:22px"><div class="panel-head"><div><span class="eyebrow">最近学习事件</span><h2>查看具体依据</h2></div></div><div class="timeline">${(profile.recent_events || []).map((item) => `<article class="timeline-item"><b>学习线索</b><span>${esc(item)}</span></article>`).join('') || '<div class="empty"><p>暂无最近学习事件。</p></div>'}</div></section><section class="panel" style="margin-top:22px"><div class="panel-head"><div><span class="eyebrow">错题记录</span><h2>需要订正的题目</h2></div></div><div class="data-list">${mistakes.map((item) => `<article class="record-card ${item.status === 'reviewed' ? 'reviewed' : ''}"><h3>${esc(item.prompt)}</h3><p class="field-help">${esc(item.knowledge_point_id || '待归类')} · ${item.status === 'reviewed' ? '已复习' : '待复习'}</p><p>${esc(item.explanation || '暂无解析')}</p></article>`).join('') || '<div class="empty"><p>暂无错题记录。</p></div>'}</div></section>`;
  } catch (error) { root.innerHTML = errorPanel(error, 'teacher-detail'); }
}

function renderTeacherKnowledge() {
  shell(`<div class="page-intro"><span class="eyebrow">知识点学情</span><h1>先看哪里需要统一讲解。</h1><p>当前按学习线索聚合。数据量不足时仅展示已涉及学生，不虚构趋势或比较。</p></div><div id="teacher-knowledge-data">${loading()}</div>`, '知识点学情');
  loadTeacherKnowledge();
}

async function loadTeacherKnowledge() {
  const root = $('#teacher-knowledge-data');
  try {
    const data = await api('/api/teacher/overview'); const selected = new URLSearchParams(location.search).get('point') || '';
    const groups = (data.knowledge_points || []).map((point) => ({ point, students: (data.students || []).filter((student) => (student.knowledge_points || []).includes(point)) }));
    root.innerHTML = `<section class="panel"><div class="panel-head"><div><span class="eyebrow">知识点分布</span><h2>${selected ? `「${esc(selected)}」的学生记录` : '当前知识点'}</h2></div></div><div class="chip-grid">${groups.filter((group) => !selected || group.point === selected).map((group) => `<article class="knowledge-chip"><b>${esc(group.point)}</b><small>${group.students.length} 位学生留下学习线索</small><div class="record-actions">${group.students.map((student) => `<a class="tag" href="/teacher/students/${encodeURIComponent(student.student_id)}">${esc(student.student_id)}</a>`).join('') || '<span class="field-help">暂无关联学生</span>'}</div></article>`).join('') || '<div class="empty"><div>' + icon('growth') + '</div><b>没有匹配的知识点记录</b><p>等待学生完成对话或练习后，会在这里形成聚合。</p></div>'}</div></section>`;
  } catch (error) { root.innerHTML = errorPanel(error, 'teacher-knowledge'); }
}

function renderTeacherReview() {
  shell(`<div class="page-intro"><span class="eyebrow">AI 学习线索审核</span><h1>确认、修正或删除学习线索。</h1><p>每条记录显示学生、知识点、置信度和来源摘要。处理后会立即给出结果反馈。</p></div><div id="teacher-review-data">${loading()}</div>`, '学习线索审核');
  loadTeacherReview();
}

async function loadTeacherReview() {
  const root = $('#teacher-review-data');
  try {
    const overview = await api('/api/teacher/overview'); const candidates = (overview.students || []).filter((item) => item.pending_memory_id);
    root.innerHTML = candidates.length ? `<section class="panel"><div class="panel-head"><div><span class="eyebrow">待审核队列</span><h2>${candidates.length} 条待处理线索</h2></div></div><div class="data-list">${candidates.map((item) => `<article class="record-card"><div class="panel-head"><div><span class="tag warning">等待审核</span><h3>${esc(item.student_id)} · ${esc((item.knowledge_points || []).join('、') || '待归类')}</h3></div><span class="meter-score">来源：学习对话</span></div><p>${esc(item.recent_event || '暂无内容摘要')}</p><p class="field-help">系统未返回逐条置信度；打开学生详情可查看相关学习记录。</p><div class="record-actions"><button class="btn primary small" data-memory-review="${esc(item.pending_memory_id)}" data-memory-action="approve">确认</button><button class="btn secondary small" data-memory-review="${esc(item.pending_memory_id)}" data-memory-action="delete">删除</button><a class="btn secondary small" href="/teacher/students/${encodeURIComponent(item.student_id)}">查看学生</a></div></article>`).join('')}</div></section>` : '<section class="panel empty"><div>' + icon('review') + '</div><b>当前没有待审核学习线索</b><p>新的 AI 学习线索出现后，会自动进入这个工作台。</p></section>';
    root.querySelectorAll('[data-memory-review]').forEach((button) => { button.onclick = () => reviewMemory(button.dataset.memoryReview, button.dataset.memoryAction); });
  } catch (error) { root.innerHTML = errorPanel(error, 'teacher-review'); }
}

async function reviewMemory(id, action) {
  try { await api(`/api/memories/${encodeURIComponent(id)}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action }) }); showToast(action === 'delete' ? '学习线索已删除' : '学习线索已确认'); loadTeacherReview(); } catch (error) { showToast(error.message); }
}

function renderTeacherActivity() {
  shell(`<div class="page-intro"><span class="eyebrow">教学活动</span><h1>从活动记录开始跟进。</h1><p>当前将学生最近学习线索汇总在一起；提问与错题的完整跨班级筛选需要后端活动接口支持。</p></div><div id="teacher-activity-data">${loading()}</div>`, '教学活动');
  loadTeacherActivity();
}

async function loadTeacherActivity() {
  const root = $('#teacher-activity-data');
  try {
    const data = await api('/api/teacher/overview');
    root.innerHTML = `<div class="admin-warning">当前接口只返回学生聚合学习线索，因此此页展示真实的最近事件；按时间、类型和知识点的跨班级筛选将在活动查询接口接入后自动扩展。</div><section class="panel"><div class="data-list">${(data.students || []).map((item) => `<div class="data-row"><span class="item-sign">${icon('activity')}</span><div class="item-copy"><b>${esc(item.student_id)}</b><small>${esc(item.recent_event || '暂无最近活动')}</small></div><span class="tag">${esc((item.knowledge_points || [])[0] || '待归类')}</span><a class="mini-link" href="/teacher/students/${encodeURIComponent(item.student_id)}">详情</a></div>`).join('') || '<div class="empty"><p>暂无教学活动。</p></div>'}</div></section>`;
  } catch (error) { root.innerHTML = errorPanel(error, 'teacher-activity'); }
}

function renderTeacherSettings() {
  shell(`<div class="page-intro"><span class="eyebrow">个人设置</span><h1>教师工作台偏好。</h1><p>通知、班级和备注等持久化设置需要配合后端接口；当前可安全退出登录。</p></div><section class="panel" style="max-width:680px"><h2 style="font-size:18px">账号</h2><p class="field-help">当前账号：${esc(state.user.display_name || state.user.username)} · 教师</p><button class="btn danger" data-action="logout">退出登录</button></section>`, '个人设置');
}

function renderAdminOverview() {
  shell(`<div class="page-intro"><span class="eyebrow">系统概览</span><h1>先处理需要行动的事项。</h1><p>账号、内容与学习活动的状态都有明确的数据来源，不将统计数字替代处理动作。</p></div><div id="admin-overview-data">${loading()}</div>`, '系统概览');
  loadAdminOverview();
}

async function loadAdminOverview() {
  const root = $('#admin-overview-data');
  try {
    const data = await api('/api/admin/overview'); const needAttention = Number(data.pending_mistakes || 0) + Number(data.disabled_users || 0);
    root.innerHTML = `<div class="admin-warning">数据更新时间：本次页面加载时。内容导入的实时进度请在“教材与题库”页面查看。</div><section class="task-card"><div><span class="eyebrow">需要处理的事项</span><h2>${needAttention ? `${needAttention} 项需要关注` : '当前没有待处理事项'}</h2><p>${data.pending_mistakes ? `${data.pending_mistakes} 条待复习错题仍在队列中。` : '错题复习队列当前为空。'} ${data.disabled_users ? `${data.disabled_users} 个账号已停用，可在用户与权限中查看。` : ''}</p><a class="btn primary" href="${data.pending_mistakes ? '/admin/activity/mistakes' : '/admin/users'}">查看并处理 ${icon('arrow')}</a></div><div class="task-meta"><span>知识服务状态</span><br />${esc(data.embedding_model || '在线')}</div></section><div class="stat-grid" style="margin-top:22px"><article class="stat"><span>启用账号</span><strong>${data.active_users || 0}</strong><span>共 ${data.user_count || 0} 个账号</span></article><article class="stat red"><span>待复习错题</span><strong>${data.pending_mistakes || 0}</strong><span>共 ${data.mistake_count || 0} 条错题</span></article><article class="stat blue"><span>提问记录</span><strong>${data.question_count || 0}</strong><span>用于观察真实需求</span></article><article class="stat yellow"><span>知识片段</span><strong>${data.knowledge_chunks || 0}</strong><span>当前知识服务内容</span></article></div><div class="grid-two" style="margin-top:22px"><section class="panel"><div class="panel-head"><div><span class="eyebrow">服务状态</span><h2>系统基础信息</h2></div></div><div class="review-list"><div class="review-item"><span class="item-sign">${icon('users')}</span><div class="item-copy"><b>账号目录</b><small>${data.user_count || 0} 个真实账号 · ${data.disabled_users || 0} 个停用账号</small></div><a class="mini-link" href="/admin/users">管理</a></div><div class="review-item"><span class="item-sign warning">${icon('book')}</span><div class="item-copy"><b>知识内容</b><small>模型：${esc(data.embedding_model || '未返回')} · ${data.knowledge_chunks || 0} 个片段</small></div><a class="mini-link" href="/admin/content">查看</a></div></div></section><section class="panel"><div class="panel-head"><div><span class="eyebrow">学习活动</span><h2>从记录进入排查</h2></div></div><div class="review-list"><div class="review-item"><span class="item-sign danger">${icon('mistakes')}</span><div class="item-copy"><b>${data.mistake_count || 0} 条错题记录</b><small>${data.pending_mistakes || 0} 条仍待复习</small></div><a class="mini-link" href="/admin/activity/mistakes">查看</a></div><div class="review-item"><span class="item-sign">${icon('chat')}</span><div class="item-copy"><b>${data.question_count || 0} 条提问记录</b><small>可按学生查看具体提问</small></div><a class="mini-link" href="/admin/activity/questions">查看</a></div></div></section></div>`;
  } catch (error) { root.innerHTML = errorPanel(error, 'admin-overview'); }
}

function renderAdminUsers() {
  shell(`<div class="page-intro"><span class="eyebrow">用户与权限</span><h1>管理账号、角色与访问状态。</h1><p>停用账号会要求二次确认；当前管理员无法停用或降级自己。</p></div><div class="filter-bar"><label class="field grow">搜索用户<input id="admin-user-search" placeholder="用户名或显示名称" /></label><label class="field">角色<select id="admin-user-role"><option value="all">全部角色</option><option value="student">学生</option><option value="teacher">教师</option><option value="admin">管理员</option></select></label><label class="field">状态<select id="admin-user-status"><option value="all">全部状态</option><option value="active">启用</option><option value="disabled">停用</option></select></label><button class="btn secondary" id="admin-user-refresh">搜索</button></div><div id="admin-users-data">${loading()}</div>`, '用户与权限');
  $('#admin-user-search').onkeydown = (event) => { if (event.key === 'Enter') loadAdminUsers(); };
  $('#admin-user-role').onchange = loadAdminUsers; $('#admin-user-status').onchange = loadAdminUsers; $('#admin-user-refresh').onclick = loadAdminUsers; loadAdminUsers();
}

async function loadAdminUsers() {
  const root = $('#admin-users-data'); if (!root) return;
  try {
    const query = new URLSearchParams({ search: $('#admin-user-search').value.trim(), limit: '100' }); const data = await api(`/api/admin/users?${query}`);
    const role = $('#admin-user-role').value; const status = $('#admin-user-status').value; const users = (data.items || []).filter((item) => (role === 'all' || item.role === role) && (status === 'all' || item.status === status));
    root.innerHTML = users.length ? `<section class="panel table-wrap"><table class="data-table"><thead><tr><th>用户</th><th>角色</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead><tbody>${users.map((item) => `<tr><td><b>${esc(item.display_name || item.username)}</b><br><small>${esc(item.username)}</small></td><td><span class="tag">${roleName[item.role] || esc(item.role)}</span></td><td><span class="tag ${item.status === 'disabled' ? 'danger' : ''}">${item.status === 'disabled' ? '停用' : '启用'}</span></td><td>${dateText(item.created_at)}</td><td><button class="btn ${item.status === 'disabled' ? 'secondary' : 'danger'} small" data-user-toggle="${esc(item.user_id)}" data-next-status="${item.status === 'disabled' ? 'active' : 'disabled'}">${item.status === 'disabled' ? '启用账号' : '停用账号'}</button></td></tr>`).join('')}</tbody></table></section>` : '<section class="panel empty"><div>' + icon('users') + '</div><b>没有匹配的用户</b><p>调整搜索和筛选条件后再试。</p></section>';
    root.querySelectorAll('[data-user-toggle]').forEach((button) => { button.onclick = () => { const enable = button.dataset.nextStatus === 'active'; setModal(enable ? '启用此账号？' : '停用此账号？', enable ? '账号将恢复登录权限。' : '该用户将无法继续登录，已有学习数据会被保留。', () => updateUserStatus(button.dataset.userToggle, button.dataset.nextStatus), enable ? '启用' : '停用'); }; });
  } catch (error) { root.innerHTML = errorPanel(error, 'admin-users'); }
}

async function updateUserStatus(id, status) {
  try { await api(`/api/admin/users/${encodeURIComponent(id)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) }); showToast(status === 'active' ? '账号已启用' : '账号已停用'); loadAdminUsers(); } catch (error) { showToast(error.message); }
}

function renderAdminContent() {
  shell(`<div class="page-intro"><span class="eyebrow">教材与题库</span><h1>上传、处理并核对知识内容。</h1><p>上传前检查文件格式和大小；上传后显示真实任务阶段、进度与失败原因。</p></div><section class="panel"><div class="panel-head"><div><span class="eyebrow">内容导入</span><h2>上传教材或结构化题库</h2></div></div><form id="document-upload" class="upload-box"><label class="field">选择文件<input type="file" id="document-file" accept=".md,.markdown,.txt,.json,.jsonl,.csv" required /></label><div class="form-grid"><label class="field">适用年级<select id="document-grade"><option value="grade7">七年级</option><option value="grade8">八年级</option><option value="grade9">九年级</option></select></label><div class="field"><span>文件要求</span><span class="field-help">支持 Markdown、TXT、JSON、JSONL、CSV；单个文件不超过 20 MB。</span></div></div><p class="form-error" id="document-error"></p><button class="btn primary" type="submit">上传并开始处理 ${icon('arrow')}</button></form><div id="document-job"></div></section><section class="panel" style="margin-top:22px"><div class="panel-head"><div><span class="eyebrow">文档列表</span><h2>已上传内容</h2></div><button class="btn secondary small" id="document-refresh">刷新</button></div><div id="document-list">${loading()}</div></section>`, '教材与题库');
  $('#document-upload').onsubmit = uploadDocument; $('#document-refresh').onclick = loadDocuments; loadDocuments();
}

function documentStatus(status) {
  return ({ queued: '排队中', processing: '处理中', completed: '已完成', failed: '失败', cancelled: '已取消' })[status] || status || '未知';
}

async function loadDocuments() {
  const root = $('#document-list'); if (!root) return;
  try { const data = await api('/api/documents?limit=50'); root.innerHTML = (data.items || []).length ? `<div class="data-list">${data.items.map((item) => `<div class="data-row"><span class="item-sign ${item.status === 'failed' ? 'danger' : ''}">${icon('book')}</span><div class="item-copy"><b>${esc(item.document_name)}</b><small>${esc(item.grade_name || gradeName[item.grade_id] || item.grade_id)} · 更新于 ${dateText(item.updated_at)}</small></div><span class="tag ${item.status === 'failed' ? 'danger' : item.status === 'processing' || item.status === 'queued' ? 'warning' : ''}">${esc(documentStatus(item.status))}</span></div>`).join('')}</div>` : '<div class="empty"><div>' + icon('book') + '</div><b>还没有上传内容</b><p>选择教材或题库文件，完成年级标注后开始导入。</p></div>'; } catch (error) { root.innerHTML = errorPanel(error, 'admin-content'); }
}

async function uploadDocument(event) {
  event.preventDefault(); const file = $('#document-file').files[0]; const error = $('#document-error'); if (!file) return;
  if (file.size > 20 * 1024 * 1024) { error.textContent = '文件不能超过 20 MB。'; return; }
  const allowed = /\.(md|markdown|txt|json|jsonl|csv)$/i; if (!allowed.test(file.name)) { error.textContent = '请选择 Markdown、TXT、JSON、JSONL 或 CSV 文件。'; return; }
  const button = $('button[type="submit"]', event.currentTarget); button.disabled = true; error.textContent = '';
  try { const data = await api(`/api/documents/upload?grade_id=${encodeURIComponent($('#document-grade').value)}`, { method: 'POST', headers: { 'Content-Type': file.type || 'application/octet-stream', 'X-Filename': file.name }, body: await file.arrayBuffer() }); $('#document-job').innerHTML = `<div class="job" id="current-job"><span><b>导入任务已创建</b><br><small>任务 ID：${esc(data.job_id)}</small></span><span class="tag warning">${esc(documentStatus(data.status))}</span></div>`; pollDocumentJob(data.job_id); $('#document-file').value = ''; loadDocuments(); } catch (err) { error.textContent = err.message; } finally { button.disabled = false; }
}

async function pollDocumentJob(jobId) {
  const root = $('#current-job'); if (!root) return;
  try { const job = await api(`/api/documents/jobs/${encodeURIComponent(jobId)}`); const progress = Math.max(0, Math.min(100, Number(job.progress || 0))); root.innerHTML = `<span><b>${esc(job.file_name || '导入任务')}</b><br><small>阶段：${esc(job.stage || '等待处理')}${job.error_message ? ` · ${esc(job.error_message)}` : ''}</small></span><span><progress value="${progress}" max="100">${progress}%</progress> <span class="tag ${job.status === 'failed' ? 'danger' : job.status === 'completed' ? '' : 'warning'}">${esc(documentStatus(job.status))} ${progress}%</span></span>`; if (!['completed', 'failed', 'cancelled'].includes(job.status)) setTimeout(() => pollDocumentJob(jobId), 2500); else loadDocuments(); } catch (error) { root.innerHTML = `<p class="form-error">无法读取导入任务：${esc(error.message)}</p>`; }
}

function renderAdminActivity(kind) {
  const mistakes = kind === 'mistakes'; const label = mistakes ? '错题记录' : '提问记录';
  shell(`<div class="page-intro"><span class="eyebrow">学习活动</span><h1>${label}</h1><p>${mistakes ? '按学生、知识点和状态查看真实错题记录。' : '按学生查看真实提问记录，了解学习需求。'}</p></div><div id="admin-activity-options">${loading()}</div><div id="admin-activity-data"></div>`, label);
  loadAdminActivityOptions(kind);
}

async function loadAdminActivityOptions(kind) {
  const optionsRoot = $('#admin-activity-options');
  try {
    const data = await api('/api/admin/activity-options'); const studentOptions = (data.students || []).map((item) => `<option value="${esc(item.user_id)}">${esc(item.display_name || item.username)} · ${esc(item.username)}</option>`).join('');
    optionsRoot.innerHTML = `<div class="filter-bar"><label class="field">学生<select id="activity-student"><option value="">请选择学生</option>${studentOptions}</select></label>${kind === 'mistakes' ? `<label class="field">知识点<select id="activity-point"><option value="">全部知识点</option>${(data.knowledge_points || []).map((point) => `<option value="${esc(point)}">${esc(point)}</option>`).join('')}</select></label><label class="field">状态<select id="activity-status"><option value="all">全部状态</option><option value="unreviewed">待复习</option><option value="reviewed">已复习</option></select></label>` : ''}<button class="btn primary" id="activity-apply">查看记录</button></div>`;
    $('#activity-apply').onclick = () => loadAdminActivity(kind);
  } catch (error) { optionsRoot.innerHTML = errorPanel(error, 'admin-activity'); }
}

async function loadAdminActivity(kind) {
  const root = $('#admin-activity-data'); const student = $('#activity-student')?.value; if (!student) { root.innerHTML = '<section class="panel empty"><div>' + icon('users') + '</div><b>请先选择学生</b><p>选择学生后即可查看对应的学习活动记录。</p></section>'; return; }
  try {
    const params = new URLSearchParams({ user_id: student, limit: '50' }); if (kind === 'mistakes') { params.set('status', $('#activity-status').value); if ($('#activity-point').value) params.set('knowledge_point_id', $('#activity-point').value); }
    const endpoint = kind === 'mistakes' ? '/api/admin/mistakes' : '/api/admin/questions'; const data = await api(`${endpoint}?${params}`); const items = data.items || [];
    root.innerHTML = items.length ? `<section class="panel"><div class="data-list">${items.map((item) => kind === 'mistakes' ? `<article class="record-card ${item.status === 'reviewed' ? 'reviewed' : ''}"><span class="tag ${item.status === 'reviewed' ? '' : 'danger'}">${item.status === 'reviewed' ? '已复习' : '待复习'}</span><h3>${esc(item.prompt)}</h3><p><b>学生答案：</b>${esc(item.student_answer || '未作答')}　<b>正确答案：</b>${esc(item.correct_answer || '—')}</p><p>${esc(item.explanation || '暂无解析')}</p><p class="field-help">${esc(item.knowledge_point_id || '待归类')} · ${dateText(item.created_at)}</p></article>` : `<article class="record-card"><span class="tag">提问</span><h3>${esc(item.question)}</h3><p class="field-help">会话：${esc(item.session_title || '新学习对话')} · ${dateText(item.created_at)}</p></article>`).join('')}</div></section>` : '<section class="panel empty"><div>' + icon('activity') + '</div><b>当前筛选条件下没有记录</b><p>调整学生或筛选条件后再试。</p></section>';
  } catch (error) { root.innerHTML = errorPanel(error, 'admin-activity'); }
}

function renderAdminQuality() {
  shell(`<div class="page-intro"><span class="eyebrow">检索质量</span><h1>查看回答前找到的教材依据。</h1><p>输入一个真实问题，检查检索候选、章节和相关度；低评分与失败任务将在质量接口接入后显示。</p></div><section class="panel"><form id="retrieval-form" class="form-grid"><label class="field">检索问题<input id="retrieval-query" required placeholder="例如：一元一次方程怎么解？" /></label><label class="field">年级<select id="retrieval-grade"><option value="">全部年级</option><option value="grade7">七年级</option><option value="grade8">八年级</option><option value="grade9">九年级</option></select></label><div><button class="btn primary" type="submit">查看检索依据 ${icon('search')}</button></div></form></section><div id="retrieval-data" style="margin-top:22px"></div>`, '检索质量');
  $('#retrieval-form').onsubmit = loadRetrieval;
}

async function loadRetrieval(event) {
  event.preventDefault(); const root = $('#retrieval-data'); root.innerHTML = loading();
  try { const params = new URLSearchParams({ query: $('#retrieval-query').value.trim(), top_k: '8' }); if ($('#retrieval-grade').value) params.set('grade_id', $('#retrieval-grade').value); const data = await api(`/api/retrieval/debug?${params}`); const hits = data.hits || [];
    root.innerHTML = hits.length ? `<section class="panel"><div class="panel-head"><div><span class="eyebrow">检索候选</span><h2>共 ${hits.length} 条依据</h2></div></div><div class="data-list">${hits.map((item, index) => { const chunk = item.chunk || {}; const metadata = chunk.metadata || {}; const grade = metadata.grade || metadata.grade_id || item.grade_id || '未标记'; return `<article class="record-card"><span class="tag">候选 ${index + 1}</span><h3>${esc(chunk.title || chunk.chapter_id || '教材片段')}</h3><p>${esc(chunk.text || item.excerpt || item.content || '未提供内容')}</p><div class="record-meta"><span>章节：${esc(chunk.chapter_id || item.chapter || '未标记')}</span><span>相关度：${Number(item.score || 0).toFixed(3)}</span><span>年级：${esc(grade)}</span></div></article>`; }).join('')}</div></section>` : '<section class="panel empty"><div>' + icon('search') + '</div><b>没有找到匹配的教材依据</b><p>检查问题、年级筛选，或确认相关内容已完成导入。</p></section>'; } catch (error) { root.innerHTML = errorPanel(error, 'admin-quality'); }
}

function logout() {
  if (state.chatAbort) state.chatAbort.abort();
  localStorage.removeItem('qisi_access_token'); state.token = ''; state.user = null; state.sessions = []; state.quiz = null;
  state.authMode = 'login'; state.authRole = route().startsWith('/admin') ? 'admin' : 'student';
  navigate('/', true); showToast('已退出登录');
}

async function render() {
  const path = route();
  if (!state.token) {
    const pathRole = roleForPath(path);
    if (pathRole) state.authRole = pathRole;
    return renderAuth();
  }
  if (!state.user) {
    try { state.user = await api('/api/auth/me'); } catch { return; }
  }
  const prefix = state.user.role === 'admin' ? '/admin' : `/${state.user.role}`;
  if (!path.startsWith(prefix)) return navigate(firstPathFor(state.user.role), true);
  if (state.user.role === 'student') {
    if (path === '/student' || path === '/') return navigate('/student/home', true);
    if (path === '/student/home') return renderHome();
    if (path.startsWith('/student/chat')) return renderChat();
    if (path.startsWith('/student/practice')) return renderPractice();
    if (path.startsWith('/student/mistakes')) return renderMistakes();
    if (path.startsWith('/student/growth')) return renderGrowth();
    if (path.startsWith('/student/settings')) return renderStudentSettings();
    return navigate('/student/home', true);
  }
  if (state.user.role === 'teacher') {
    if (path === '/teacher' || path === '/') return navigate('/teacher/overview', true);
    if (path.startsWith('/teacher/students')) return renderTeacherStudents();
    if (path.startsWith('/teacher/knowledge')) return renderTeacherKnowledge();
    if (path.startsWith('/teacher/review')) return renderTeacherReview();
    if (path.startsWith('/teacher/activity')) return renderTeacherActivity();
    if (path.startsWith('/teacher/settings')) return renderTeacherSettings();
    return renderTeacherOverview();
  }
  if (path === '/admin' || path === '/') return navigate('/admin/overview', true);
  if (path.startsWith('/admin/users')) return renderAdminUsers();
  if (path.startsWith('/admin/content')) return renderAdminContent();
  if (path.startsWith('/admin/activity/mistakes')) return renderAdminActivity('mistakes');
  if (path.startsWith('/admin/activity/questions')) return renderAdminActivity('questions');
  if (path.startsWith('/admin/quality')) return renderAdminQuality();
  return renderAdminOverview();
}

window.addEventListener('popstate', render);
document.addEventListener('click', (event) => {
  const logoutButton = event.target.closest('[data-action="logout"]');
  if (logoutButton) { event.preventDefault(); logout(); return; }
  const retry = event.target.closest('[data-retry]');
  if (retry) { event.preventDefault(); render(); return; }
  const link = event.target.closest('a[href]');
  if (!link || event.defaultPrevented || link.target || link.hasAttribute('download')) return;
  const url = new URL(link.href, location.origin);
  if (url.origin !== location.origin || !url.pathname.startsWith('/')) return;
  event.preventDefault(); navigate(`${url.pathname}${url.search}`);
});

render();
