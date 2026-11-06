// CROWN PROMPT STUDIO — thư viện (trái) · trình soạn (giữa) · thông tin & phân tích (phải) · lịch sử/biến/xuất/nguồn/Agent (dưới).
// Database là nơi lưu chính. localStorage chỉ là lớp dự phòng cho bản nháp chưa lên máy chủ.
import { del, get, patch, post, put, qs } from './api.js';
import { add, badge, clear, fill, confirmDlg, countWords, dialog, diffView, field, fmtTime, h, lineDiff, readFileB64, renderMarkdown, select, toast, toastErr } from './ui.js';

const AUTOSAVE_MS = 1200;
const VAR_RE = /\{\{\s*([^\W\d][\w.]*)\s*\}\}/gu;
let S = null; // trạng thái màn Studio
let C = null; // prompt đang mở

const lsKey = (id) => `ci.draft.${id}`;
function lsGet(id) { try { const v = localStorage.getItem(lsKey(id)); return v ? JSON.parse(v) : null; } catch { return null; } }
function lsSet(id, v) { try { localStorage.setItem(lsKey(id), JSON.stringify(v)); } catch { /* bộ nhớ trình duyệt bị chặn: vẫn chạy được */ } }
function lsDel(id) { try { localStorage.removeItem(lsKey(id)); } catch { /* bỏ qua */ } }

const canWrite = () => C && ['OWNER', 'EDITOR'].includes(C.data.your_role) && !C.data.archived_at;
const isOwner = () => S && S.session.user.role === 'OWNER';

function onBeforeUnload(e) {
  if (C && (C.text !== C.baseDraft || C.status === 'saving')) { e.preventDefault(); e.returnValue = ''; }
}

export async function mountStudio(root, { promptId, session }) {
  if (!S || S.root !== root || !S.el.isConnected) build(root, session);
  if (promptId) {
    if (!C || C.id !== promptId) await openPrompt(promptId);
  } else if (C) {
    await closePrompt();
  }
  await loadLibrary();
}

export async function unmountStudio({ confirmLeave = false } = {}) {
  if (!S) return true;
  if (C) {
    if (C.text !== C.baseDraft && !C.conflict) await queueSave();
    if (confirmLeave && C && (C.text !== C.baseDraft || C.conflict)) {
      const ok = await confirmDlg('Rời Prompt Studio?', 'Bản nháp chưa lưu được lên máy chủ. Bản sao vẫn còn trong trình duyệt này và sẽ được đề nghị khôi phục khi mở lại.', 'Rời trang', 'danger');
      if (!ok) return false;
    }
  }
  window.removeEventListener('beforeunload', onBeforeUnload);
  if (C) clearTimeout(C.saveTimer);
  S = null; C = null;
  return true;
}

// ------------------------------------------------------------------ bố cục
function build(root, session) {
  S = { root, session, page: 1, projects: [], facets: { tags: [], categories: [], target_ais: [] } };
  const savedProject = sessionStorage.getItem('ci.filter.project') || '';
  sessionStorage.removeItem('ci.filter.project');
  S.q = h('input', { type: 'search', placeholder: 'Tìm tiêu đề, nội dung, tag (có/không dấu)…', 'aria-label': 'Tìm prompt' });
  S.fProject = select([['', 'Mọi dự án']], savedProject, { 'aria-label': 'Lọc dự án' });
  S.fProject.dataset.want = savedProject;
  S.fTag = select([['', 'Mọi tag']], '', { 'aria-label': 'Lọc tag' });
  S.fCat = select([['', 'Mọi danh mục']], '', { 'aria-label': 'Lọc danh mục' });
  S.fAi = select([['', 'Mọi AI đích']], '', { 'aria-label': 'Lọc AI đích' });
  S.fFav = h('input', { type: 'checkbox' }); S.fArch = h('input', { type: 'checkbox' });
  S.list = h('div', { class: 'lib-list', role: 'list' });
  S.pager = h('div', { class: 'row small muted' });
  let qTimer;
  S.q.addEventListener('input', () => { clearTimeout(qTimer); qTimer = setTimeout(() => { S.page = 1; loadLibrary(); }, 300); });
  [S.fProject, S.fTag, S.fCat, S.fAi, S.fFav, S.fArch].forEach((x) => x.addEventListener('change', () => { S.page = 1; loadLibrary(); }));
  const libToggle = h('button', { id: 'lib-toggle', class: 'btn small', text: 'Thư viện ▾', onclick: () => S.lib.classList.toggle('collapsed') });
  S.lib = h('aside', { class: 'lib card', 'aria-label': 'Thư viện prompt' },
    h('div', { class: 'row' }, h('h2', { text: 'Thư viện' }), h('span', { class: 'spacer' }), libToggle),
    h('div', { class: 'lib-body stack' },
      h('div', { class: 'row' }, h('button', { class: 'btn primary small', text: '+ Prompt mới', onclick: newPromptDialog }), h('button', { class: 'btn small', text: 'Nhập tệp', onclick: importDialog })),
      S.q, S.fProject, h('div', { class: 'grid2' }, S.fTag, S.fCat), S.fAi,
      h('div', { class: 'row small' }, h('label', { class: 'row' }, S.fFav, 'Yêu thích'), h('label', { class: 'row' }, S.fArch, 'Đã lưu trữ')),
      S.list, S.pager));
  S.center = h('section', { class: 'editor', 'aria-label': 'Trình soạn' });
  S.meta = h('aside', { class: 'meta', 'aria-label': 'Thông tin prompt' });
  S.el = h('div', { class: 'studio' }, S.lib, S.center, S.meta);
  fill(root, S.el);
  window.addEventListener('beforeunload', onBeforeUnload);
  showEmpty();
  get('/projects').then((r) => {
    S.projects = r.items;
    const want = S.fProject.dataset.want;
    fill(S.fProject, h('option', { value: '', text: 'Mọi dự án' }), ...r.items.map((p) => h('option', { value: p.id, text: p.name, selected: p.id === want })));
  }).catch((e) => toastErr(e, 'Tải dự án'));
  loadFacets();
}

