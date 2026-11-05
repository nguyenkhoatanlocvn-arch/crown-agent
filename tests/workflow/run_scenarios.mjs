// Chạy thử workflow agent-vua-vuong-toa trên sổ SQLite thật (DB kiểm thử riêng), fixture cho phần việc.
// node tests/workflow/run_scenarios.mjs  → in bảng PASS/FAIL và ghi runtime/test/workflow_report.json
import { runWorkflow, REPO } from './harness.mjs'
import { rmSync, mkdirSync, writeFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import path from 'node:path'

const WF = path.join(REPO, '.claude', 'workflows', 'agent-vua-vuong-toa.js')
const TDIR = path.join(REPO, 'runtime', 'test')
rmSync(TDIR, { recursive: true, force: true }); mkdirSync(TDIR, { recursive: true })
rmSync(path.join(REPO, 'runtime', 'hoang_thanh_local'), { recursive: true, force: true })

const PLAN = [
  { key: 'S1', owner: 'A03', kind: 'work', objective: 'Thẩm định skill verification-before-completion', output: 'link SKILL.md@commit, commit, quyền, 2 ví dụ', acceptance: 'đủ 4 mục, kiểm lại được', write_scope: ['agents/A03/'] },
  { key: 'S2', owner: 'A03', kind: 'sync', objective: 'Đồng bộ kết quả sang tab SKILL', output: 'biên nhận đọc lại', acceptance: 'đọc lại thấy op_id', depends_on: ['S1'], write_scope: ['hoang-thanh:skill_state'] },
]
const base = (id, extra = {}) => ({
  request_id: id, objective: 'Giao A03 thẩm định skill verification-before-completion của obra/superpowers',
  scope: { domain: 'SKILL' }, acceptance: ['Link SKILL.md đúng commit', 'Một ví dụ đạt, một ví dụ trượt', 'Đồng bộ tab SKILL đọc lại được'],
  issued_by: 'CHAIRMAN', mode: 'TEST', python: 'python3', db: path.join(TDIR, `${id}.db`), plan: PLAN, ...extra,
})
const cli = (db, cmd, obj) => JSON.parse(execFileSync('python3', ['-m', 'crown', 'vua', cmd, '--q', encodeURIComponent(JSON.stringify(obj))], { cwd: REPO, env: { ...process.env, CROWN_DB: db } }).toString())
const countTasks = db => Number(execFileSync('python3', ['-c', `import sqlite3;print(sqlite3.connect(${JSON.stringify(db)}).execute("select count(*) from tasks").fetchone()[0])`]).toString())

const rows = []
function check(scn, name, cond, seen) { rows.push({ scenario: scn, check: name, pass: !!cond, observed: seen }); }

// 0. Đường chuẩn
{
  const a = base('VT-TEST-0001')
  const r = await runWorkflow(WF, a)
  const s = r.result
  check('0 đường chuẩn', 'không lỗi', !r.error, r.error?.message || 'ok')
  check('0 đường chuẩn', 'gốc VERIFIED_DONE', s?.root_state === 'VERIFIED_DONE', s?.root_state)
  check('0 đường chuẩn', 'S1, S2 VERIFIED_DONE', s?.children.S1.state === 'VERIFIED_DONE' && s?.children.S2.state === 'VERIFIED_DONE', `${s?.children.S1.state}/${s?.children.S2.state}`)
  check('0 đường chuẩn', 'nhãn dữ liệu kiểm thử', s?.label === 'DỮ LIỆU KIỂM THỬ', s?.label)
  check('0 đường chuẩn', 'outbox CONFIRMED có biên nhận', s?.outbox[0]?.status === 'CONFIRMED' && /sha256/.test(s.outbox[0].receipt), s?.outbox[0]?.receipt)
  check('0 đường chuẩn', 'người kiểm khác người làm (A20)', r.calls.some(c => c.label === 'kiểm:S1:A20'), r.calls.filter(c => c.label.startsWith('kiểm')).map(c => c.label).join(','))
  check('0 đường chuẩn', 'cửa A03 về sáng sau khi xong', s?.doors.A03.activity === 'IDLE', s?.doors.A03.door)
  const lk = await runWorkflow(WF, { action: 'lookup', code: s?.task_id, python: 'python3', db: a.db })
  check('0 đường chuẩn', 'tra theo mã sau khi xong', lk.result?.ok && lk.result.root.state === 'VERIFIED_DONE', `${s?.task_id} → ${lk.result?.root?.state}`)
  const lk2 = cli(a.db, 'lookup', { code: s?.children.S1.task_id })
  check('0 đường chuẩn', 'tra mã việc con có bằng chứng', lk2.ok && lk2.result.evidence.length > 0, lk2.result.evidence.join(' | '))
  const again = await runWorkflow(WF, a)
  check('0 đường chuẩn', 'gửi lại cùng request_id → nhiệm vụ cũ', again.result?.kind === 'ĐÃ_XONG_TRƯỚC_ĐÓ' && again.result.task_id === s?.task_id, `${again.result?.kind} ${again.result?.task_id}`)
  check('0 đường chuẩn', 'không phát sinh nhiệm vụ mới', countTasks(a.db) === 3, `${countTasks(a.db)} nhiệm vụ trong sổ`)
  check('0 đường chuẩn', 'lượt gọi agent', true, `${r.calls.length} (sổ ${r.calls.filter(c => c.label.startsWith('sổ:')).length}, việc ${r.calls.filter(c => !c.label.startsWith('sổ:')).length})`)
}

// 1. Gửi trùng đồng thời
{
  const a = base('VT-TEST-DUP')
  const [x, y] = await Promise.all([runWorkflow(WF, a), runWorkflow(WF, a)])
  const kinds = [x.result?.kind, y.result?.kind]
  check('1 gửi trùng đồng thời', 'hai lượt cùng một mã nhiệm vụ', x.result?.task_id && x.result.task_id === y.result?.task_id, `${x.result?.task_id} / ${y.result?.task_id}`)
  check('1 gửi trùng đồng thời', 'chỉ một lượt chạy, lượt kia trả trạng thái', kinds.filter(k => k === 'XONG_CHỜ_ĐỌC_LẠI').length === 1, kinds.join(' + '))
  check('1 gửi trùng đồng thời', 'Rồng chỉ làm một lần', [...x.calls, ...y.calls].filter(c => c.label.startsWith('làm:S1')).length === 1, `${[...x.calls, ...y.calls].filter(c => c.label.startsWith('làm:')).length} lượt làm`)
  check('1 gửi trùng đồng thời', 'sổ có đúng 3 nhiệm vụ', countTasks(a.db) === 3, `${countTasks(a.db)} nhiệm vụ`)
}

// 2a. Ngắt sau khi A03 đã nộp kết quả, rồi tiếp tục
{
  const a = base('VT-TEST-KILL-A', { lock_ttl: 900 })
  const firstWork = (await runWorkflow(WF, { ...a, request_id: 'probe', db: path.join(TDIR, 'probe.db') })).calls.findIndex(c => c.label === 'kiểm:S1:A20') + 1
  const r1 = await runWorkflow(WF, a, { killAt: firstWork })   // chết ngay trước khi A20 kiểm
  const mid = cli(a.db, 'status', { request_id: a.request_id }).result
  check('2a ngắt rồi tiếp (sau khi làm)', 'lượt 1 bị ngắt', r1.error?.killed, r1.error?.message)
  check('2a ngắt rồi tiếp (sau khi làm)', 'sổ giữ S1 ở REVIEW + kết quả', mid.children.S1.state === 'REVIEW' && mid.steps.includes('S1:result'), `${mid.children.S1.state}; steps=${mid.steps.join(',')}`)
  const busy = await runWorkflow(WF, a)
  check('2a ngắt rồi tiếp (sau khi làm)', 'chạy lại khi khóa còn hạn → không chạy chồng', busy.result?.kind === 'ĐANG_CHẠY_Ở_LƯỢT_KHÁC', busy.result?.kind)
  const r2 = await runWorkflow(WF, { ...a, takeover: true })
  check('2a ngắt rồi tiếp (sau khi làm)', 'takeover tiếp tục tới VERIFIED_DONE', r2.result?.root_state === 'VERIFIED_DONE', r2.result?.root_state || r2.error?.message)
  check('2a ngắt rồi tiếp (sau khi làm)', 'không làm lại S1', r2.calls.filter(c => c.label.startsWith('làm:')).length === 0, `${r2.calls.filter(c => c.label.startsWith('làm:')).length} lượt làm ở lượt 2`)
  check('2a ngắt rồi tiếp (sau khi làm)', 'không tính thử lại', r2.result?.children.S1.retry === 0, `retry=${r2.result?.children.S1.retry}`)
}

// 2b. Ngắt giữa lúc đồng bộ (đã ghi tới đích, chưa báo) → đối soát trước khi gửi lại
{
  const a = base('VT-TEST-KILL-B')
  const r1 = await runWorkflow(WF, a, { sync: 'WRITE_THEN_KILL' })
  const mid = cli(a.db, 'status', { request_id: a.request_id }).result
  check('2b ngắt giữa đồng bộ', 'outbox kẹt SENDING (chưa rõ)', mid.outbox[0]?.status === 'SENDING' && mid.outbox[0].needs_reconcile, `${mid.outbox[0]?.status} needs_reconcile=${mid.outbox[0]?.needs_reconcile}`)
  const r2 = await runWorkflow(WF, { ...a, takeover: true })
  const sends = r2.calls.filter(c => c.label.startsWith('đồng bộ:')).length
  check('2b ngắt giữa đồng bộ', 'đối soát trước, không gửi lại', r2.calls.some(c => c.label.startsWith('đối soát:')) && sends === 0, `đối soát=${r2.calls.filter(c => c.label.startsWith('đối soát:')).length}, gửi=${sends}`)
  check('2b ngắt giữa đồng bộ', 'outbox CONFIRMED, attempts=1', r2.result?.outbox[0]?.status === 'CONFIRMED' && r2.result.outbox[0].attempts === 1, `${r2.result?.outbox[0]?.status} attempts=${r2.result?.outbox[0]?.attempts}`)
  check('2b ngắt giữa đồng bộ', 'gốc VERIFIED_DONE', r2.result?.root_state === 'VERIFIED_DONE', r2.result?.root_state || r2.error?.message)
}

// 3. Thiếu quyền đồng bộ → ghi lý do, giữ kết quả cục bộ; cấp quyền rồi chạy lại
{
  const a = base('VT-TEST-NOPERM')
  const r1 = await runWorkflow(WF, a, { sync: 'NO_PERMISSION' })
  const s = r1.result
  check('3 thiếu quyền đồng bộ', 'không lỗi, dừng có kiểm soát', !r1.error && s?.kind === 'DỪNG_CÓ_VIỆC_CHỜ', r1.error?.message || s?.kind)
  check('3 thiếu quyền đồng bộ', 'S1 vẫn VERIFIED_DONE (phần cục bộ giữ)', s?.children.S1.state === 'VERIFIED_DONE', s?.children.S1.state)
  check('3 thiếu quyền đồng bộ', 'S2 BLOCKED kèm lý do', s?.children.S2.state === 'BLOCKED' && /thiếu quyền/.test(s.children.S2.last), s?.children.S2.last)
  check('3 thiếu quyền đồng bộ', 'cửa A03 = xích đỏ', s?.doors.A03.activity === 'BLOCKED', s?.doors.A03.door)
  check('3 thiếu quyền đồng bộ', 'cửa Agent Vua = xích đỏ (gốc chờ)', s?.doors.VUA_CEO.activity === 'BLOCKED', s?.doors.VUA_CEO.door)
  const r2 = await runWorkflow(WF, a, { sync: 'CONFIRMED' })
  check('3 thiếu quyền đồng bộ', 'cấp quyền rồi chạy lại → xong, không làm lại S1', r2.result?.root_state === 'VERIFIED_DONE' && !r2.calls.some(c => c.label.startsWith('làm:')), `${r2.result?.root_state}; làm=${r2.calls.filter(c => c.label.startsWith('làm:')).length}`)
}

// 4. Người kiểm đánh trượt lần đầu → làm lại một lần
{
  const a = base('VT-TEST-REWORK')
  const r = await runWorkflow(WF, a, { review: { S1: ['FAIL', 'PASS'] } })
  check('4 trượt rồi làm lại', 'xong sau 1 lần làm lại', r.result?.root_state === 'VERIFIED_DONE' && r.result.children.S1.retry === 1, `${r.result?.root_state} retry=${r.result?.children.S1.retry}`)
  const r2 = await runWorkflow(WF, base('VT-TEST-FAIL2'), { review: { S1: ['FAIL', 'FAIL'] } })
  check('4 trượt rồi làm lại', 'trượt 2 lần → dừng, không đồng bộ', r2.result?.children.S1.state === 'FAILED' && r2.result.children.S2.state === 'READY' && !r2.calls.some(c => c.label.startsWith('đồng bộ:')), `${r2.result?.children.S1.state}/${r2.result?.children.S2.state}`)
}

// 4b. Người kiểm đánh trượt biên nhận đồng bộ → gửi lại bằng op_id mới, không dùng biên nhận cũ
{
  const r = await runWorkflow(WF, base('VT-TEST-SYNCFAIL'), { review: { S2: ['FAIL', 'PASS'] } })
  const ops = (r.result?.outbox || []).map(o => `${o.op_id}:${o.status}`)
  check('4b biên nhận bị trượt', 'gửi lại với op_id #1 và xong', r.result?.root_state === 'VERIFIED_DONE' && ops.includes('VT-TEST-SYNCFAIL:S2#1:CONFIRMED'), `${r.result?.root_state}; ${ops.join(', ')}`)
  check('4b biên nhận bị trượt', 'lần kiểm thứ hai có nhãn riêng', r.calls.some(c => c.label === 'kiểm:S2:A20#r1'), r.calls.filter(c => c.label.startsWith('kiểm:S2')).map(c => c.label).join(','))
}

// 4c. Bị ngắt giữa lần đồng bộ lại (lỗi thật gặp khi chạy thật 05/11) → tiếp tục, giữ góp ý, đối soát
{
  const a = base('VT-TEST-SYNCFAIL-KILL')
  const r1 = await runWorkflow(WF, a, { review: { S2: ['FAIL', 'PASS'] }, sync: n => (n === 1 ? 'CONFIRMED' : 'WRITE_THEN_KILL') })
  const r2 = await runWorkflow(WF, { ...a, takeover: true }, { review: { S2: ['PASS'] } })
  const ops = (r2.result?.outbox || []).map(o => `${o.op_id}:${o.status}`)
  check('4c ngắt giữa lần đồng bộ lại', 'lượt 1 bị ngắt khi gửi #1', r1.error?.killed, r1.error?.message)
  check('4c ngắt giữa lần đồng bộ lại', 'tiếp tục: đối soát #1, không lỗi, xong', r2.result?.root_state === 'VERIFIED_DONE' && ops.includes('VT-TEST-SYNCFAIL-KILL:S2#1:CONFIRMED') && !r2.logs.some(l => /LỖI/.test(l)), `${r2.result?.root_state}; ${ops.join(', ')}; ${r2.logs.filter(l => /LỖI/.test(l)).join(' ')}`)
}

// 5. Quyền Vương Tọa, việc cần duyệt, Hội đồng
{
  const r = await runWorkflow(WF, base('VT-TEST-AUTH', { issued_by: 'VUA_CEO', scope: { domain: 'WEB' } }))
  check('5 quyền & duyệt', 'Vương Tọa AI chưa ủy nhiệm → từ chối', r.result?.stop === 'TỪ_CHỐI_NHẬN_LỆNH', r.result?.reason)
  const plan = [{ ...PLAN[0], needs_approval: true, needs_council: true }]
  const a = base('VT-TEST-APPROVE', { plan })
  const r1 = await runWorkflow(WF, a)
  check('5 quyền & duyệt', 'việc cần duyệt dừng ở WAITING_APPROVAL', r1.result?.children.S1.state === 'WAITING_APPROVAL', r1.result?.children.S1.state)
  check('5 quyền & duyệt', 'cửa A03 = lồng đèn vàng khi chờ Chủ tịch duyệt', r1.result?.doors.A03.activity === 'REVIEW', r1.result?.doors.A03.door)
  const r2 = await runWorkflow(WF, { ...a, approvals: { S1: 'QD-TEST-01' } })
  check('5 quyền & duyệt', 'có quyết định hợp lệ → tiếp tục và xong', r2.result?.root_state === 'VERIFIED_DONE', r2.result?.root_state || r2.error?.message)
  check('5 quyền & duyệt', 'Hội đồng không kết nối → ghi lý do, làm cục bộ', r2.logs.some(l => /Hội đồng không mời — .*chưa có kết nối/.test(l)), r2.logs.find(l => /Hội đồng/.test(l)))
}

const pass = rows.filter(r => r.pass).length
for (const r of rows) console.log(`${r.pass ? 'PASS' : 'FAIL'} | ${r.scenario} | ${r.check} | ${r.observed}`)
console.log(`\n${pass}/${rows.length} PASS`)
writeFileSync(path.join(TDIR, 'workflow_report.json'), JSON.stringify({ label: 'DỮ LIỆU KIỂM THỬ (fixture cho phần việc; sổ SQLite chạy thật)', rows }, null, 2))
process.exit(pass === rows.length ? 0 : 1)
