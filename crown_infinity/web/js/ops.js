// Màn hình "Điều hành" — bộ nhớ quyết định, bản đồ mã (Graphify), Rà soát bản build, lịch chạy.
// Web và desktop dùng chung trang này và cùng API /api/v1.
import { get, patch, post, put, qs } from './api.js';
import { badge, confirmDlg, dialog, field, fill, fmtTime, h, select, toast, toastErr } from './ui.js';

const PKEY = 'ci.ops.project';
let S = null;

const owner = () => S && S.session.user.role === 'OWNER';
const confBadge = (c) => badge(c === 'EXTRACTED' ? 'ok' : c === 'INFERRED' ? 'warn' : 'info', c);
const labelBadge = (d) => (d.kind === 'DECISION'
  ? badge(d.status === 'ACTIVE' ? 'ok' : 'bad', d.status === 'ACTIVE' ? 'QUYẾT ĐỊNH ĐÃ XÁC NHẬN' : 'ĐÃ THU HỒI')
  : badge(d.status === 'REJECTED' ? 'bad' : 'warn', d.status === 'REJECTED' ? 'ĐỀ XUẤT ĐÃ BÁC' : 'ĐỀ XUẤT AI/AGENT — CHƯA XÁC NHẬN'));

export async function pageOps(root, session) {
  const { items: projects } = await get('/projects');
  S = { session, root, projects };
  if (!projects.length) { fill(root, h('p', { class: 'empty', text: 'Chưa có dự án nào bạn được truy cập.' })); return; }
  let pid = '';
  try { pid = localStorage.getItem(PKEY) || ''; } catch { /* bỏ qua */ }
  if (!projects.some((p) => p.id === pid)) pid = projects[0].id;
  S.pid = pid;
  const psel = select(projects.map((p) => [p.id, p.name]), pid, { 'aria-label': 'Dự án' });
  psel.addEventListener('change', () => { S.pid = psel.value; try { localStorage.setItem(PKEY, psel.value); } catch { /* bỏ qua */ } renderAll(); });
  S.env = h('div', { class: 'note small' });
  S.dec = h('div', { class: 'card stack', id: 'ops-decisions' });
  S.map = h('div', { class: 'card stack', id: 'ops-graph' });
  S.rev = h('div', { class: 'card stack', id: 'ops-review' });
  S.sch = h('div', { class: 'card stack', id: 'ops-schedule' });
  fill(root, h('div', { class: 'stack' },
    h('div', { class: 'row' }, h('div', {}, h('h1', { text: 'Điều hành' }),
      h('p', { class: 'muted small', text: 'Bộ não điều hành của CROWN AGENT: quyết định của Chủ tịch, bản đồ mã, rà soát bản build. Giai đoạn này chỉ ĐỌC và ĐỀ XUẤT — không merge, không deploy, không tăng ngân sách.' })),
    h('span', { class: 'spacer' }), field('Dự án', psel)),
    S.env, S.dec, h('div', { class: 'cols' }, S.rev, S.sch), S.map));
  await renderAll();
}

async function renderAll() {
  await Promise.all([renderStatus(), renderDecisions(), renderReview()]);
}

// ------------------------------------------------------------------ trạng thái & môi trường
async function renderStatus() {
  let st;
  try { st = await get(`/ops/status${qs({ project_id: S.pid })}`); } catch (e) { fill(S.env, `Không đọc được trạng thái: ${e.message}`); return; }
  S.status = st;
  const c = st.commit;
  fill(S.env,
    h('strong', { text: 'Môi trường: ' }),
    c.available ? `commit ${c.sha.slice(0, 12)} (${c.branch})${c.dirty_count ? ` · ${c.dirty_count} tệp chưa commit` : ''}` : `không đọc được git — ${c.reason}`,
    ` · Graphify: ${st.graphify.available ? `có (${st.graphify.path})` : 'chưa cài trên máy chủ'}`,
    ' · Đã chạy thử: Linux (container cloud) + Chromium. Windows: chưa chạy thử.');
  renderMap();
  renderSchedule();
}