function loadFacets() {
  get('/prompts/facets').then((f) => {
    if (!S) return;
    S.facets = f;
    const fill = (sel, items, first) => { const v = sel.value; fill(sel, h('option', { value: '', text: first }), ...items.map((x) => h('option', { value: x, text: x, selected: x === v }))); };
    fill(S.fTag, f.tags, 'Mọi tag'); fill(S.fCat, f.categories, 'Mọi danh mục'); fill(S.fAi, f.target_ais, 'Mọi AI đích');
  }).catch(() => {});
}

function showEmpty() {
  fill(S.center, h('div', { class: 'card empty' }, h('p', { text: 'Chọn một prompt trong thư viện hoặc tạo prompt mới.' }),
    h('button', { class: 'btn primary', text: '+ Prompt mới', onclick: newPromptDialog })));
  clear(S.meta);
}

// ------------------------------------------------------------------ thư viện
async function loadLibrary() {
  if (!S) return;
  const params = { q: S.q.value.trim(), project_id: S.fProject.value || S.fProject.dataset.want, tag: S.fTag.value, category: S.fCat.value, target_ai: S.fAi.value,
    favorite: S.fFav.checked ? '1' : '', archived: S.fArch.checked ? '1' : '0', page: S.page, limit: 25 };
  S.fProject.dataset.want = '';
  try {
    const r = await get(`/prompts${qs(params)}`);
    if (!S) return;
    clear(S.list);
    if (!r.items.length) add(S.list, h('p', { class: 'empty small', text: params.q ? 'Không có kết quả.' : 'Chưa có prompt.' }));
    for (const p of r.items) {
      add(S.list, h('button', { class: 'lib-item', role: 'listitem', 'aria-current': C && C.id === p.id ? 'true' : 'false', onclick: () => { location.hash = `#/prompts/${p.id}`; } },
        h('span', { class: 't', text: `${p.favorite ? '★ ' : ''}${p.title}` }),
        h('span', { class: 'small muted', text: `${p.project_name} · ${fmtTime(p.updated_at)}${p.has_production ? ' · production' : ''}` }),
        p.tags.length ? h('span', { class: 'tags' }, p.tags.map((t) => h('span', { class: 'tag', text: t }))) : null));
    }
    const pages = Math.max(1, Math.ceil(r.total / r.limit));
    fill(S.pager, `${r.total} prompt · trang ${r.page}/${pages}`, h('span', { class: 'spacer' }),
      h('button', { class: 'btn small', text: '‹', disabled: r.page <= 1, 'aria-label': 'Trang trước', onclick: () => { S.page -= 1; loadLibrary(); } }),
      h('button', { class: 'btn small', text: '›', disabled: r.page >= pages, 'aria-label': 'Trang sau', onclick: () => { S.page += 1; loadLibrary(); } }));
  } catch (e) {
    fill(S.list, h('p', { class: 'note bad', role: 'alert', text: `Không tải được thư viện: ${e.message}` }));
  }
}

// ------------------------------------------------------------------ mở / đóng prompt
async function closePrompt() {
  if (C) { clearTimeout(C.saveTimer); if (C.text !== C.baseDraft && !C.conflict) await queueSave(); }
  C = null; showEmpty();
}

async function openPrompt(id) {
  if (C && C.id !== id) await closePrompt();
  fill(S.center, h('p', { class: 'muted', text: 'Đang mở…' }));
  let d;
  try { d = await get(`/prompts/${id}`); } catch (e) {
    fill(S.center, h('div', { class: 'note bad', role: 'alert', text: `Không mở được prompt: ${e.message}` })); return;
  }
  if (!S) return;
  C = { id, data: d, baseRev: d.revision, baseDraft: d.draft_content, text: d.draft_content, saveTimer: null, chain: Promise.resolve(), status: 'saved', conflict: null, tab: 'history', preview: false };
  renderEditor();
  renderMeta();
  if (window.matchMedia('(max-width: 760px)').matches) S.lib.classList.add('collapsed'); // điện thoại: ưu tiên trình soạn
  const cached = lsGet(id);
  if (cached && cached.text !== d.draft_content && canWrite()) showRecovery(cached);
  else if (cached) lsDel(id);
}

// ------------------------------------------------------------------ trình soạn
function renderEditor() {
  const d = C.data;
  C.statusEl = h('span', { class: 'save-state', role: 'status', 'aria-live': 'polite' });
  C.retryBtn = h('button', { class: 'btn small', text: 'Thử lưu lại', hidden: true, onclick: () => queueSave() });
  C.title = h('input', { class: 'title-input', value: d.title, maxlength: 200, 'aria-label': 'Tiêu đề prompt', readonly: !canWrite() });
  C.title.addEventListener('change', () => saveMeta({ title: C.title.value }));
  C.ta = h('textarea', { class: 'content', spellcheck: 'false', 'aria-label': 'Nội dung prompt', readonly: !canWrite() });
  C.ta.value = C.text;
  C.ta.addEventListener('input', onInput);
  C.ta.addEventListener('keydown', (e) => { if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); queueSave(); } });
  C.previewEl = h('div', { class: 'preview', hidden: true });
  C.counts = h('div', { class: 'counts' });
  C.banners = h('div', { class: 'stack' });
  C.tabsBody = h('div', { class: 'card stack' });
  const prevBtn = h('button', { class: 'btn small', text: 'Xem trước', 'aria-pressed': 'false', onclick: () => {
    C.preview = !C.preview; prevBtn.setAttribute('aria-pressed', String(C.preview)); prevBtn.textContent = C.preview ? 'Sửa' : 'Xem trước';
    C.ta.hidden = C.preview; C.previewEl.hidden = !C.preview;
    if (C.preview) fill(C.previewEl, renderMarkdown(C.text));
  } });
  const tools = h('div', { class: 'row' },
    canWrite() ? h('button', { class: 'btn primary small', text: 'Lưu phiên bản', onclick: commitDialog }) : null,
    prevBtn,
    h('button', { class: 'btn small', text: d.favorite ? '★ Bỏ yêu thích' : '☆ Yêu thích', disabled: !['OWNER', 'EDITOR'].includes(d.your_role), onclick: () => saveMeta({ favorite: !C.data.favorite }) }),
    h('button', { class: 'btn small', text: 'Nhân bản', onclick: duplicatePrompt }),
    ['OWNER', 'EDITOR'].includes(d.your_role) ? h('button', { class: 'btn small', text: d.archived_at ? 'Khôi phục từ lưu trữ' : 'Lưu trữ', onclick: toggleArchive }) : null,
    h('span', { class: 'spacer' }), C.retryBtn, C.statusEl);
  const notices = [];
  if (d.your_role === 'VIEWER') notices.push(h('div', { class: 'note', text: 'Bạn chỉ có quyền xem prompt này.' }));
  if (d.archived_at) notices.push(h('div', { class: 'note', text: `Prompt đã lưu trữ lúc ${fmtTime(d.archived_at)}; khôi phục từ lưu trữ để sửa.` }));
  fill(S.center, C.title, tools, notices, C.banners, C.ta, C.previewEl, C.counts, tabsBar(), C.tabsBody);
  setStatus('saved');
  updateCounts();
  renderTab();
}

