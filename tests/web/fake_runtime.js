/* Runtime giả của window.claude để thử tab TOOL ngoài claude.ai. Chỉ dùng trong test. */
(function(){
  const cfg = window.__FAKE__ || {};
  const store = {};              // collection -> {id: data}
  const subs = [];               // [collection, cb]
  const calls = (window.__CALLS__ = {mcp: [], uploads: [], writes: []});
  const snap = name => ({docs: Object.entries(store[name] || {}).map(([id, data]) => ({id, exists: true, data: () => JSON.parse(JSON.stringify(data))}))});
  const fire = name => subs.filter(s => s[0] === name).forEach(s => setTimeout(() => s[1](snap(name)), 0));
  const put = (c, id, data, merge) => { store[c] = store[c] || {}; store[c][id] = merge ? Object.assign({}, store[c][id] || {}, data) : data; calls.writes.push({c, id, merge: !!merge}); fire(c); };
  for (const [c, docs] of Object.entries(cfg.seed || {})) store[c] = JSON.parse(JSON.stringify(docs));
  window.__STORE__ = store; window.__PUT__ = put;
  const docRef = (c, id) => ({ get: async () => ({exists: !!(store[c] || {})[id], data: () => (store[c] || {})[id]}), set: async d => put(c, id, d), update: async d => put(c, id, d, true) });
  const db = {
    collection: c => ({ onSnapshot(cb){ subs.push([c, cb]); setTimeout(() => cb(snap(c)), 0); return () => {}; }, doc: id => docRef(c, id),
                        add: async d => { const id = "auto" + Math.random().toString(36).slice(2, 8); put(c, id, d); return {id}; } }),
    doc: path => { const i = path.indexOf("/"); return docRef(path.slice(0, i), path.slice(i + 1)); },
  };
  const trig = cfg.trigger || {id: "trig_TEST", enabled: true, next_run_at: "2026-11-06T01:05:00Z", last_run: {status: "ROUTINE_RUN_STATUS_SUCCEEDED", fired_at: "2026-11-06T00:05:03Z"}};
  const mcp = {
    callTool: async (server, tool, input) => {
      calls.mcp.push({server, tool, input});
      if (cfg.mcpError) throw {code: cfg.mcpError, message: "fake"};
      if (tool === "list_triggers") return {payload: {data: [trig], has_more: false}};
      if (tool === "update_trigger") { trig.enabled = input.enabled; return {payload: {trigger: trig}}; }
      return {payload: {ok: true}};
    },
  };
  const caps = {
    db: cfg.noDb ? null : db,
    user: {canEdit: async () => cfg.canEdit !== false, isOwner: async () => true},
    assets: cfg.noAssets ? null : {upload: async (blob, o) => { calls.uploads.push({size: blob.size, type: (o || {}).type, name: blob.name}); return {id: Array.from({length: 32}, () => "0123456789abcdef"[Math.random() * 16 | 0]).join(""), url: "/_blob/x", sizeBytes: blob.size, contentType: (o || {}).type || blob.type}; }},
    mcp: cfg.noMcp ? null : mcp,
    permissions: {state: async () => cfg.mcpState || "granted", request: async () => ({})},
  };
  window.claude = {use: async name => (name in caps ? caps[name] : null)};
})();
