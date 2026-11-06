import { ApiError, del, get, onConnection, patch, post, put, qs, setCsrf } from './api.js';
import { add, badge, clear, fill, confirmDlg, dialog, field, fmtTime, h, select, toast, toastErr } from './ui.js';
import { mountStudio, unmountStudio } from './studio.js';
import { pageOps } from './ops.js';

export const session = { user: null, app: null };
const $ = (id) => document.getElementById(id);
const view = () => $('view');
const isOwner = () => session.user && session.user.role === 'OWNER';

// ------------------------------------------------------------------ khởi động & đăng nhập
async function boot() {
  onConnection((ok) => { $('conn-status').classList.toggle('down', !ok); $('conn-status').title = ok ? 'Đã kết nối máy chủ' : 'Mất kết nối máy chủ'; });
  $('login-form').addEventListener('submit', onLogin);
  $('logout').addEventListener('click', onLogout);
  $('nav-toggle').addEventListener('click', () => {
    const open = $('sidebar').classList.toggle('open');
    $('nav-toggle').setAttribute('aria-expanded', String(open));
  });
  $('sidebar').addEventListener('click', (e) => { if (e.target.tagName === 'A') $('sidebar').classList.remove('open'); });
  window.addEventListener('hashchange', route);
  try {
    const me = await get('/auth/me');
    startSession(me);
  } catch (e) {
    showLogin(e.status === 401 ? '' : e.message);
  } finally { $('boot').hidden = true; }
}

function showLogin(msg = '') {
  unmountStudio();
  $('shell').hidden = true; $('login').hidden = false; $('login-error').textContent = msg;
  $('login-form').username.focus();
}

function startSession(me) {
  session.user = me.user || { ...me.user };
  session.app = me.app || session.app;
  setCsrf(me.csrf_token);
  $('login').hidden = true; $('shell').hidden = false;
  $('whoami').textContent = `${session.user.display_name} · ${session.user.role}`;
  document.querySelectorAll('[data-owner]').forEach((a) => { a.hidden = !isOwner(); });
  if (!location.hash) location.hash = '#/dashboard';
  route();
}

async function onLogin(ev) {
  ev.preventDefault();
  const f = ev.target; const btn = f.querySelector('button'); btn.disabled = true; $('login-error').textContent = '';
  try {
    const res = await post('/auth/login', { username: f.username.value, password: f.password.value });
    f.password.value = '';
    const me = await get('/auth/me');
    startSession({ ...me, user: me.user || res.user });
  } catch (e) { $('login-error').textContent = e.message; } finally { btn.disabled = false; }
}

async function onLogout() {
  if (!(await unmountStudio({ confirmLeave: true }))) return;
  try { await post('/auth/logout'); } catch (e) { toastErr(e, 'Đăng xuất'); }
  session.user = null; setCsrf(null); showLogin();
}

// Mọi lỗi 401 giữa phiên → về màn đăng nhập.
window.addEventListener('unhandledrejection', (ev) => {
  if (ev.reason instanceof ApiError && ev.reason.status === 401) { ev.preventDefault(); showLogin('Phiên đã hết hạn, hãy đăng nhập lại.'); }
});

// ------------------------------------------------------------------ định tuyến
const pages = {
  dashboard: pageDashboard, ops: () => pageOps(view(), session), prompts: pagePrompts, projects: pageProjects, tasks: pageTasks, modules: pageModules,
  tools: pageTools, agents: pageAgents, ai: pageAi, activity: pageActivity, admin: pageAdmin,
};
let currentPage = null;