function onInput() {
  C.text = C.ta.value;
  updateCounts();
  if (C.conflict) return; // đang giải quyết xung đột: không tự lưu
  setStatus('editing');
  lsSet(C.id, { text: C.text, baseRev: C.baseRev, ts: new Date().toISOString() });
  clearTimeout(C.saveTimer);
  C.saveTimer = setTimeout(() => queueSave(), AUTOSAVE_MS);
}

function updateCounts() {
  const t = C.text;
  const vars = [...new Set([...t.matchAll(VAR_RE)].map((m) => m[1]))];
  C.counts.textContent = `${t.length.toLocaleString('vi-VN')} ký tự · ${countWords(t).toLocaleString('vi-VN')} từ · ~${Math.ceil(t.length / 4).toLocaleString('vi-VN')} token (ước tính thô ký tự/4, chưa dùng tokenizer) · ${vars.length} biến`;
}

function setStatus(s, msg = '') {
  if (!C) return;
  C.status = s;
  const label = { saved: 'Đã lưu', editing: 'Đang sửa…', saving: 'Đang lưu…', error: 'Lỗi lưu', conflict: 'Xung đột' }[s];
  C.statusEl.className = `save-state s-${s === 'conflict' ? 'error' : s}`;
  C.statusEl.textContent = msg ? `${label}: ${msg}` : label;
  C.statusEl.title = s === 'error' ? 'Bản nháp vẫn được giữ trên trình duyệt này. Bấm "Thử lưu lại".' : '';
  C.retryBtn.hidden = s !== 'error';
}

function queueSave() {
  if (!C) return Promise.resolve();
  clearTimeout(C.saveTimer);
  const c = C;
  c.chain = c.chain.then(() => doSave(c), () => doSave(c));
  return c.chain;
}

async function doSave(c) {
  if (c !== C || c.conflict || !canWrite()) return;
  const text = c.text;
  if (text === c.baseDraft) { setStatus('saved'); lsDel(c.id); return; }
  setStatus('saving');
  try {
    const d = await put(`/prompts/${c.id}/draft`, { content: text }, { ifMatch: c.baseRev });
    if (c !== C) return;
    c.baseRev = d.revision; c.baseDraft = text; c.data = d;
    if (c.text === text) { setStatus('saved'); lsDel(c.id); } else { setStatus('editing'); c.saveTimer = setTimeout(() => queueSave(), AUTOSAVE_MS); }
    renderMeta();
  } catch (e) {
    if (c !== C) return;
    if (e.code === 'REVISION_CONFLICT' && e.details) {
      const srv = e.details;
      if (srv.draft_content === c.baseDraft) { c.baseRev = srv.current_revision; return doSave(c); } // chỉ thông tin khác đổi
      if (srv.draft_content === text) { c.baseRev = srv.current_revision; c.baseDraft = text; setStatus('saved'); lsDel(c.id); return; }
      showConflict(srv, text);
    } else {
      setStatus('error', e.message);
    }
  }
}

// ------------------------------------------------------------------ xung đột & phục hồi
function showConflict(srv, mine) {
  C.conflict = srv; setStatus('conflict');
  lsSet(C.id, { text: mine, baseRev: C.baseRev, ts: new Date().toISOString() });
  const rows = lineDiff(srv.draft_content, mine);
  const box = h('div', { class: 'banner bad', role: 'alert' },
    h('strong', { text: 'Xung đột: bản nháp đã được sửa ở nơi khác' }),
    h('span', { class: 'small', text: `Máy chủ: revision ${srv.current_revision}, sửa lúc ${fmtTime(srv.draft_updated_at)}${srv.draft_updated_by ? ` bởi ${srv.draft_updated_by}` : ''}. Bản của bạn vẫn nằm trong trình soạn và trên trình duyệt — chưa mất gì.` }),
    h('span', { class: 'small muted', text: 'Khác biệt (− bản trên máy chủ, + bản của bạn):' }),
    rows ? diffView(rows) : h('p', { class: 'small', text: 'Văn bản quá dài để so sánh trực tiếp.' }),
    h('div', { class: 'row' },
      h('button', { class: 'btn danger small', text: 'Giữ bản của tôi (ghi đè có chủ đích)', onclick: () => {
        C.baseRev = srv.current_revision; C.baseDraft = srv.draft_content; C.conflict = null; box.remove(); queueSave();
      } }),
      h('button', { class: 'btn small', text: 'Dùng bản trên máy chủ', onclick: async () => {
        if (!(await confirmDlg('Dùng bản trên máy chủ', 'Bản của bạn sẽ bị thay trong trình soạn. Sao chép trước nếu cần giữ.', 'Dùng bản máy chủ', 'danger'))) return;
        C.baseRev = srv.current_revision; C.baseDraft = srv.draft_content; C.text = srv.draft_content; C.ta.value = C.text; C.conflict = null;
        lsDel(C.id); box.remove(); setStatus('saved'); updateCounts();
      } }),
      h('button', { class: 'btn small', text: 'Sao chép bản của tôi', onclick: () => copyText(mine) })));
  C.banners.prepend(box);
}

