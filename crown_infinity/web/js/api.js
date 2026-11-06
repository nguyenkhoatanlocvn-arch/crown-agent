// Gọi API v1. Mọi lỗi HTTP ném ApiError — giao diện KHÔNG được báo thành công khi API thất bại.
export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status; this.code = code; this.details = details;
  }
}

let csrf = null;
const listeners = new Set();
export const setCsrf = (t) => { csrf = t; };
export const onConnection = (fn) => listeners.add(fn);
const emit = (ok) => listeners.forEach((fn) => fn(ok));

export async function api(method, path, { body, ifMatch, signal } = {}) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (csrf && method !== 'GET') headers['X-CSRF-Token'] = csrf;
  if (ifMatch !== undefined && ifMatch !== null) headers['If-Match'] = `"${ifMatch}"`;
  let res;
  try {
    res = await fetch(`/api/v1${path}`, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'same-origin', signal,
    });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    emit(false);
    throw new ApiError(0, 'NETWORK', 'Mất kết nối tới máy chủ CROWN INFINITY');
  }
  emit(true);
  const text = await res.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = null; }
  }
  if (!res.ok) {
    const err = (data && data.error) || {};
    throw new ApiError(res.status, err.code || `HTTP_${res.status}`, err.message || `Lỗi máy chủ (HTTP ${res.status})`, err.details);
  }
  if (data === null) throw new ApiError(res.status, 'BAD_RESPONSE', 'Máy chủ trả dữ liệu không đọc được');
  return data;
}

export const get = (p, o) => api('GET', p, o);
export const post = (p, body = {}, o = {}) => api('POST', p, { ...o, body });
export const put = (p, body = {}, o = {}) => api('PUT', p, { ...o, body });
export const patch = (p, body = {}, o = {}) => api('PATCH', p, { ...o, body });
export const del = (p, o) => api('DELETE', p, o);

export function qs(params) {
  const u = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') u.set(k, v); });
  const s = u.toString();
  return s ? `?${s}` : '';
}