// ------------------------------------------------------------------ bộ nhớ quyết định
async function renderDecisions(filter = S.decFilter || 'ACTIVE') {
  S.decFilter = filter;
  const tabs = h('div', { class: 'tabs', role: 'tablist' }, [['ACTIVE', 'Đang hiệu lực'], ['PROPOSED', 'Đề xuất AI/Agent'], ['REVOKED', 'Đã thu hồi'], ['', 'Tất cả']]
    .map(([k, l]) => h('button', { role: 'tab', 'aria-selected': String(filter === k), text: l, onclick: () => renderDecisions(k) })));
  const list = h('div', { class: 'stack' }, h('p', { class: 'muted small', text: 'Đang tải…' }));
  const addBox = owner() ? addDecisionForm() : h('p', { class: 'small muted', text: 'Chỉ OWNER được thêm, sửa, thu hồi quyết định.' });
  fill(S.dec, h('div', { class: 'row' }, h('h2', { text: 'Bộ nhớ quyết định' }), h('span', { class: 'spacer' }),
    h('span', { class: 'small muted', text: 'Quyết định do Chủ tịch xác nhận tách biệt với đề xuất do AI/Agent viết.' })), tabs, list, addBox);
  try {
    const { items } = await get(`/decisions${qs({ project_id: S.pid, status: filter })}`);
    fill(list, items.length ? items.map(decisionCard) : h('p', { class: 'empty small', text: 'Chưa có mục nào.' }));
  } catch (e) { fill(list, h('p', { class: 'note bad', text: e.message })); }
}

function decisionCard(d) {
  const acts = [];
  if (owner()) {
    acts.push(h('button', { class: 'btn small', text: 'Lịch sử', onclick: () => showHistory(d) }));
    if (d.status === 'ACTIVE' || d.status === 'PROPOSED') acts.push(h('button', { class: 'btn small', text: 'Sửa', onclick: () => editDecision(d) }));
    if (d.kind === 'DECISION' && d.status === 'ACTIVE') acts.push(h('button', { class: 'btn small danger', text: 'Thu hồi', onclick: () => revokeDecision(d) }));
    if (d.kind === 'PROPOSAL' && d.status === 'PROPOSED') {
      acts.push(h('button', { class: 'btn small primary', text: 'Xác nhận thành quyết định', onclick: () => act(d, { action: 'CONFIRM' }, 'Đã xác nhận') }));
      acts.push(h('button', { class: 'btn small', text: 'Bác', onclick: () => act(d, { action: 'REJECT', reason: 'OWNER bác' }, 'Đã bác') }));
    }
  }
  return h('div', { class: 'version-row', dataset: { decision: d.id } },
    h('div', { class: 'row' }, labelBadge(d), h('strong', { text: d.title }), h('span', { class: 'spacer' }), h('span', { class: 'small muted', text: `${d.id} · rev ${d.revision}` })),
    h('div', { text: d.content }),
    h('div', { class: 'small muted', text: d.kind === 'DECISION'
      ? `Xác nhận: ${d.confirmed_by_name || d.confirmed_by} lúc ${fmtTime(d.confirmed_at)} · quyết định lúc ${fmtTime(d.decided_at)} · nguồn: ${d.source}`
      : `Đề xuất bởi ${d.proposed_by}${d.ai_model ? ` (model ${d.ai_model})` : ''} lúc ${fmtTime(d.created_at)} · nguồn: ${d.source} · CHƯA phải quyết định` }),
    d.revoked_at ? h('div', { class: 'small', text: `Thu hồi/bác lúc ${fmtTime(d.revoked_at)}: ${d.revoke_reason}` }) : null,
    acts.length ? h('div', { class: 'row' }, acts) : null);
}

function addDecisionForm() {
  const t = h('input', { maxlength: 200, 'aria-label': 'Tiêu đề quyết định' });
  const c = h('textarea', { rows: 3, maxlength: 10000, 'aria-label': 'Nội dung quyết định' });
  const s = h('input', { maxlength: 1000, value: 'Chủ tịch xác nhận trên màn hình Điều hành', 'aria-label': 'Nguồn' });
  return h('details', {}, h('summary', { text: '+ Thêm quyết định (OWNER xác nhận)' }),
    h('div', { class: 'stack' }, field('Tiêu đề', t), field('Nội dung', c), field('Nguồn', s),
      h('div', { class: 'row end' }, h('button', { class: 'btn primary', text: 'Lưu quyết định', onclick: async () => {
        try { await post('/decisions', { project_id: S.pid, title: t.value, content: c.value, source: s.value }); toast('Đã lưu quyết định'); renderDecisions('ACTIVE'); } catch (e) { toastErr(e); }
      } }))));
}