function showRecovery(cached) {
  const box = h('div', { class: 'banner', role: 'alert' },
    h('strong', { text: 'Có bản nháp chưa lưu lên máy chủ trên trình duyệt này' }),
    h('span', { class: 'small', text: `Lưu cục bộ lúc ${fmtTime(cached.ts)} · ${cached.text.length.toLocaleString('vi-VN')} ký tự. Bản trên máy chủ: revision ${C.data.revision}.` }),
    h('div', { class: 'row' },
      h('button', { class: 'btn primary small', text: 'Khôi phục bản nháp này', onclick: () => {
        box.remove();
        C.text = cached.text; C.ta.value = cached.text; updateCounts();
        if (cached.baseRev === C.baseRev) { setStatus('editing'); queueSave(); }
        else showConflict({ current_revision: C.data.revision, draft_content: C.data.draft_content, draft_updated_at: C.data.draft_updated_at, draft_updated_by: C.data.draft_updated_by }, cached.text);
      } }),
      h('button', { class: 'btn small', text: 'Xem khác biệt', onclick: () => {
        const rows = lineDiff(C.data.draft_content, cached.text);
        dialog('Bản máy chủ (−) ↔ bản trên trình duyệt (+)', rows ? diffView(rows) : h('p', { text: 'Quá dài để so sánh.' }));
      } }),
      h('button', { class: 'btn small danger', text: 'Bỏ bản nháp cục bộ', onclick: () => { lsDel(C.id); box.remove(); } })));
  C.banners.prepend(box);
}

function copyText(text) {
  navigator.clipboard.writeText(text).then(() => toast('Đã sao chép')).catch(() => toast('Trình duyệt chặn sao chép; hãy chọn và sao chép thủ công', 'bad'));
}

// ------------------------------------------------------------------ thao tác prompt
function chainOp(fn) { const c = C; c.chain = c.chain.then(fn, fn); return c.chain; }

function saveMeta(fields) {
  return chainOp(async () => {
    const c = C; if (!c) return;
    try {
      const d = await patch(`/prompts/${c.id}`, fields, { ifMatch: c.baseRev });
      if (c !== C) return;
      c.baseRev = d.revision; c.data = d;
      toast('Đã lưu thông tin'); renderMeta(); loadLibrary(); loadFacets();
      if ('favorite' in fields) renderEditorKeepText();
    } catch (e) {
      if (e.code === 'REVISION_CONFLICT' && e.details && e.details.draft_content === c.baseDraft) {
        c.baseRev = e.details.current_revision;
        toast('Prompt vừa được cập nhật ở nơi khác; hãy kiểm tra lại thông tin rồi lưu lần nữa', 'bad');
        await refreshData();
      } else toastErr(e, 'Lưu thông tin');
    }
  });
}

async function refreshData() {
  const c = C; const d = await get(`/prompts/${c.id}`);
  if (c !== C) return;
  c.data = d;
  if (d.draft_content === c.baseDraft) c.baseRev = d.revision;
  renderMeta(); renderTab();
}

function renderEditorKeepText() {
  const text = C.text; const status = C.status; renderEditor(); C.text = text; C.ta.value = text; updateCounts(); setStatus(status);
}

function commitDialog() {
  const cl = h('input', { maxlength: 500, placeholder: 'VD: Thêm ràng buộc định dạng JSON' });
  dialog('Lưu phiên bản mới', h('div', { class: 'stack' },
    h('p', { class: 'small muted', text: 'Tạo snapshot bất biến từ bản nháp hiện tại. Phiên bản cũ, phiên bản APPROVED và con trỏ production không đổi.' }),
    field('Ghi chú thay đổi', cl)), [{ label: 'Lưu phiên bản', kind: 'primary', onClick: async (close) => {
    await queueSave();
    if (C.status !== 'saved') throw new Error('Bản nháp chưa lưu được lên máy chủ nên chưa tạo phiên bản');
    await chainOp(async () => {
      const d = await post(`/prompts/${C.id}/versions`, { changelog: cl.value }, { ifMatch: C.baseRev });
      C.baseRev = d.revision; C.data = d; C.baseDraft = d.draft_content;
    });
    if (C.data.head_version && C.status === 'saved') { close(); toast(`Đã lưu v${C.data.head_version.version_no}`); renderMeta(); C.tab = 'history'; renderTabsBar(); renderTab(); loadLibrary(); }
  } }]);
}

async function duplicatePrompt() {
  try {
    await queueSave();
    const d = await post(`/prompts/${C.id}/duplicate`, {});
    toast('Đã nhân bản'); location.hash = `#/prompts/${d.id}`;
  } catch (e) { toastErr(e, 'Nhân bản'); }
}

async function toggleArchive() {
  const arch = !C.data.archived_at;
  if (arch && !(await confirmDlg('Lưu trữ prompt', 'Prompt sẽ ẩn khỏi thư viện (không xóa dữ liệu, khôi phục được).', 'Lưu trữ'))) return;
  await queueSave();
  await chainOp(async () => {
    try {
      const d = await post(`/prompts/${C.id}/${arch ? 'archive' : 'unarchive'}`, {}, { ifMatch: C.baseRev });
      C.data = d; C.baseRev = d.revision; renderEditor(); renderMeta(); loadLibrary();
      toast(arch ? 'Đã lưu trữ' : 'Đã khôi phục');
    } catch (e) { toastErr(e); }
  });
}

