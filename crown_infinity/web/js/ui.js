// Tiện ích DOM: chỉ dùng textContent/thuộc tính — không chèn HTML từ dữ liệu người dùng.
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k === 'value') el.value = v;
    else if (k === 'checked' || k === 'disabled' || k === 'selected' || k === 'hidden') el[k] = !!v;
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export const clear = (el) => { while (el.firstChild) el.firstChild.remove(); return el; };
// add/fill: như append nhưng làm phẳng mảng và bỏ qua null/false (append gốc của DOM sẽ in "null").
export const add = (el, ...children) => append(el, children);
export const fill = (el, ...children) => append(clear(el), children);

export function toast(message, kind = '') {
  const t = h('div', { class: `toast ${kind}`, role: kind === 'bad' ? 'alert' : 'status', text: message });
  document.getElementById('toasts').append(t);
  setTimeout(() => t.remove(), kind === 'bad' ? 7000 : 3500);
}

export const errText = (e) => (e && e.message ? e.message : String(e));
export function toastErr(e, prefix = '') { toast(`${prefix}${prefix ? ': ' : ''}${errText(e)}`, 'bad'); }

export function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('vi-VN', { hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export const badge = (status, label) => h('span', { class: `badge b-${status}`, text: label || status });

export function dialog(title, body, actions = []) {
  // actions: [{label, kind, onClick(close) → Promise|void}]
  const dlg = h('dialog', { 'aria-label': title });
  const err = h('p', { class: 'form-error', role: 'alert' });
  const close = () => { if (dlg.open) dlg.close(); };
  const btns = actions.map((a) => h('button', {
    class: `btn ${a.kind || ''}`, type: 'button', text: a.label,
    onclick: async (ev) => {
      const b = ev.currentTarget; b.disabled = true; err.textContent = '';
      try { await a.onClick(close); } catch (e) { err.textContent = errText(e); } finally { if (b.isConnected) b.disabled = false; }
    },
  }));
  append(dlg, [h('div', { class: 'dlg' }, h('h2', { text: title }), body, err,
    h('div', { class: 'row end' }, h('button', { class: 'btn', type: 'button', text: 'Đóng', onclick: close }), btns))]);
  dlg.addEventListener('close', () => setTimeout(() => dlg.remove(), 0));
  document.body.append(dlg);
  dlg.showModal();
  return { dlg, close, err };
}

export function confirmDlg(title, message, okLabel = 'Đồng ý', kind = 'primary') {
  return new Promise((resolve) => {
    let done = false;
    const { dlg } = dialog(title, h('p', { text: message }), [{ label: okLabel, kind, onClick: (close) => { done = true; resolve(true); close(); } }]);
    dlg.addEventListener('close', () => { if (!done) resolve(false); });
  });
}

export function field(label, input) { return h('label', {}, label, input); }

export function select(options, value, attrs = {}) {
  const s = h('select', attrs);
  for (const o of options) {
    const [v, l] = Array.isArray(o) ? o : [o, o];
    s.append(h('option', { value: v, text: l, selected: String(v) === String(value ?? '') }));
  }
  return s;
}

// Markdown an toàn: dựng nút DOM, không bao giờ diễn giải HTML trong prompt.
export function renderMarkdown(src) {
  const root = h('div');
  const lines = src.split('\n');
  let i = 0; let list = null; let para = [];
  const flushPara = () => { if (para.length) { root.append(h('p', {}, inline(para.join('\n')))); para = []; } };
  const flushList = () => { list = null; };
  while (i < lines.length) {
    const ln = lines[i];
    if (/^```/.test(ln)) {
      flushPara(); flushList();
      const buf = []; i += 1;
      while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i += 1; }
      root.append(h('pre', {}, h('code', { text: buf.join('\n') }))); i += 1; continue;
    }
    const hm = ln.match(/^(#{1,6})\s+(.*)$/);
    if (hm) { flushPara(); flushList(); root.append(h(`h${Math.min(hm[1].length + 1, 6)}`, {}, inline(hm[2]))); i += 1; continue; }
    const lm = ln.match(/^\s*(?:[-*+]|(\d+)[.)])\s+(.*)$/);
    if (lm) {
      flushPara();
      const tag = lm[1] ? 'ol' : 'ul';
      if (!list || list.tagName.toLowerCase() !== tag) { list = h(tag); root.append(list); }
      list.append(h('li', {}, inline(lm[2]))); i += 1; continue;
    }
    if (!ln.trim()) { flushPara(); flushList(); i += 1; continue; }
    flushList(); para.push(ln); i += 1;
  }
  flushPara();
  return root;
}

function inline(text) {
  const out = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\{\{[^}]*\}\})|(\[[^\]]+\]\((https?:\/\/[^)\s]+)\))/g;
  let last = 0; let m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1]) out.push(h('code', { text: m[1].slice(1, -1) }));
    else if (m[2]) out.push(h('strong', { text: m[2].slice(2, -2) }));
    else if (m[3]) out.push(h('span', { class: 'var', text: m[3] }));
    else if (m[4]) out.push(h('a', { href: m[5], target: '_blank', rel: 'noopener noreferrer nofollow', text: m[4].slice(1, m[4].indexOf(']')) }));
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// Diff dòng phía client (dùng cho màn xung đột: bản của tôi ↔ bản trên máy chủ).
export function lineDiff(a, b) {
  const A = a.split('\n'); const B = b.split('\n');
  if (A.length * B.length > 4_000_000) return null;
  const n = A.length; const m = B.length;
  const dp = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let x = n - 1; x >= 0; x -= 1) for (let y = m - 1; y >= 0; y -= 1) dp[x][y] = A[x] === B[y] ? dp[x + 1][y + 1] + 1 : Math.max(dp[x + 1][y], dp[x][y + 1]);
  const rows = []; let x = 0; let y = 0;
  while (x < n && y < m) {
    if (A[x] === B[y]) { rows.push({ op: 'equal', a: x + 1, b: y + 1, text: A[x] }); x += 1; y += 1; }
    else if (dp[x + 1][y] >= dp[x][y + 1]) { rows.push({ op: 'delete', a: x + 1, b: null, text: A[x] }); x += 1; }
    else { rows.push({ op: 'insert', a: null, b: y + 1, text: B[y] }); y += 1; }
  }
  while (x < n) { rows.push({ op: 'delete', a: x + 1, b: null, text: A[x] }); x += 1; }
  while (y < m) { rows.push({ op: 'insert', a: null, b: y + 1, text: B[y] }); y += 1; }
  return rows;
}

export function diffView(rows) {
  const box = h('div', { class: 'diff', role: 'table', 'aria-label': 'Khác biệt theo dòng' });
  for (const r of rows) {
    box.append(h('div', { class: r.op, role: 'row' },
      h('span', { class: 'ln', text: r.a ?? '' }), h('span', { class: 'ln', text: r.b ?? '' }),
      h('span', { text: r.op === 'insert' ? '+' : r.op === 'delete' ? '−' : ' ' }), h('span', { text: r.text })));
  }
  return box;
}

export function countWords(text) {
  const t = text.trim();
  return t ? t.split(/\s+/u).length : 0;
}

export function readFileB64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => {
      const bytes = new Uint8Array(r.result); let bin = '';
      for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
      resolve(btoa(bin));
    };
    r.onerror = () => reject(new Error(`Không đọc được tệp ${file.name}`));
    r.readAsArrayBuffer(file);
  });
}