async function act(d, body, msg) {
  try { await patch(`/decisions/${d.id}`, body, { ifMatch: d.revision }); toast(msg); renderDecisions(); } catch (e) { toastErr(e); }
}

function editDecision(d) {
  const t = h('input', { value: d.title, maxlength: 200 }); const c = h('textarea', { rows: 5 }); c.value = d.content;
  const s = h('input', { value: d.source, maxlength: 1000 });
  dialog(`Sửa ${d.id}`, h('div', { class: 'stack' }, h('p', { class: 'small muted', text: 'Mỗi lần sửa lưu một bản lịch sử; bản cũ không mất.' }), field('Tiêu đề', t), field('Nội dung', c), field('Nguồn', s)),
    [{ label: 'Lưu', kind: 'primary', onClick: async (close) => { await patch(`/decisions/${d.id}`, { action: 'EDIT', title: t.value, content: c.value, source: s.value }, { ifMatch: d.revision }); close(); toast('Đã sửa'); renderDecisions(); } }]);
}

function revokeDecision(d) {
  const r = h('input', { maxlength: 1000, placeholder: 'Lý do thu hồi' });
  dialog(`Thu hồi: ${d.title}`, h('div', { class: 'stack' }, h('p', { class: 'small', text: 'Quyết định thôi hiệu lực nhưng vẫn giữ trong lịch sử.' }), field('Lý do', r)),
    [{ label: 'Thu hồi', kind: 'danger', onClick: async (close) => { await patch(`/decisions/${d.id}`, { action: 'REVOKE', reason: r.value }, { ifMatch: d.revision }); close(); toast('Đã thu hồi'); renderDecisions(); } }]);
}

async function showHistory(d) {
  try {
    const { items } = await get(`/decisions/${d.id}/history`);
    dialog(`Lịch sử ${d.id}`, h('div', { class: 'stack' }, items.map((x) => h('div', { class: 'version-row' },
      h('div', { class: 'row' }, badge('info', x.action), h('strong', { text: `rev ${x.revision}` }), h('span', { class: 'small muted', text: `${fmtTime(x.ts)} · ${x.actor_type} ${x.actor_id}` })),
      h('div', { class: 'small', text: `${x.snapshot.title} — ${x.snapshot.content}` }), x.note ? h('div', { class: 'small muted', text: `Ghi chú: ${x.note}` }) : null))));
  } catch (e) { toastErr(e); }
}