// ------------------------------------------------------------------ cột phải: thông tin & phân tích
function renderMeta() {
  if (!C || !S) return;
  const d = C.data; const w = canWrite();
  const desc = h('textarea', { rows: 3, maxlength: 2000, readonly: !w }); desc.value = d.description;
  const cat = h('input', { value: d.category, maxlength: 80, readonly: !w, list: 'ci-cats' });
  const ai = h('input', { value: d.target_ai, maxlength: 80, readonly: !w, placeholder: 'VD: Claude, ChatGPT, Grok', list: 'ci-ais' });
  const tags = h('input', { value: d.tags.join(', '), readonly: !w, placeholder: 'phân cách bằng dấu phẩy' });
  const proj = select(S.projects.filter((p) => ['OWNER', 'EDITOR'].includes(p.your_role) || p.id === d.project.id).map((p) => [p.id, p.name]), d.project.id, { disabled: !w });
  const save = w ? h('button', { class: 'btn small primary', text: 'Lưu thông tin', onclick: () => {
    const f = { description: desc.value, category: cat.value, target_ai: ai.value, tags: tags.value.split(',').map((t) => t.trim()).filter(Boolean) };
    if (proj.value && proj.value !== d.project.id) f.project_id = proj.value;
    saveMeta(f);
  } }) : null;
  C.analysisEl = h('div', { class: 'small muted', text: 'Đang phân tích…' });
  fill(S.meta, 
    h('div', { class: 'card stack' }, h('h2', { text: 'Thông tin' }),
      h('div', { class: 'small muted', text: `${d.id} · revision ${d.revision} · vai trò của bạn: ${d.your_role}` }),
      field('Dự án', proj), field('Mô tả / mục tiêu', desc), field('Danh mục', cat), field('AI đích', ai), field('Tag', tags), save,
      h('datalist', { id: 'ci-cats' }, S.facets.categories.map((x) => h('option', { value: x }))),
      h('datalist', { id: 'ci-ais' }, ['Claude', 'ChatGPT', 'Grok', 'Gemini', ...S.facets.target_ais].map((x) => h('option', { value: x })))),
    h('div', { class: 'card stack small' }, h('h2', { text: 'Phiên bản' }),
      h('div', {}, 'Mới nhất: ', d.head_version ? h('strong', { text: `v${d.head_version.version_no}` }) : 'chưa có', d.head_version ? h('span', {}, ' ', badge(d.head_version.review_status)) : null),
      h('div', {}, 'Production: ', d.production_version ? h('strong', { class: 'b-prod', text: `v${d.production_version.version_no}` }) : h('span', { class: 'muted', text: 'chưa đặt' })),
      h('div', { class: d.draft_dirty ? '' : 'muted', text: d.draft_dirty ? 'Bản nháp khác phiên bản mới nhất (chưa lưu phiên bản).' : 'Bản nháp trùng phiên bản mới nhất.' }),
      h('div', { class: 'muted', text: `${d.version_count} phiên bản · cập nhật ${fmtTime(d.updated_at)}` })),
    h('div', { class: 'card stack' }, h('h2', { text: 'Phân tích tĩnh' }), C.analysisEl));
  loadAnalysis();
}

async function loadAnalysis() {
  const c = C; const el = C.analysisEl;
  try {
    const a = await get(`/prompts/${c.id}/analysis?version=draft`);
    if (c !== C || !el.isConnected) return;
    fill(el, 
      h('div', {}, `Biến: `, a.variables.length ? a.variables.map((v) => h('span', { class: 'var', text: v })).flatMap((x) => [x, ' ']) : 'không có'),
      a.blocks.length ? h('div', {}, `Khối: ${a.blocks.join(', ')}`) : null,
      a.findings.length ? a.findings.map((f) => h('div', { class: 'finding' }, h('span', { class: `sev sev-${f.severity}`, text: f.severity.toUpperCase() }), f.line ? `dòng ${f.line}: ` : '', f.message))
        : h('div', { class: 'finding', text: 'Không phát hiện vấn đề theo quy tắc.' }),
      h('p', { class: 'small muted', text: `${a.disclaimer} (phân tích trên bản nháp đã lưu lên máy chủ)` }));
  } catch (e) { if (el.isConnected) el.textContent = `Không phân tích được: ${e.message}`; }
}

// ------------------------------------------------------------------ tab dưới
const TABS = [['history', 'Lịch sử & so sánh'], ['vars', 'Biến & xuất'], ['sources', 'Nguồn & ghi chú'], ['agents', 'Cấp cho Agent']];
function tabsBar() { C.tabsBar = h('div', { class: 'tabs', role: 'tablist' }); renderTabsBar(); return C.tabsBar; }
function renderTabsBar() {
  fill(C.tabsBar, TABS.filter(([k]) => k !== 'agents' || isOwner()).map(([k, l]) => h('button', { role: 'tab', 'aria-selected': String(C.tab === k), text: l, onclick: () => { C.tab = k; renderTabsBar(); renderTab(); } })));
}
function renderTab() {
  const box = clear(C.tabsBody);
  ({ history: tabHistory, vars: tabVars, sources: tabSources, agents: tabAgents })[C.tab](box).catch((e) => add(box, h('p', { class: 'note bad', role: 'alert', text: e.message })));
}