async function route() {
  if (!session.user) return;
  const [, name = 'dashboard', ...rest] = location.hash.replace(/^#/, '').split('/');
  const page = pages[name] ? name : 'dashboard';
  if (currentPage === 'prompts' && page !== 'prompts') {
    const ok = await unmountStudio({ confirmLeave: true });
    if (!ok) { history.replaceState(null, '', '#/prompts'); return; }
  }
  document.querySelectorAll('#sidebar a').forEach((a) => a.toggleAttribute('aria-current', a.dataset.route === page));
  document.querySelectorAll('#sidebar a[aria-current]').forEach((a) => a.setAttribute('aria-current', 'page'));
  currentPage = page;
  if (page !== 'prompts') fill(view(), h('p', { class: 'muted', text: 'Đang tải…' }));
  try { await pages[page](rest); } catch (e) {
    if (e instanceof ApiError && e.status === 401) { showLogin('Phiên đã hết hạn, hãy đăng nhập lại.'); return; }
    fill(view(), h('div', { class: 'note bad', role: 'alert' }, `Không tải được trang: ${e.message}`));
  }
}

function header(title, sub, ...actions) {
  return h('div', { class: 'row', style: undefined }, h('div', {}, h('h1', { text: title }), sub ? h('p', { class: 'muted small', text: sub }) : null),
    h('span', { class: 'spacer' }), actions);
}

// ------------------------------------------------------------------ Tổng quan
async function pageDashboard() {
  const d = await get('/dashboard');
  const kpi = (num, lbl) => h('div', { class: 'card kpi' }, h('div', { class: 'num', text: num }), h('div', { class: 'lbl', text: lbl }));
  const openTasks = (d.tasks.TODO || 0) + (d.tasks.IN_PROGRESS || 0) + (d.tasks.BLOCKED || 0);
  const integ = h('table', {}, h('thead', {}, h('tr', {}, h('th', { text: 'Module' }), h('th', { text: 'Vận hành' }), h('th', { text: 'Bằng chứng' }))),
    h('tbody', {}, Object.entries(d.integrations).map(([k, v]) => h('tr', {}, h('td', { text: k }), h('td', {}, badge(v.status)), h('td', { class: 'small', text: v.evidence })))));
  fill(view(), h('div', { class: 'stack' },
    header('Tổng quan', `Số liệu thật từ cơ sở dữ liệu · cập nhật ${fmtTime(d.generated_at)}`),
    h('div', { class: 'cards' },
      kpi(d.prompts.active, 'Prompt đang dùng'), kpi(d.prompts.versions, 'Phiên bản đã lưu'),
      kpi(d.prompts.approved_versions, 'Phiên bản APPROVED'), kpi(d.prompts.in_production, 'Prompt có production'),
      kpi(d.projects, 'Dự án'), kpi(openTasks, 'Việc đang mở'),
      kpi(d.ai.enabled ? 'BẬT' : 'TẮT', 'Gọi model AI'), kpi(d.ai.calls_today, 'Lời gọi AI hôm nay')),
    h('div', { class: 'cols' },
      h('div', { class: 'card' }, h('h2', { text: 'Prompt sửa gần đây' }),
        d.recent_prompts.length ? h('ul', {}, d.recent_prompts.map((p) => h('li', {}, h('a', { href: `#/prompts/${p.id}`, text: p.title }), h('span', { class: 'muted small', text: ` · ${fmtTime(p.updated_at)}` }))))
          : h('p', { class: 'empty', text: 'Chưa có prompt. Vào Prompt Studio để tạo.' })),
      h('div', { class: 'card' }, h('h2', { text: 'Trạng thái module & kết nối' }), h('div', { class: 'table-wrap' }, integ))),
    h('div', { class: 'card' }, h('h2', { text: 'Hoạt động gần đây' }), activityTable(d.recent_activity))));
}

function activityTable(items) {
  if (!items.length) return h('p', { class: 'empty', text: 'Chưa có hoạt động.' });
  return h('div', { class: 'table-wrap' }, h('table', {},
    h('thead', {}, h('tr', {}, ['Thời gian', 'Tác nhân', 'Hành động', 'Đối tượng', 'Kết quả'].map((t) => h('th', { text: t })))),
    h('tbody', {}, items.map((a) => h('tr', {}, h('td', { class: 'small', text: fmtTime(a.ts) }), h('td', { class: 'small', text: `${a.actor_type}${a.actor_id ? ` ${a.actor_id}` : ''}` }),
      h('td', { text: a.action }), h('td', { class: 'small', text: [a.object_type, a.object_id].filter(Boolean).join(' ') }), h('td', {}, badge(a.result)))))));
}

// ------------------------------------------------------------------ Prompt Studio
async function pagePrompts(rest) { await mountStudio(view(), { promptId: rest[0] || null, session }); }

// ------------------------------------------------------------------ Dự án
async function pageProjects() {
  const [{ items }, users] = await Promise.all([get('/projects'), isOwner() ? get('/admin/users') : Promise.resolve({ items: [] })]);
  const body = h('div', { class: 'stack' });
  const addBtn = isOwner() ? h('button', { class: 'btn primary', text: '+ Dự án', onclick: () => {
    const name = h('input', { required: true, maxlength: 120 }); const desc = h('textarea', { rows: 3 });
    dialog('Tạo dự án', h('div', { class: 'stack' }, field('Tên', name), field('Mô tả', desc)), [{ label: 'Tạo', kind: 'primary', onClick: async (close) => {
      await post('/projects', { name: name.value, description: desc.value }); close(); toast('Đã tạo dự án'); route();
    } }]);
  } }) : null;
  fill(view(), header('Dự án', 'Phân quyền theo dự án: OWNER thấy mọi dự án; người khác cần là thành viên.', addBtn), body);
  if (!items.length) { add(body, h('p', { class: 'empty', text: 'Chưa có dự án nào bạn được truy cập.' })); return; }
  for (const p of items) {
    const card = h('div', { class: 'card stack' },
      h('div', { class: 'row' }, h('h2', { text: p.name }), p.archived_at ? badge('CANCELLED', 'Đã lưu trữ') : null, h('span', { class: 'spacer' }),
        h('span', { class: 'muted small', text: `${p.prompt_count} prompt · vai trò: ${p.your_role}` })),
      p.description ? h('p', { class: 'muted', text: p.description }) : null,
      h('a', { href: `#/prompts`, text: 'Mở Prompt Studio', onclick: () => sessionStorage.setItem('ci.filter.project', p.id) }));
    if (isOwner()) {
      const membersBox = h('div', { class: 'small muted', text: 'Đang tải thành viên…' });
      add(card, membersBox);
      get(`/projects/${p.id}/members`).then(({ items: mem }) => {
        fill(membersBox, h('strong', { text: 'Thành viên: ' }), mem.length ? mem.map((m) => `${m.display_name} (${m.role})`).join(', ') : 'chưa có');
        const others = users.items.filter((u) => u.role !== 'OWNER');
        if (others.length) {
          const us = select(others.map((u) => [u.id, `${u.display_name} — ${u.role}`]));
          const rs = select([['EDITOR', 'EDITOR (sửa)'], ['VIEWER', 'VIEWER (xem)'], ['', 'Gỡ khỏi dự án']]);
          add(membersBox, h('div', { class: 'row' }, us, rs, h('button', { class: 'btn small', text: 'Cập nhật', onclick: async () => {
            try {
              if (rs.value) await put(`/projects/${p.id}/members/${us.value}`, { role: rs.value });
              else await del(`/projects/${p.id}/members/${us.value}`);
              toast('Đã cập nhật thành viên'); route();
            } catch (e) { toastErr(e); }
          } })));
        }
      }).catch((e) => { membersBox.textContent = `Lỗi tải thành viên: ${e.message}`; });
    }
    add(body, card);
  }
}

// ------------------------------------------------------------------ Việc
async function pageTasks() {
  const [{ items }, projects] = await Promise.all([get('/tasks'), get('/projects')]);
  const pname = Object.fromEntries(projects.items.map((p) => [p.id, p.name]));
  const newTaskBtn = h('button', { class: 'btn primary', text: '+ Việc', onclick: () => {
    const t = h('input', { maxlength: 200 }); const d = h('textarea', { rows: 3 }); const a = h('input', { maxlength: 80, placeholder: 'VD: A14 hoặc tên người' });
    const p = select([['', '— Không gắn dự án (chỉ OWNER) —'], ...projects.items.map((x) => [x.id, x.name])]);
    dialog('Tạo việc', h('div', { class: 'stack' }, field('Tiêu đề', t), field('Dự án', p), field('Người phụ trách', a), field('Mô tả', d)), [{ label: 'Tạo', kind: 'primary', onClick: async (close) => {
      await post('/tasks', { title: t.value, description: d.value, assignee: a.value, project_id: p.value || null }); close(); toast('Đã tạo việc'); route();
    } }]);
  } });
  const rows = items.map((t) => {
    const s = select(['TODO', 'IN_PROGRESS', 'BLOCKED', 'DONE', 'CANCELLED'], t.status, { 'aria-label': `Trạng thái ${t.title}` });
    s.addEventListener('change', async () => {
      try { await patch(`/tasks/${t.id}`, { status: s.value }, { ifMatch: t.revision }); toast('Đã cập nhật'); route(); } catch (e) { toastErr(e); s.value = t.status; }
    });
    return h('tr', {}, h('td', {}, h('strong', { text: t.title }), t.description ? h('div', { class: 'small muted', text: t.description }) : null),
      h('td', { class: 'small', text: t.project_id ? pname[t.project_id] || t.project_id : '—' }), h('td', { class: 'small', text: t.assignee || '—' }), h('td', {}, s), h('td', { class: 'small', text: fmtTime(t.updated_at) }));
  });
  fill(view(), h('div', { class: 'stack' }, header('Việc', 'Sổ việc tối thiểu của CROWN INFINITY (tách khỏi sổ nhiệm vụ Agent).', newTaskBtn),
    items.length ? h('div', { class: 'card table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['Việc', 'Dự án', 'Phụ trách', 'Trạng thái', 'Cập nhật'].map((x) => h('th', { text: x })))), h('tbody', {}, rows)))
      : h('p', { class: 'empty', text: 'Chưa có việc.' })));
}

// ------------------------------------------------------------------ Module Registry
async function pageModules() {
  const d = await get('/modules');
  const cards = d.items.map((m) => {
    const toggle = h('input', { type: 'checkbox', checked: m.enabled, disabled: !isOwner() || ['PLANNED', 'BLOCKED'].includes(m.dev_status) || m.module_id === 'crown_admin_center', 'aria-label': `Bật ${m.name}` });
    toggle.addEventListener('change', async () => {
      try { await patch(`/modules/${m.module_id}`, { enabled: toggle.checked }); toast(`${m.name}: ${toggle.checked ? 'đã bật' : 'đã tắt'}`); route(); } catch (e) { toggle.checked = !toggle.checked; toastErr(e); }
    });
    return h('div', { class: 'card stack' },
      h('div', { class: 'row' }, h('span', { class: 'muted small', text: m.code }), h('h2', { text: m.name }), h('span', { class: 'spacer' }), badge(m.dev_status), badge(m.op_status)),
      h('p', { text: m.description }),
      m.implemented_scope ? h('div', { class: 'small' }, h('strong', { text: 'Đã có: ' }), m.implemented_scope.join(' · ')) : null,
      m.not_yet ? h('div', { class: 'small muted' }, h('strong', { text: 'Chưa có: ' }), m.not_yet.join(' · ')) : null,
      m.planned_notes ? h('div', { class: 'note small', text: m.planned_notes }) : null,
      m.verification ? h('div', { class: 'small', text: `Kiểm chứng: ${m.verification}` }) : null,
      h('div', { class: 'small muted', text: `Vận hành: ${m.op_evidence}` }),
      h('label', { class: 'row' }, toggle, h('span', { text: ['PLANNED', 'BLOCKED'].includes(m.dev_status) ? 'Chưa triển khai — không bật được' : 'Bật module' })));
  });
  fill(view(), h('div', { class: 'stack' }, header('Module Registry', d.note), h('div', { class: 'cols' }, cards)));
}

// ------------------------------------------------------------------ Tool Registry
async function pageTools() {
  const d = await get('/tools');
  const sync = isOwner() ? h('button', { class: 'btn', text: 'Đồng bộ từ kho TOOL', onclick: async (ev) => {
    const b = ev.currentTarget;
    b.disabled = true;
    try { const r = await post('/tools/sync'); toast(`Đã đọc ${r.synced} tool (${r.status})`); route(); } catch (e) { toastErr(e); b.disabled = false; }
  } }) : null;
  const list = d.items.length ? d.items.map((t) => h('div', { class: 'card stack' },
    h('div', { class: 'row' }, h('h2', { text: t.name }), h('span', { class: 'spacer' }), badge('info', t.category || 'khác'), h('span', { class: 'small muted', text: `v${t.version}` })),
    h('p', { text: t.description }),
    h('div', { class: 'small' }, h('strong', { text: 'Gọi: ' }), h('code', { text: t.details.how_to_call })),
    h('div', { class: 'small muted', text: `Phiên bản: ${(t.details.versions || []).map((v) => `${v.version} ${v.status}`).join(', ')} · nguồn ${t.source_ref} · đồng bộ ${fmtTime(t.synced_at)}` })))
    : [h('p', { class: 'empty', text: 'Chưa có tool trong registry. OWNER bấm "Đồng bộ từ kho TOOL" để đọc tool/catalog.' })];
  fill(view(), h('div', { class: 'stack' }, header('Tool Registry', d.note, sync), h('div', { class: 'cols' }, list)));
}

// ------------------------------------------------------------------ CROWN AGENT
async function pageAgents() {
  const [health, agents] = await Promise.all([get('/integrations/crown-agent/health'), get('/agents')]);
  const syncBtn = h('button', { class: 'btn', text: 'Đọc lại sổ đăng ký Agent', onclick: async () => { try { await post('/agents/sync'); toast('Đã đồng bộ'); route(); } catch (e) { toastErr(e); } } });
  const rows = agents.items.map((a) => h('tr', {},
    h('td', {}, h('strong', { text: a.agent_id }), h('div', { class: 'small muted', text: a.display_name })),
    h('td', { class: 'small', text: a.source }),
    h('td', {}, a.has_token ? badge('ok', `token …${a.token_hint}`) : badge('info', 'chưa có token')),
    h('td', { class: 'small', text: String(a.active_assignments) }),
    h('td', { class: 'small', text: fmtTime(a.last_used_at) }),
    h('td', {}, h('div', { class: 'row' },
      h('button', { class: 'btn small', text: a.has_token ? 'Cấp lại token' : 'Cấp token', onclick: async () => {
        if (a.has_token && !(await confirmDlg('Cấp lại token', `Token cũ của ${a.agent_id} sẽ hết hiệu lực ngay.`))) return;
        try {
          const r = await post(`/agents/${a.agent_id}/token`);
          dialog(`Token của ${a.agent_id}`, h('div', { class: 'stack' }, h('p', { class: 'note', text: r.warning }), h('div', { class: 'secret', text: r.token }),
            h('p', { class: 'small muted', text: 'Agent gọi: GET /api/v1/agent/prompts/<prompt_id> với header Authorization: Bearer <token>.' })));
          route();
        } catch (e) { toastErr(e); }
      } }),
      a.has_token ? h('button', { class: 'btn small danger', text: 'Thu hồi', onclick: async () => {
        if (!(await confirmDlg('Thu hồi token', `${a.agent_id} sẽ không đọc được prompt nữa.`, 'Thu hồi', 'danger'))) return;
        try { await del(`/agents/${a.agent_id}/token`); toast('Đã thu hồi'); route(); } catch (e) { toastErr(e); }
      } }) : null))));
  const ledger = h('div', { class: 'card' }, h('h2', { text: 'Sổ nhiệm vụ CROWN AGENT (chỉ đọc)' }));
  if (health.ledger.status === 'ONLINE') {
    get('/integrations/crown-agent/tasks').then((t) => add(ledger, t.items.length ? h('ul', {}, t.items.map((x) => h('li', { class: 'small', text: `${x.task_id} · ${x.owner_agent_id} · ${x.state} · ${x.objective}` }))) : h('p', { class: 'empty', text: 'Sổ trống.' })))
      .catch((e) => add(ledger, h('p', { class: 'note bad', text: e.message })));
  } else add(ledger, h('p', { class: 'note', text: `Chưa kết nối: ${health.ledger.evidence}. Không hiển thị dữ liệu giả.` }));
  fill(view(), h('div', { class: 'stack' },
    header('CROWN AGENT CONTROL', 'Adapter chỉ đọc sổ đăng ký trong repo crown-agent; cấp prompt theo phiên bản cố định. Chưa có thao tác giao/dừng nhiệm vụ.', syncBtn),
    h('div', { class: 'cols' },
      h('div', { class: 'card' }, h('h2', { text: 'Sổ đăng ký Agent' }), badge(health.registry.status), h('p', { class: 'small muted', text: health.registry.evidence })),
      h('div', { class: 'card' }, h('h2', { text: 'Sổ nhiệm vụ' }), badge(health.ledger.status), h('p', { class: 'small muted', text: health.ledger.evidence }))),
    h('div', { class: 'card table-wrap' }, h('h2', { text: `Danh tính Agent (${agents.items.length})` }),
      agents.items.length ? h('table', {}, h('thead', {}, h('tr', {}, ['Agent', 'Nguồn', 'Token', 'Prompt được cấp', 'Dùng lần cuối', ''].map((x) => h('th', { text: x })))), h('tbody', {}, rows))
        : h('p', { class: 'empty', text: 'Chưa có. Bấm "Đọc lại sổ đăng ký Agent".' })),
    ledger));
}

// ------------------------------------------------------------------ AI Hub
async function pageAi() {
  const d = await get('/ai');
  const en = h('input', { type: 'checkbox', checked: d.enabled });
  en.addEventListener('change', async () => {
    if (en.checked && !(await confirmDlg('Bật AI Hub', 'Bật chỉ cho phép lời gọi đi qua cổng ngân sách. Ngân sách đang là ' +
      `${d.budget.daily_usd} USD/ngày. Phiên bản này chưa có transport gọi model thật.`, 'Bật'))) { en.checked = false; return; }
    try { await put('/ai/settings', { enabled: en.checked }); toast(en.checked ? 'Đã bật' : 'Đã tắt'); route(); } catch (e) { en.checked = !en.checked; toastErr(e); }
  });
  const b = d.budget; const inputs = {};
  const bf = h('div', { class: 'grid2' }, Object.entries({ daily_usd: 'Trần/ngày (USD)', monthly_usd: 'Trần/tháng (USD)', per_request_usd: 'Trần/request (USD)', max_output_tokens: 'Token đầu ra tối đa', timeout_seconds: 'Timeout (giây)', max_retries: 'Thử lại tối đa', max_concurrency: 'Gọi đồng thời tối đa' })
    .map(([k, l]) => { inputs[k] = h('input', { type: 'number', min: 0, step: k.endsWith('usd') ? '0.01' : '1', value: b[k] }); return field(l, inputs[k]); }));
  const saveB = h('button', { class: 'btn primary', text: 'Lưu ngân sách', onclick: async () => {
    try { await put('/ai/settings', { budget: Object.fromEntries(Object.entries(inputs).map(([k, i]) => [k, Number(i.value)])) }); toast('Đã lưu ngân sách'); route(); } catch (e) { toastErr(e); }
  } });
  const pv = d.providers.length ? h('table', {}, h('thead', {}, h('tr', {}, ['Tên', 'Loại', 'Model', 'Biến khóa', 'Khóa', 'Trả phí', 'Bật'].map((x) => h('th', { text: x })))),
    h('tbody', {}, d.providers.map((p) => h('tr', {}, h('td', { text: p.name }), h('td', { text: p.kind }), h('td', { text: p.model || '—' }), h('td', {}, h('code', { text: p.key_env || '—' })),
      h('td', {}, badge(p.key_configured ? 'ok' : 'info', p.key_configured ? 'đã đặt' : 'chưa đặt')), h('td', { text: p.paid ? 'có' : 'không' }),
      h('td', {}, h('button', { class: 'btn small', text: p.enabled ? 'Tắt' : 'Bật', onclick: async () => { try { await patch(`/ai/providers/${p.id}`, { enabled: !p.enabled }); route(); } catch (e) { toastErr(e); } } }))))))
    : h('p', { class: 'empty', text: 'Chưa cấu hình provider.' });
  const addP = h('button', { class: 'btn', text: '+ Provider', onclick: () => {
    const n = h('input'); const k = select(['anthropic', 'openai', 'openrouter', 'ollama', 'other']); const m = h('input', { placeholder: 'VD: tên model' });
    const env = h('input', { placeholder: 'VD: ANTHROPIC_API_KEY' }); const url = h('input', { placeholder: 'https://… (tùy chọn)' });
    dialog('Thêm provider', h('div', { class: 'stack' }, h('p', { class: 'note', text: 'Không nhập khóa API ở đây. Đặt khóa vào biến môi trường của máy chủ rồi ghi TÊN biến.' }),
      field('Tên', n), field('Loại', k), field('Model', m), field('Tên biến môi trường chứa khóa', env), field('Base URL', url)),
    [{ label: 'Thêm', kind: 'primary', onClick: async (close) => { await post('/ai/providers', { name: n.value, kind: k.value, model: m.value, key_env: env.value, base_url: url.value }); close(); route(); } }]);
  } });
  fill(view(), h('div', { class: 'stack' }, header('AI Hub', 'Mọi lời gọi trả phí TẮT mặc định. Thư viện, phiên bản, tìm kiếm, nhập/xuất chạy đầy đủ khi không có khóa API.'),
    h('div', { class: 'card stack' }, h('label', { class: 'row' }, en, h('strong', { text: 'Cho phép gọi model (qua cổng ngân sách)' })), d.notes.map((n) => h('p', { class: 'small muted', text: `• ${n}` }))),
    h('div', { class: 'card stack' }, h('h2', { text: 'Ngân sách' }), bf, h('div', { class: 'row end' }, saveB),
      h('p', { class: 'small muted', text: `Đã dùng hôm nay: ${d.usage.spent_today_usd} USD · tháng này: ${d.usage.spent_month_usd} USD. ${d.usage.note}` })),
    h('div', { class: 'card stack table-wrap' }, h('div', { class: 'row' }, h('h2', { text: 'Provider' }), h('span', { class: 'spacer' }), addP), pv)));
}

// ------------------------------------------------------------------ Nhật ký
async function pageActivity() {
  const box = h('div', { class: 'card' }); let last = null;
  const more = h('button', { class: 'btn', text: 'Tải thêm', onclick: () => load() });
  async function load() {
    more.disabled = true;
    try {
      const d = await get(`/activity${qs({ limit: 50, before_id: last })}`);
      if (d.items.length) { last = d.items[d.items.length - 1].id; add(box, activityTable(d.items)); }
      more.hidden = d.items.length < 50;
    } catch (e) { toastErr(e); } finally { more.disabled = false; }
  }
  fill(view(), h('div', { class: 'stack' }, header('Nhật ký hoạt động', isOwner() ? 'Chỉ thêm, không sửa/xóa. Không ghi nội dung prompt hay secret.' : 'Bạn thấy hoạt động của chính mình.'), box, h('div', { class: 'row end' }, more)));
  await load();
}

// ------------------------------------------------------------------ Quản trị
async function pageAdmin() {
  const out = h('div', { class: 'stack' }, header('Quản trị', 'Người dùng, phiên đăng nhập, sao lưu. MFA/SSO: chưa triển khai.'));
  const cur = h('input', { type: 'password', autocomplete: 'current-password' }); const nw = h('input', { type: 'password', autocomplete: 'new-password', minlength: 10 });
  add(out, h('div', { class: 'card stack' }, h('h2', { text: 'Đổi mật khẩu của tôi' }), h('div', { class: 'grid2' }, field('Mật khẩu hiện tại', cur), field('Mật khẩu mới (≥10 ký tự)', nw)),
    h('div', { class: 'row end' }, h('button', { class: 'btn', text: 'Thu hồi mọi phiên khác', onclick: async () => {
      try { await post(`/admin/users/${session.user.user_id}/revoke-sessions`); toast('Đã thu hồi mọi phiên (kể cả phiên này)'); showLogin(); } catch (e) { toastErr(e); }
    } }), h('button', { class: 'btn primary', text: 'Đổi mật khẩu', onclick: async () => {
      try { await post('/auth/password', { current_password: cur.value, new_password: nw.value }); cur.value = ''; nw.value = ''; toast('Đã đổi mật khẩu; các phiên khác bị thu hồi'); } catch (e) { toastErr(e); }
    } }))));
  if (isOwner()) {
    const [users, backups] = await Promise.all([get('/admin/users'), get('/admin/backups')]);
    const un = h('input', { autocomplete: 'off' }); const dn = h('input'); const pw = h('input', { type: 'password', autocomplete: 'new-password' }); const rl = select(['EDITOR', 'VIEWER', 'OWNER'], 'EDITOR');
    add(out, h('div', { class: 'card stack table-wrap' }, h('h2', { text: 'Người dùng' }),
      h('table', {}, h('thead', {}, h('tr', {}, ['Tên', 'Vai trò', 'Trạng thái', ''].map((x) => h('th', { text: x })))),
        h('tbody', {}, users.items.map((u) => h('tr', {}, h('td', {}, h('strong', { text: u.username }), h('div', { class: 'small muted', text: u.display_name })), h('td', { text: u.role }),
          h('td', {}, badge(u.is_active ? 'ok' : 'bad', u.is_active ? 'hoạt động' : 'đã khóa')),
          h('td', {}, u.id === session.user.user_id ? h('span', { class: 'small muted', text: '(bạn)' }) : h('div', { class: 'row' },
            h('button', { class: 'btn small', text: u.is_active ? 'Khóa' : 'Mở khóa', onclick: async () => { try { await patch(`/admin/users/${u.id}`, { is_active: !u.is_active }); route(); } catch (e) { toastErr(e); } } }),
            h('button', { class: 'btn small', text: 'Thu hồi phiên', onclick: async () => { try { const r = await post(`/admin/users/${u.id}/revoke-sessions`); toast(`Đã thu hồi ${r.revoked} phiên`); } catch (e) { toastErr(e); } } }))))))),
      h('h3', { text: 'Thêm người dùng' }), h('div', { class: 'grid2' }, field('Tên đăng nhập', un), field('Tên hiển thị', dn), field('Mật khẩu (≥10 ký tự)', pw), field('Vai trò', rl)),
      h('div', { class: 'row end' }, h('button', { class: 'btn primary', text: 'Tạo', onclick: async () => {
        try { await post('/admin/users', { username: un.value, display_name: dn.value, password: pw.value, role: rl.value }); toast('Đã tạo người dùng'); route(); } catch (e) { toastErr(e); }
      } }))));
    add(out, h('div', { class: 'card stack' }, h('div', { class: 'row' }, h('h2', { text: 'Sao lưu' }), h('span', { class: 'spacer' }),
      h('button', { class: 'btn primary', text: 'Sao lưu ngay', onclick: async (ev) => {
    const b = ev.currentTarget;
        b.disabled = true;
        try { const r = await post('/admin/backups'); toast(`Đã sao lưu ${r.name}`); route(); } catch (e) { toastErr(e); b.disabled = false; }
      } })),
      h('p', { class: 'small muted', text: `Thư mục: ${backups.directory}. Khôi phục: dừng máy chủ rồi chạy  py -m crown_infinity restore <tệp.zip>  (tự kiểm tra trên bản tạm và sao lưu dữ liệu hiện tại trước khi thay).` }),
      backups.items.length ? h('ul', {}, backups.items.map((b) => h('li', { class: 'small', text: `${b.name} · ${(b.bytes / 1024).toFixed(1)} KB` }))) : h('p', { class: 'empty', text: 'Chưa có bản sao lưu.' })));
  }
  fill(view(), out);
}

boot();