// ------------------------------------------------------------------ bản đồ mã
function renderMap() {
  const st = S.status; const m = st.latest_map;
  const build = owner() ? h('button', { class: 'btn small', text: 'Dựng bản đồ ở commit hiện tại', disabled: !st.graphify.available,
    title: st.graphify.available ? '' : 'Máy chủ chưa có Graphify (gói graphifyy)', onclick: async (ev) => {
    const b = ev.currentTarget;
      b.disabled = true;
      try { const r = await post('/graph/maps/build', { project_id: S.pid }); toast(`Đã dựng ${r.id}: ${r.node_count} nút, ${r.edge_count} cạnh`); renderStatus(); }
      catch (e) { toastErr(e, 'Dựng bản đồ'); b.disabled = false; }
    } }) : null;
  const q = h('input', { placeholder: 'Tên hàm/lớp/tệp, VD: create_backup', 'aria-label': 'Tên nút cần tra' });
  const dir = select([['both', 'Hai chiều'], ['out', 'Nó gọi/dùng gì (đi ra)'], ['in', 'Ai gọi/dùng nó (đi vào)']], 'both', { 'aria-label': 'Hướng' });
  const out = h('div', { class: 'stack' });
  const run = async (name) => {
    if (!m) return;
    try {
      const r = await get(`/graph/maps/${m.id}/query${qs({ node: name || q.value, direction: dir.value })}`);
      if (!r.node) {
        fill(out, h('p', { class: 'small', text: r.note || 'Không có kết quả' }), r.candidates.map((c) => h('button', { class: 'btn small', text: `${c.label} · ${c.source_file}:${c.source_line ?? ''}`, onclick: () => { q.value = c.node_id; run(c.node_id); } })));
        return;
      }
      fill(out, h('div', { class: 'small' }, h('strong', { text: r.node.label }), ` · ${r.node.type} · ${m.scope_prefix}/${r.node.source_file}:${r.node.source_line ?? ''} `,
        r.node.source_file ? h('button', { class: 'btn small', text: 'Mở nguồn', onclick: () => openSource(m, r.node.source_file, r.node.source_line) }) : null),
      r.relations.length ? h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['Hướng', 'Quan hệ', 'Nút kia', 'Nhãn', 'Bằng chứng'].map((x) => h('th', { text: x })))),
        h('tbody', {}, r.relations.map((e) => h('tr', {}, h('td', { text: e.direction === 'out' ? '→' : '←' }), h('td', { text: e.relation }),
          h('td', { text: e.other.label }), h('td', {}, confBadge(e.confidence)),
          h('td', {}, e.evidence.source_file ? h('button', { class: 'btn small', text: `${e.evidence.source_file}:${e.evidence.source_line ?? ''}`, onclick: () => openSource(m, e.evidence.source_file, e.evidence.source_line) }) : '—')))))) : h('p', { class: 'small muted', text: 'Không có quan hệ.' }),
      h('p', { class: 'small muted', text: 'EXTRACTED = đọc trực tiếp từ mã nguồn · INFERRED = Graphify suy luận khi phân giải (cần đối chiếu nguồn).' }));
    } catch (e) { fill(out, h('p', { class: 'note bad', text: e.message })); }
  };
  q.addEventListener('keydown', (e) => { if (e.key === 'Enter') run(); });
  fill(S.map, h('div', { class: 'row' }, h('h2', { text: 'Bản đồ mã (Graphify)' }), h('span', { class: 'spacer' }), build),
    m ? h('p', { class: 'small', text: `${m.id} · Graphify ${m.tool_version} · commit nguồn ${m.source_commit.slice(0, 12)} · tạo lúc ${fmtTime(m.created_at)} · ${m.file_count} tệp (${m.scope_prefix}/) · ${m.node_count} nút · ${m.edge_count} cạnh (${m.extracted_count} EXTRACTED, ${m.inferred_count} INFERRED)` })
      : h('p', { class: 'empty small', text: st.graphify.available ? 'Chưa có bản đồ. OWNER bấm "Dựng bản đồ".' : 'Chưa có bản đồ và máy chủ chưa cài Graphify — xem docs/infinity/OPS_BRAIN.md.' }),
    m && st.commit.available && m.source_commit !== st.commit.sha ? h('p', { class: 'note small', text: 'Bản đồ được dựng ở commit khác commit hiện tại — quan hệ có thể đã cũ.' }) : null,
    m ? h('p', { class: 'small muted', text: m.notes }) : null,
    m ? h('div', { class: 'row' }, q, dir, h('button', { class: 'btn small primary', text: 'Tra quan hệ', onclick: () => run() })) : null, out);
}

async function openSource(m, path, line) {
  try {
    const s = await get(`/graph/maps/${m.id}/source${qs({ path, line })}`);
    const pre = h('div', { class: 'diff', role: 'region', 'aria-label': 'Mã nguồn' }, s.lines.map((l) => h('div', { class: l.n === s.line ? 'insert' : 'equal' },
      h('span', { class: 'ln', text: l.n }), h('span', { class: 'ln', text: '' }), h('span', { text: l.n === s.line ? '▶' : ' ' }), h('span', { text: l.text }))));
    dialog(`${s.repo_path}${s.line ? `:${s.line}` : ''}`, h('div', { class: 'stack' }, h('p', { class: 'small muted', text: `Nguồn: ${s.origin} · dòng ${s.start}–${s.end}/${s.total_lines} · băm khớp bản đồ` }), pre));
  } catch (e) { toastErr(e, 'Mở nguồn'); }
}