async function tabHistory(box) {
  const c = C; const { items } = await get(`/prompts/${c.id}/versions`);
  if (c !== C) return;
  const refOpts = [['draft', 'Bản nháp'], ...items.map((v) => [v.id, `v${v.version_no} · ${fmtTime(v.created_at)}`])];
  const fromSel = select(refOpts, items[1] ? items[1].id : (items[0] ? items[0].id : 'draft'), { 'aria-label': 'So sánh từ' });
  const toSel = select(refOpts, items[0] ? (c.data.draft_dirty ? 'draft' : items[0].id) : 'draft', { 'aria-label': 'So sánh tới' });
  const diffBox = h('div');
  const runDiff = async () => {
    try {
      await queueSave();
      const r = await get(`/prompts/${c.id}/diff${qs({ from: fromSel.value, to: toSel.value })}`);
      fill(diffBox, h('p', { class: 'small muted', text: `${r.from.label} → ${r.to.label}: +${r.added} / −${r.removed} dòng${r.identical ? ' (giống hệt)' : ''}` }), diffView(r.rows));
    } catch (e) { fill(diffBox, h('p', { class: 'note bad', text: e.message })); }
  };
  add(box, h('div', { class: 'row' }, fromSel, h('span', { text: '→' }), toSel, h('button', { class: 'btn small', text: 'So sánh', onclick: runDiff })), diffBox);
  if (!items.length) { add(box, h('p', { class: 'empty', text: 'Chưa có phiên bản. Bấm "Lưu phiên bản" để tạo snapshot đầu tiên.' })); return; }
  const prodId = c.data.production_version && c.data.production_version.id;
  const headId = c.data.head_version && c.data.head_version.id;
  for (const v of items) {
    const acts = [h('button', { class: 'btn small', text: 'Xem', onclick: async () => {
      try { const full = await get(`/prompts/${c.id}/versions/${v.id}`); const ta = h('textarea', { class: 'content', rows: 16, readonly: true }); ta.value = full.content;
        dialog(`v${v.version_no} · checksum ${v.checksum.slice(0, 12)}…`, h('div', { class: 'stack' }, ta, h('button', { class: 'btn small', text: 'Sao chép', onclick: () => copyText(full.content) })));
      } catch (e) { toastErr(e); }
    } })];
    if (canWrite() && v.id !== headId) acts.push(h('button', { class: 'btn small', text: 'Khôi phục', onclick: async () => {
      if (!(await confirmDlg(`Khôi phục v${v.version_no}`, `Tạo phiên bản MỚI có nội dung của v${v.version_no} và đặt làm bản nháp. Nếu bản nháp hiện tại chưa lưu phiên bản, hệ thống tự lưu nó thành một phiên bản trước. Lịch sử giữ nguyên; production và Agent không đổi.`, 'Khôi phục'))) return;
      await queueSave();
      await chainOp(async () => {
        try { const d = await post(`/prompts/${c.id}/versions/${v.id}/restore`, {}, { ifMatch: C.baseRev }); C.data = d; C.baseRev = d.revision; C.baseDraft = d.draft_content; C.text = d.draft_content; C.ta.value = C.text; lsDel(C.id); updateCounts(); setStatus('saved'); renderMeta(); renderTab(); toast(`Đã khôi phục thành v${d.head_version.version_no}`); }
        catch (e) { toastErr(e, 'Khôi phục'); }
      });
    } }));
    const review = async (status) => {
      try { await post(`/prompts/${c.id}/versions/${v.id}/review`, { status }); toast(`v${v.version_no}: ${status}`); await refreshData(); } catch (e) { toastErr(e); }
    };
    if (['OWNER', 'EDITOR'].includes(c.data.your_role) && v.review_status === 'DRAFT') acts.push(h('button', { class: 'btn small', text: 'Đưa vào kiểm thử', onclick: () => review('TESTING') }));
    if (isOwner() && v.review_status !== 'APPROVED') acts.push(h('button', { class: 'btn small', text: 'Duyệt', onclick: () => review('APPROVED') }));
    if (isOwner() && !['REJECTED'].includes(v.review_status) && v.id !== prodId) acts.push(h('button', { class: 'btn small', text: 'Từ chối', onclick: () => review('REJECTED') }));
    if (isOwner() && v.review_status === 'APPROVED' && v.id !== prodId) acts.push(h('button', { class: 'btn small primary', text: 'Đặt production', onclick: async () => {
      if (!(await confirmDlg('Đặt production', `Con trỏ production của prompt sẽ trỏ tới v${v.version_no}. Assignment của Agent KHÔNG tự đổi theo.`, 'Đặt production'))) return;
      try { await put(`/prompts/${c.id}/production`, { version_id: v.id, expected_version_id: prodId || null }); toast('Đã đặt production'); await refreshData(); } catch (e) { toastErr(e); }
    } }));
    add(box, h('div', { class: 'version-row' },
      h('div', { class: 'row' }, h('strong', { text: `v${v.version_no}` }), badge(v.review_status), v.id === prodId ? badge('prod', 'PRODUCTION') : null, v.id === headId ? badge('info', 'mới nhất') : null,
        h('span', { class: 'small muted', text: `${fmtTime(v.created_at)} · ${v.author_name || v.author_id || ''}` })),
      h('div', { class: 'small', text: `${v.changelog || '—'}${v.restored_from ? ` (khôi phục từ ${v.restored_from})` : ''}` }),
      h('div', { class: 'row' }, acts)));
  }
}

