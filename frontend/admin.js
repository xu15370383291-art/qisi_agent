const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const token = localStorage.getItem('qisi_access_token');
const headers = () => token ? { Authorization: `Bearer ${token}` } : {};
const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' }[char]));
const formatDate = (value) => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value).slice(0, 19).replace('T', ' ') : date.toLocaleString('zh-CN', { hour12: false });
};

const state = {
  view: 'users',
  users: [],
  userPage: 0,
  userPageSize: 20,
  mistakes: { page: 0, pageSize: 10, total: 0, filters: { status: 'all', user_id: '', knowledge_point_id: '' } },
  questions: { page: 0, pageSize: 10, total: 0, filters: { user_id: '' } },
  documents: [],
};

async function request(url, options = {}) {
  const response = await fetch(url, { ...options, headers: { ...headers(), ...(options.headers || {}) } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || '请求失败');
  return data;
}

function setError(selector, message = '') { const node = $(selector); if (node) node.textContent = message; }

function showView(view) {
  state.view = view;
  $$('.admin-view').forEach((panel) => panel.classList.toggle('active-admin-view', panel.dataset.adminPanel === view));
  $$('.admin-nav-item').forEach((item) => item.classList.toggle('active', item.dataset.adminView === view));
  const labels = {
    dashboard: ['仪表盘', '掌握账号、内容和学习活动的整体状态。'],
    users: ['用户管理', '管理账号、角色和访问状态。'],
    mistakes: ['错题记录', '查看学生练习中的错误、答案和待复习知识点。'],
    questions: ['提问记录', '查看学生在学习对话中提出的问题。'],
    content: ['教材与题库', '管理教材、题库和知识内容导入任务。'],
  };
  const [title, subtitle] = labels[view] || labels.users;
  $('#admin-view-title').textContent = title;
  $('#admin-view-subtitle').textContent = subtitle;
  if (view === 'mistakes') loadMistakes();
  if (view === 'questions') loadQuestions();
  if (view === 'content') loadDocuments();
}

function renderAdminDashboard(overview) {
  const students = Number(overview.student_count || 0);
  const teachers = Number(overview.teacher_count || 0);
  const total = Math.max(1, Number(overview.user_count || 0));
  const width = (value) => `${Math.round((Number(value || 0) / total) * 100)}%`;
  $('#dash-stat-users').textContent = overview.user_count ?? 0;
  $('#dash-stat-users-trend').textContent = `${overview.active_users ?? 0} 个账号正在启用`;
  $('#dash-stat-ratio').textContent = `${students} / ${teachers}`;
  $('#dash-stat-mistakes').textContent = overview.pending_mistakes ?? 0;
  $('#dash-stat-mistakes-trend').textContent = overview.mistake_count ? `共 ${overview.mistake_count} 条错题记录` : '当前队列已清空';
  $('#dash-stat-chunks').textContent = overview.knowledge_chunks ?? 0;
  $('#dash-stat-model').textContent = overview.embedding_model ? `模型 · ${overview.embedding_model}` : '知识服务在线';
  $('#admin-role-bars').innerHTML = `<div class="admin-role-row"><div><span>学生</span><b>${students}</b></div><div class="admin-role-bar"><i class="student" style="width:${width(students)}"></i></div><small>${Math.round((students / total) * 100)}%</small></div><div class="admin-role-row"><div><span>教师</span><b>${teachers}</b></div><div class="admin-role-bar"><i class="teacher" style="width:${width(teachers)}"></i></div><small>${Math.round((teachers / total) * 100)}%</small></div><div class="admin-role-row"><div><span>管理员</span><b>${overview.admin_count ?? 0}</b></div><div class="admin-role-bar"><i class="admin" style="width:${width(overview.admin_count)}"></i></div><small>${Math.round((Number(overview.admin_count || 0) / total) * 100)}%</small></div>`;
  $('#admin-dashboard-signals').innerHTML = `<div class="admin-signal"><span class="signal-icon teal">✓</span><div><b>账号目录已同步</b><small>${overview.user_count ?? 0} 个真实用户 · ${overview.disabled_users ?? 0} 个停用账号</small></div><em>正常</em></div><div class="admin-signal"><span class="signal-icon coral">⌁</span><div><b>错题复习队列</b><small>${overview.pending_mistakes ?? 0} 条待复习 · ${overview.mistake_count ?? 0} 条累计记录</small></div><em class="${overview.pending_mistakes ? 'attention' : ''}">${overview.pending_mistakes ? '需关注' : '清爽'}</em></div><div class="admin-signal"><span class="signal-icon yellow">？</span><div><b>学生提问活跃度</b><small>${overview.question_count ?? 0} 条提问记录等待查看</small></div><em>已记录</em></div>`;
}

async function loadOverview() {
  const overview = await request('/api/admin/overview');
  $('#stat-users').textContent = overview.user_count ?? '0';
  $('#stat-students').textContent = overview.student_count ?? '0';
  $('#stat-teachers').textContent = overview.teacher_count ?? '0';
  $('#stat-disabled').textContent = overview.disabled_users ?? '0';
  $('#mistake-total-count').textContent = overview.mistake_count ?? '0';
  $('#mistake-pending-count').textContent = overview.pending_mistakes ?? '0';
  $('#question-total-count').textContent = overview.question_count ?? '0';
  renderAdminDashboard(overview);
}

function renderUsers() {
  const role = $('#admin-role-filter').value;
  const status = $('#admin-status-filter').value;
  const filtered = state.users.filter((item) => (role === 'all' || item.role === role) && (status === 'all' || item.status === status));
  const pages = Math.max(1, Math.ceil(filtered.length / state.userPageSize));
  state.userPage = Math.min(state.userPage, pages - 1);
  const items = filtered.slice(state.userPage * state.userPageSize, (state.userPage + 1) * state.userPageSize);
  $('#admin-users').innerHTML = items.length ? `<table><thead><tr><th>用户</th><th>显示名称</th><th>角色</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead><tbody>${items.map((item) => `<tr data-user-id="${esc(item.user_id)}"><td><strong>${esc(item.username)}</strong></td><td>${esc(item.display_name)}</td><td><span class="role-pill">${item.role === 'admin' ? '管理员' : item.role === 'teacher' ? '教师' : '学生'}</span></td><td><span class="status-pill ${item.status === 'disabled' ? 'disabled' : ''}">${item.status === 'active' ? '启用' : '停用'}</span></td><td>${formatDate(item.created_at)}</td><td><div class="admin-row-actions"><button data-action="toggle">${item.status === 'active' ? '停用' : '启用'}</button></div></td></tr>`).join('')}</tbody></table>` : '<div class="admin-empty"><span>⌁</span><b>没有匹配的用户</b><small>调整筛选条件后再试。</small></div>';
  $('#admin-pagination').hidden = filtered.length <= state.userPageSize;
  $('#admin-page-label').textContent = `第 ${state.userPage + 1} / ${pages} 页 · ${filtered.length} 人`;
  $('#admin-prev-page').disabled = state.userPage === 0;
  $('#admin-next-page').disabled = state.userPage >= pages - 1;
}

async function loadUsers() {
  try {
    const search = encodeURIComponent($('#admin-search-input').value.trim());
    const data = await request(`/api/admin/users?search=${search}&limit=100`);
    state.users = data.items || [];
    state.userPage = 0;
    renderUsers();
    setError('#admin-error');
  } catch (error) { setError('#admin-error', error.message); }
}

function renderMistakes(items) {
  $('#admin-mistakes').innerHTML = items.length ? items.map((item) => `<article class="admin-activity-card mistake-activity-card"><div class="admin-activity-card-head"><div><span class="admin-user-label">${esc(item.display_name || item.username)} · ${esc(item.username)}</span><h3>${esc(item.prompt)}</h3></div><span class="activity-status ${item.status === 'reviewed' ? 'done' : 'pending'}">${item.status === 'reviewed' ? '已复习' : '待复习'}</span></div><div class="admin-answer-grid"><div><small>学生答案</small><p>${esc(item.student_answer || '未作答')}</p></div><div><small>正确答案</small><p>${esc(item.correct_answer || '—')}</p></div></div><p class="admin-activity-explanation">${esc(item.explanation || '暂无解析')}</p><div class="admin-activity-footer"><span>${esc(item.knowledge_point_id || '未归类')}</span><span>${formatDate(item.created_at)}</span></div></article>`).join('') : '<div class="admin-empty"><span>⌁</span><b>没有错题记录</b><small>当前筛选条件下暂无数据。</small></div>';
}

async function loadMistakes() {
  const cfg = state.mistakes;
  if (!cfg.filters.user_id && !cfg.filters.knowledge_point_id) {
    $('#admin-mistakes').innerHTML = '<div class="admin-empty admin-filter-empty"><span>⌁</span><b>请选择学生或知识点</b><small>选择后再加载错题记录。</small></div>';
    $('#mistake-pagination').hidden = true;
    return;
  }
  const query = new URLSearchParams({ ...cfg.filters, limit: String(cfg.pageSize), offset: String(cfg.page * cfg.pageSize) });
  try {
    const data = await request(`/api/admin/mistakes?${query}`);
    cfg.total = data.total || 0;
    renderMistakes(data.items || []);
    updateActivityPagination('mistake', cfg.total, cfg.page, cfg.pageSize);
    setError('#mistake-error');
  } catch (error) { setError('#mistake-error', error.message); }
}

function renderQuestions(items) {
  $('#admin-questions').innerHTML = items.length ? items.map((item) => `<article class="admin-activity-card question-activity-card"><div class="admin-activity-card-head"><div><span class="admin-user-label">${esc(item.display_name || item.username)} · ${esc(item.username)}</span><h3>${esc(item.question)}</h3></div><span class="question-mark">？</span></div><div class="admin-question-context"><span>会话：${esc(item.session_title || '新学习对话')}</span><span>${formatDate(item.created_at)}</span></div></article>`).join('') : '<div class="admin-empty"><span>？</span><b>没有提问记录</b><small>当前筛选条件下暂无数据。</small></div>';
}

async function loadQuestions() {
  const cfg = state.questions;
  if (!cfg.filters.user_id) {
    $('#admin-questions').innerHTML = '<div class="admin-empty admin-filter-empty"><span>？</span><b>请选择学生</b><small>选择后再加载提问记录。</small></div>';
    $('#question-pagination').hidden = true;
    return;
  }
  const query = new URLSearchParams(cfg.filters);
  query.set('limit', String(cfg.pageSize));
  query.set('offset', String(cfg.page * cfg.pageSize));
  try {
    const data = await request(`/api/admin/questions?${query}`);
    cfg.total = data.total || 0;
    renderQuestions(data.items || []);
    updateActivityPagination('question', cfg.total, cfg.page, cfg.pageSize);
    setError('#question-error');
  } catch (error) { setError('#question-error', error.message); }
}

function updateActivityPagination(kind, total, page, pageSize) {
  const prefix = kind === 'mistake' ? 'mistake' : 'question';
  const pages = Math.max(1, Math.ceil(total / pageSize));
  $(`#${prefix}-pagination`).hidden = total <= pageSize;
  $(`#${prefix}-page-label`).textContent = `第 ${page + 1} / ${pages} 页 · ${total} 条`;
  $(`#${prefix}-prev`).disabled = page === 0;
  $(`#${prefix}-next`).disabled = page >= pages - 1;
}

async function loadDocuments() {
  try {
    const data = await request('/api/documents?limit=20');
    state.documents = data.items || [];
    $('#document-list').innerHTML = state.documents.length ? state.documents.map((item) => { const raw = String(item.status || ''); const text = ({ queued: '排队中', processing: '处理中', completed: '已完成', failed: '失败', cancelled: '已取消' })[raw] || raw || '未知'; const statusClass = /失败|failed/i.test(raw) ? 'failed' : /处理中|processing|queued|pending/i.test(raw) ? 'processing' : ''; return `<div class="document-item"><div><strong>${esc(item.document_name)}</strong><small>${esc(item.grade_name)} · 更新于 ${formatDate(item.updated_at)}</small></div><div class="document-meta"><span class="document-status ${statusClass}">${esc(text)}</span></div></div>`; }).join('') : '<span class="muted-copy">还没有上传文档。</span>';
  } catch (error) { $('#document-list').textContent = error.message; }
}

async function loadActivityOptions() {
  const data = await request('/api/admin/activity-options');
  const students = data.students || [];
  const studentOptions = students.map((item) => `<option value="${esc(item.user_id)}">${esc(item.display_name || item.username)} · ${esc(item.username)}</option>`).join('');
  $('#mistake-user').insertAdjacentHTML('beforeend', studentOptions);
  $('#question-user').insertAdjacentHTML('beforeend', studentOptions);
  $('#mistake-point').insertAdjacentHTML('beforeend', (data.knowledge_points || []).map((point) => `<option value="${esc(point)}">${esc(point)}</option>`).join(''));
}

async function loadAdmin() {
  if (!token) { location.href = '/admin/login'; return; }
  try {
    const me = await request('/api/auth/me');
    if (me.role !== 'admin') throw new Error('只有管理员可以访问此页面');
    $('#admin-user-name').textContent = me.display_name || me.username;
    await Promise.all([loadOverview(), loadUsers(), loadActivityOptions()]);
  } catch (error) {
    localStorage.removeItem('qisi_access_token');
    setError('#admin-error', error.message);
    setTimeout(() => { location.href = '/admin/login'; }, 1200);
  }
}

$$('[data-admin-view]').forEach((item) => item.addEventListener('click', () => showView(item.dataset.adminView)));
$('#admin-refresh-current').addEventListener('click', async () => { await loadOverview(); if (state.view === 'users') await loadUsers(); if (state.view === 'mistakes') await loadMistakes(); if (state.view === 'questions') await loadQuestions(); if (state.view === 'content') await loadDocuments(); });
$('#admin-search').addEventListener('submit', (event) => { event.preventDefault(); loadUsers(); });
$('#admin-role-filter').addEventListener('change', () => { state.userPage = 0; renderUsers(); });
$('#admin-status-filter').addEventListener('change', () => { state.userPage = 0; renderUsers(); });
$('#admin-prev-page').addEventListener('click', () => { if (state.userPage > 0) { state.userPage -= 1; renderUsers(); } });
$('#admin-next-page').addEventListener('click', () => { state.userPage += 1; renderUsers(); });
$('#mistake-apply').addEventListener('click', () => { state.mistakes.page = 0; state.mistakes.filters = { status: $('#mistake-status').value, user_id: $('#mistake-user').value, knowledge_point_id: $('#mistake-point').value }; loadMistakes(); });
$('#question-apply').addEventListener('click', () => { state.questions.page = 0; state.questions.filters = { user_id: $('#question-user').value }; loadQuestions(); });
$('#mistake-prev').addEventListener('click', () => { if (state.mistakes.page > 0) { state.mistakes.page -= 1; loadMistakes(); } });
$('#mistake-next').addEventListener('click', () => { state.mistakes.page += 1; loadMistakes(); });
$('#question-prev').addEventListener('click', () => { if (state.questions.page > 0) { state.questions.page -= 1; loadQuestions(); } });
$('#question-next').addEventListener('click', () => { state.questions.page += 1; loadQuestions(); });
$('#admin-export-users').addEventListener('click', () => { if (!state.users.length) { setError('#admin-error', '暂无可导出的用户'); return; } const rows = [['用户名','显示名称','角色','状态','创建时间'], ...state.users.map((item) => [item.username, item.display_name, item.role, item.status, item.created_at])]; const csv = '\ufeff' + rows.map((row) => row.map((cell) => `"${String(cell ?? '').replace(/"/g, '""')}"`).join(',')).join('\n'); const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' })); const link = document.createElement('a'); link.href = url; link.download = `启思学伴-用户-${new Date().toISOString().slice(0,10)}.csv`; link.click(); URL.revokeObjectURL(url); });
$('#admin-users').addEventListener('click', async (event) => { const button = event.target.closest('button[data-action="toggle"]'); if (!button) return; const row = button.closest('tr'); const userId = row.dataset.userId; row.classList.add('row-saving'); try { const nextStatus = button.textContent === '停用' ? 'disabled' : 'active'; const updated = await request(`/api/admin/users/${encodeURIComponent(userId)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: nextStatus }) }); const index = state.users.findIndex((item) => item.user_id === userId); if (index >= 0) state.users[index] = { ...state.users[index], ...updated, status: nextStatus }; setError('#admin-error', '账号状态已更新'); renderUsers(); await loadOverview(); } catch (error) { row.classList.remove('row-saving'); setError('#admin-error', error.message); } });
$('#refresh-documents').addEventListener('click', loadDocuments);
$('#document-form').addEventListener('submit', async (event) => { event.preventDefault(); const file = $('#document-file').files[0]; if (!file) return; const submit = event.currentTarget.querySelector('button[type="submit"]'); submit.disabled = true; $('#document-jobs').textContent = '正在上传…'; try { const response = await request(`/api/documents/upload?grade_id=${encodeURIComponent($('#document-grade').value)}`, { method: 'POST', headers: { 'Content-Type': file.type || 'application/octet-stream', 'X-Filename': file.name }, body: await file.arrayBuffer() }); $('#document-jobs').textContent = `任务已创建：${response.job_id}，状态：${response.status}`; $('#document-file').value = ''; } catch (error) { $('#document-jobs').textContent = error.message; } finally { submit.disabled = false; } });
$('#admin-logout').addEventListener('click', () => { localStorage.removeItem('qisi_access_token'); location.href = '/'; });

loadAdmin();