// ------------------------------------------------------------------ rà soát bản build
async function renderReview(showRun = null) {
  const out = h('div', { class: 'stack', id: 'review-result' });
  let providers = [];
  if (owner()) { try { providers = (await get('/ai')).providers; } catch { providers = []; } }
  const opts = [['', 'Không dùng AI (chỉ quy tắc)'], ...providers.map((p) => [p.id, `${p.name} · ${p.model || '?'}${p.enabled ? '' : ' (đang tắt)'}`])];
  const ana = select(opts, '', { 'aria-label': 'Model phân tích' }); const ver = select(opts, '', { 'aria-label': 'Model kiểm tra lại' });
  const btn = h('button', { class: 'btn primary', text: 'Rà soát bản build', disabled: !owner(), onclick: async (ev) => {
    const b = ev.currentTarget;
    b.disabled = true; fill(out, h('p', { class: 'muted', text: 'Agent Vua đang rà soát…' }));
    try { const r = await post('/ops/reviews', { project_id: S.pid, analysis_provider_id: ana.value || null, verify_provider_id: ver.value || null }); showReport(out, r); loadRuns(); renderStatus(); }
    catch (e) { fill(out, h('p', { class: 'note bad', role: 'alert', text: `Rà soát thất bại: ${e.message}` })); }
    finally { b.disabled = false; }
  } });
  const runs = h('div', { class: 'stack small' });
  S.loadRuns = loadRuns;
  async function loadRuns() {
    try {
      const { items } = await get(`/ops/reviews${qs({ project_id: S.pid })}`);
      fill(runs, items.length ? items.map((r) => h('div', { class: 'row' }, badge(r.status), h('button', { class: 'btn small', text: r.id, onclick: async () => showReport(out, await get(`/ops/reviews/${r.id}`)) }),
        h('span', { class: 'muted', text: `${r.trigger} · commit ${(r.commit_sha || '?').slice(0, 10)} · ${fmtTime(r.created_at)} · gọi AI: ${r.ai_calls}${r.duplicate_of ? ` · trùng ${r.duplicate_of}` : ''}` }))) : h('p', { class: 'muted', text: 'Chưa có lượt rà soát.' }));
    } catch (e) { fill(runs, e.message); }
  }
  fill(S.rev, h('h2', { text: 'Rà soát bản build' }),
    h('p', { class: 'small muted', text: 'Agent Vua nhận một nhiệm vụ có ID, chỉ được ĐỌC đúng dự án này: quyết định còn hiệu lực → commit → bản đồ → báo cáo test → tổng hợp. Ngân sách API mặc định 0: không có model được phép thì chỉ chạy các bước theo quy tắc.' }),
    owner() ? h('div', { class: 'grid2' }, field('Model phân tích', ana), field('Model kiểm tra lại (tùy chọn)', ver)) : null,
    h('div', { class: 'row' }, btn), out, h('h3', { text: 'Các lượt gần đây' }), runs);
  loadRuns();
  if (showRun) showReport(out, showRun);
}