async function tabVars(box) {
  const c = C;
  const vars = [...new Set([...c.text.matchAll(VAR_RE)].map((m) => m[1]))];
  const src = select([['draft', 'Bản nháp'], ...(c.data.head_version ? [['head', `Phiên bản mới nhất (v${c.data.head_version.version_no})`]] : []),
    ...(c.data.production_version ? [[c.data.production_version.id, `Production (v${c.data.production_version.version_no})`]] : [])], 'draft', { 'aria-label': 'Nguồn nội dung' });
  const inputs = Object.fromEntries(vars.map((v) => [v, h('textarea', { rows: 1 })]));
  const out = h('div', { class: 'stack' });
  const run = h('button', { class: 'btn primary small', text: 'Dựng prompt', onclick: async () => {
    clear(out);
    try {
      await queueSave();
      const r = await post(`/prompts/${c.id}/render`, { version: src.value, variables: Object.fromEntries(Object.entries(inputs).map(([k, i]) => [k, i.value])) });
      const ta = h('textarea', { class: 'content', rows: 10, readonly: true }); ta.value = r.text;
      add(out, h('p', { class: 'small muted', text: `Nguồn: ${r.source}${r.unused_variables.length ? ` · biến thừa: ${r.unused_variables.join(', ')}` : ''}` }), ta,
        h('div', { class: 'row' }, h('button', { class: 'btn small', text: 'Sao chép', onclick: () => copyText(r.text) }),
          h('button', { class: 'btn small', text: 'Tải .txt', onclick: () => downloadText(`${c.data.title}.txt`, r.text) })));
    } catch (e) {
      const d = e.details || {};
      add(out, h('div', { class: 'note bad', role: 'alert' }, e.message,
        d.missing_variables && d.missing_variables.length ? h('div', { text: `Thiếu biến: ${d.missing_variables.join(', ')}` }) : null,
        (d.block_errors || []).map((b) => h('div', { text: b.message }))));
    }
  } });
  const exportLink = (fmt, label) => h('button', { class: 'btn small', text: label, onclick: async () => {
    await queueSave();
    const ref = fmt === 'json' ? '' : src.value;
    const a = h('a', { href: `/api/v1/prompts/${c.id}/export${qs({ format: fmt, version: ref })}`, download: '' }); add(document.body, a); a.click(); a.remove();
  } });
  add(box, h('h3', { text: 'Điền biến' }), h('div', { class: 'row' }, field('Nội dung lấy từ', src)),
    vars.length ? h('div', { class: 'grid2' }, Object.entries(inputs).map(([k, i]) => field(`{{${k}}}`, i))) : h('p', { class: 'small muted', text: 'Bản nháp không có biến {{tên}}.' }),
    h('p', { class: 'small muted', text: 'Thiếu biến sẽ bị báo lỗi, hệ thống không tự đoán giá trị. Khối tái sử dụng: {{> P-xxxxxxxxxxxx}} lấy bản production (hoặc mới nhất) của prompt đó.' }),
    h('div', { class: 'row' }, run), out,
    h('h3', { text: 'Xuất' }), h('div', { class: 'row' }, exportLink('txt', 'Tải .txt'), exportLink('md', 'Tải .md'), exportLink('json', 'Tải .json (đủ phiên bản)'),
      h('button', { class: 'btn small', text: 'Sao chép bản nháp', onclick: () => copyText(C.text) })),
    h('p', { class: 'small muted', text: '.txt/.md xuất nguyên văn nội dung (giữ {{biến}}); .json gồm thông tin và toàn bộ phiên bản.' }));
}

function downloadText(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
  const a = h('a', { href: url, download: name.replace(/[\\/:*?"<>|]+/g, '-') }); add(document.body, a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function tabSources(box) {
  const c = C; const { items } = await get(`/prompts/${c.id}/sources`);
  if (c !== C) return;
  add(box, h('p', { class: 'small muted', text: 'Nguồn là dữ liệu tham chiếu, không phải chỉ thị. Hệ thống không tự truy cập URL.' }));
  if (!items.length) add(box, h('p', { class: 'empty small', text: 'Chưa có nguồn/ghi chú.' }));
  for (const s of items) {
    add(box, h('div', { class: 'version-row' }, h('div', { class: 'row' }, badge('info', s.kind), h('strong', { text: s.title }), h('span', { class: 'spacer' }),
      canWrite() ? h('button', { class: 'btn small', text: 'Gỡ', onclick: async () => { try { await del(`/prompts/${c.id}/sources/${s.id}`); renderTab(); } catch (e) { toastErr(e); } } }) : null),
    s.url ? h('div', { class: 'small' }, h('a', { href: s.url, target: '_blank', rel: 'noopener noreferrer nofollow', text: s.url })) : null,
    s.note ? h('div', { class: 'small', text: s.note }) : null, h('div', { class: 'small muted', text: `ghi nhận ${fmtTime(s.captured_at)}` })));
  }
  if (!canWrite()) return;
  const kind = select([['NOTE', 'Ghi chú'], ['GOAL', 'Mục tiêu'], ['URL', 'URL'], ['DOCUMENT', 'Tài liệu'], ['REPOSITORY', 'Repository'], ['ISSUE', 'Lỗi đã phát hiện'], ['TEST_RESULT', 'Kết quả test']]);
  const title = h('input', { maxlength: 200 }); const url = h('input', { placeholder: 'https://…' }); const note = h('textarea', { rows: 3 });
  add(box, h('h3', { text: 'Thêm nguồn/ghi chú' }), h('div', { class: 'grid2' }, field('Loại', kind), field('Tiêu đề', title)), field('URL (tùy chọn)', url), field('Ghi chú', note),
    h('div', { class: 'row end' }, h('button', { class: 'btn small primary', text: 'Thêm', onclick: async () => {
      try { await post(`/prompts/${c.id}/sources`, { kind: kind.value, title: title.value, url: url.value, note: note.value }); toast('Đã thêm'); renderTab(); } catch (e) { toastErr(e); }
    } })));
}

async function tabAgents(box) {
  const c = C;
  const [asg, vers] = await Promise.all([get(`/assignments${qs({ prompt_id: c.id })}`), get(`/prompts/${c.id}/versions`)]);
  if (c !== C) return;
  add(box, h('p', { class: 'small muted', text: 'Agent chỉ đọc đúng phiên bản được cấp (agent_id + prompt + version cụ thể). Phiên bản mới không tự đổi assignment.' }));
  if (!asg.items.length) add(box, h('p', { class: 'empty small', text: 'Chưa cấp cho Agent nào.' }));
  for (const a of asg.items) {
    add(box, h('div', { class: 'row version-row' }, h('strong', { text: a.agent_id }), h('span', { text: `v${a.version_no}` }), h('span', { class: 'small muted', text: `cấp ${fmtTime(a.assigned_at)}` }), h('span', { class: 'spacer' }),
      h('button', { class: 'btn small danger', text: 'Thu hồi', onclick: async () => { try { await post(`/assignments/${a.id}/revoke`); toast('Đã thu hồi'); renderTab(); } catch (e) { toastErr(e); } } })));
  }
  const approved = vers.items.filter((v) => v.review_status === 'APPROVED');
  if (!approved.length) { add(box, h('p', { class: 'note small', text: 'Chưa có phiên bản APPROVED. Chỉ cấp cho Agent phiên bản đã duyệt.' })); return; }
  let agents;
  try { agents = await get('/agents'); } catch (e) { add(box, h('p', { class: 'note bad', text: e.message })); return; }
  if (!agents.items.length) { add(box, h('p', { class: 'note small', text: 'Chưa có danh tính Agent. Vào trang CROWN AGENT để đọc sổ đăng ký.' })); return; }
  const ag = select(agents.items.map((a) => [a.agent_id, `${a.agent_id} — ${a.display_name}`]));
  const vs = select(approved.map((v) => [v.id, `v${v.version_no}`]));
  add(box, h('div', { class: 'row' }, ag, vs, h('button', { class: 'btn small primary', text: 'Cấp', onclick: async () => {
    try { await post('/assignments', { agent_id: ag.value, prompt_id: c.id, version_id: vs.value }); toast('Đã cấp'); renderTab(); } catch (e) { toastErr(e); }
  } })));
}

// ------------------------------------------------------------------ tạo mới & nhập
function writableProjects() { return S.projects.filter((p) => ['OWNER', 'EDITOR'].includes(p.your_role) && !p.archived_at); }

function newPromptDialog() {
  const projects = writableProjects();
  if (!projects.length) { toast('Bạn chưa có dự án nào được quyền ghi', 'bad'); return; }
  const title = h('input', { maxlength: 200, required: true }); const proj = select(projects.map((p) => [p.id, p.name]), S.fProject.value || projects[0].id);
  const cat = h('input', { maxlength: 80 }); const ai = h('input', { maxlength: 80, placeholder: 'VD: Claude' }); const content = h('textarea', { rows: 8, class: 'content' });
  dialog('Prompt mới', h('div', { class: 'stack' }, field('Tiêu đề', title), h('div', { class: 'grid2' }, field('Dự án', proj), field('Danh mục', cat), field('AI đích', ai)),
    field('Nội dung (có thể để trống)', content)), [{ label: 'Tạo', kind: 'primary', onClick: async (close) => {
    const d = await post('/prompts', { project_id: proj.value, title: title.value, category: cat.value, target_ai: ai.value, content: content.value });
    close(); toast('Đã tạo prompt'); loadFacets(); location.hash = `#/prompts/${d.id}`;
  } }]);
}

function importDialog() {
  const projects = writableProjects();
  if (!projects.length) { toast('Bạn chưa có dự án nào được quyền ghi', 'bad'); return; }
  const proj = select(projects.map((p) => [p.id, p.name]), S.fProject.value || projects[0].id);
  const files = h('input', { type: 'file', multiple: true, accept: '.txt,.md,.markdown,text/plain,text/markdown' });
  const out = h('div', { class: 'stack' });
  let preview = null; let payload = [];
  const { close } = dialog('Nhập prompt từ tệp TXT/Markdown', h('div', { class: 'stack' },
    h('p', { class: 'small muted', text: 'Tối đa 50 tệp, mỗi tệp ≤ 1 MB, mã hóa UTF-8 (hoặc UTF-16 có BOM). Xem trước rồi mới ghi; mục trùng không bị ghi đè âm thầm.' }),
    field('Dự án', proj), files, out), [
    { label: 'Xem trước', onClick: async () => {
      clear(out);
      if (!files.files.length) throw new Error('Chọn ít nhất một tệp');
      payload = await Promise.all([...files.files].map(async (f) => ({ filename: f.name, content_b64: await readFileB64(f) })));
      preview = await post('/import/preview', { project_id: proj.value, files: payload });
      for (const it of preview.items) {
        if (!it.ok) { add(out, h('div', { class: 'note bad small', text: `✗ ${it.filename || `Tệp #${it.index + 1}`}: ${it.error}` })); continue; }
        const actOpts = [['create', 'Tạo prompt mới'], ['skip', 'Bỏ qua']];
        if (it.title_conflict) actOpts.splice(1, 0, ['new_version', `Thêm làm phiên bản mới của "${it.title_conflict.title}"`]);
        it.actSel = select(actOpts, it.suggested_action); it.titleIn = h('input', { value: it.title, maxlength: 200 });
        add(out, h('div', { class: 'card stack small' }, h('strong', { text: `✓ ${it.filename} · ${it.chars.toLocaleString('vi-VN')} ký tự · ${it.encoding}` }),
          it.duplicate_of ? h('div', { class: 'note', text: `Trùng nội dung với "${it.duplicate_of.title}"${it.duplicate_of.version_no ? ` v${it.duplicate_of.version_no}` : ''} — đề xuất bỏ qua.` }) : null,
          it.title_conflict && !it.duplicate_of ? h('div', { class: 'note', text: `Đã có prompt cùng tiêu đề "${it.title_conflict.title}".` }) : null,
          field('Tiêu đề', it.titleIn), field('Hành động', it.actSel), h('pre', { class: 'small', text: it.preview })));
      }
    } },
    { label: 'Nhập', kind: 'primary', onClick: async () => {
      if (!preview) throw new Error('Bấm "Xem trước" trước');
      const items = preview.items.filter((it) => it.ok).map((it) => ({ ...payload[it.index], title: it.titleIn.value, action: it.actSel.value,
        target_prompt_id: it.title_conflict ? it.title_conflict.id : undefined, target_revision: it.title_conflict ? it.title_conflict.revision : undefined }));
      if (!items.length) throw new Error('Không có tệp hợp lệ để nhập');
      const r = await post('/import/commit', { project_id: proj.value, items });
      const n = (a) => r.results.filter((x) => x.action === a).length;
      toast(`Đã nhập: ${n('create')} mới, ${n('new_version')} phiên bản mới, ${n('skip')} bỏ qua`);
      close(); loadLibrary(); loadFacets();
    } }]);
}