function showReport(box, r) {
  const rep = r.report || {};
  const sec = (title, items, kind) => h('div', { class: 'stack' }, h('h3', {}, badge(kind, title)),
    (items || []).length ? h('ul', {}, items.map((x) => h('li', {}, x.text, x.evidence ? h('span', { class: 'small muted', text: ` (nguồn: ${x.evidence})` }) : null,
      (x.items || []).length ? h('ul', {}, x.items.slice(0, 12).map((it) => h('li', { class: 'small', text: it }))) : null))) : h('p', { class: 'small muted', text: '—' }));
  if (r.status === 'SKIPPED_DUPLICATE') {
    fill(box, h('div', { class: 'banner', role: 'status', dataset: { status: r.status } }, h('strong', { text: `${r.id}: không chạy lại — cùng commit và cùng đầu vào với ${r.duplicate_of}` }),
      h('div', { class: 'row' }, h('button', { class: 'btn small', text: `Xem báo cáo ${r.duplicate_of}`, onclick: async () => showReport(box, await get(`/ops/reviews/${r.duplicate_of}`)) }))));
    return;
  }
  const md = r.report_md || '';
  fill(box, h('div', { class: 'stack', dataset: { status: r.status, run: r.id } },
    h('div', { class: 'row' }, badge(r.status), h('strong', { text: `${r.id} · nhiệm vụ ${r.task_id}` }), h('span', { class: 'spacer' }),
      md ? h('button', { class: 'btn small', text: 'Tải báo cáo .md', onclick: () => {
        const url = URL.createObjectURL(new Blob([md], { type: 'text/markdown;charset=utf-8' }));
        const a = h('a', { href: url, download: `${r.id}.md` }); document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      } }) : null),
    r.error ? h('p', { class: 'note bad', text: r.error }) : null,
    sec('ĐÃ KIỂM CHỨNG', rep.verified, 'ok'), sec('CHỈ ĐƯỢC BÁO CÁO', rep.reported, 'warn'), sec('CHƯA BIẾT / KHÔNG DÙNG LÀM CĂN CỨ', rep.unknown, 'info'),
    h('h3', { text: 'Việc tiếp theo (đề xuất — cần người duyệt)' }),
    (rep.next_actions || []).length ? h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['Ưu tiên', 'Việc', 'Phụ trách'].map((x) => h('th', { text: x })))),
      h('tbody', {}, rep.next_actions.map((a) => h('tr', {}, h('td', { text: a.priority }), h('td', { text: a.action }), h('td', { text: a.owner_name })))))) : h('p', { class: 'small muted', text: 'Không có.' }),
    h('h3', { text: 'Các bước' }),
    h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['Bước', 'Kết quả', 'Gọi AI thật', 'Ghi chú'].map((x) => h('th', { text: x })))),
      h('tbody', {}, (r.steps || []).map((s) => h('tr', {}, h('td', { text: s.name }), h('td', {}, badge(s.status === 'DONE' ? 'ok' : s.status === 'FAILED' ? 'bad' : 'info', s.status)),
        h('td', { text: s.ai_called ? 'CÓ' : 'KHÔNG' }), h('td', { class: 'small', text: s.note || '' })))))),
    h('p', { class: 'small muted', text: rep.policy || '' })));
}

// ------------------------------------------------------------------ lịch
function renderSchedule() {
  const sc = S.status.schedule;
  const on = h('input', { type: 'checkbox', checked: !!sc.enabled, disabled: !owner() || (!sc.can_enable && !sc.enabled), 'aria-label': 'Bật lịch rà soát' });
  on.addEventListener('change', async () => {
    if (on.checked && !(await confirmDlg('Bật lịch rà soát', `Agent Vua sẽ tự rà soát mỗi ${sc.interval_minutes / 60} giờ khi máy chủ đang chạy. Lượt trùng đầu vào sẽ tự bỏ qua. Không merge/deploy/tăng ngân sách.`, 'Bật'))) { on.checked = false; return; }
    try { await put('/ops/schedule', { project_id: S.pid, enabled: on.checked, interval_minutes: sc.interval_minutes }); toast(on.checked ? 'Đã bật lịch' : 'Đã tắt lịch'); renderStatus(); }
    catch (e) { on.checked = !on.checked; toastErr(e); }
  });
  const svc = sc.service;
  fill(S.sch, h('h2', { text: 'Lịch tự động' }),
    h('label', { class: 'row' }, on, h('strong', { text: `Rà soát mỗi ${sc.interval_minutes / 60} giờ (mặc định TẮT)` })),
    !sc.can_enable && !sc.enabled ? h('p', { class: 'small note', text: 'Cần một lần "Rà soát bản build" thủ công thành công trước khi bật lịch.' }) : null,
    h('div', { class: 'small' }, `Lần chạy gần nhất: ${sc.last_run_at ? `${fmtTime(sc.last_run_at)} · ${sc.last_status} · ${sc.last_run_id || ''}` : 'chưa có'}`),
    h('div', { class: 'small' }, `Lần kế tiếp: ${sc.enabled && sc.next_run_at ? fmtTime(sc.next_run_at) : '— (lịch đang tắt)'}`),
    h('div', { class: 'row small' }, 'Dịch vụ lập lịch: ', badge(svc.status), h('span', { class: 'muted', text: svc.evidence })),
    h('p', { class: 'small muted', text: sc.machine_note }));
}

