// Bộ chạy thử CỤC BỘ cho .claude/workflows/*.js — mô phỏng runtime Workflow của Claude Code.
// - Lệnh sổ (<<CMD>> ... <<END>>) được CHẠY THẬT trên sổ SQLite (crown/vua.py), DB kiểm thử riêng.
// - Việc của Rồng / người kiểm / đồng bộ dùng FIXTURE (dữ liệu kiểm thử, không phải kết quả thật).
// - Chặn Date.now()/Math.random()/new Date() như runtime thật; không cấp fs cho script.
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs'
import { exec } from 'node:child_process'
import { promisify } from 'node:util'
import { createHash } from 'node:crypto'
import path from 'node:path'

const sh = promisify(exec)
export const REPO = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..', '..')

class Killed extends Error { constructor(n) { super(`TIẾN TRÌNH BỊ NGẮT tại lượt agent #${n}`); this.killed = true } }

function guardedDate() {
  const D = function (...a) { if (!a.length) throw new Error('new Date() bị cấm trong workflow'); return new Date(...a) }
  D.now = () => { throw new Error('Date.now() bị cấm trong workflow') }
  D.parse = Date.parse; D.UTC = Date.UTC
  return D
}
function guardedMath() {
  const M = Object.create(Math)
  M.random = () => { throw new Error('Math.random() bị cấm trong workflow') }
  return M
}

export function loadWorkflow(file) {
  const src = readFileSync(file, 'utf8')
  if (!/^export const meta = \{/.test(src)) throw new Error('Script phải bắt đầu bằng export const meta = {')
  const end = src.indexOf('\n}\n')
  const metaSrc = src.slice('export const meta = '.length, end + 2)
  if (/\$\{|`|\.\.\.|\w\(/.test(metaSrc.replace(/'[^']*'/g, "''"))) throw new Error('meta phải là literal thuần')
  const meta = Function(`"use strict"; return (${metaSrc})`)()
  const body = src.slice(end + 3)
  return { meta, body }
}

export async function runWorkflow(file, args, fx = {}) {
  const { meta, body } = loadWorkflow(file)
  const calls = []
  let n = 0, dead = false
  const logs = []
  const ctx = { phase: null }

  async function agent(prompt, opts = {}) {
    n++
    const label = opts.label || '(no label)'
    calls.push({ n, label, phase: ctx.phase, model: opts.model || 'default' })
    if (dead) throw new Killed(n)
    if (fx.killAt && n === fx.killAt) { dead = true; throw new Killed(n) }
    const cmd = (prompt.match(/<<CMD>>\n([\s\S]*?)\n<<END>>/) || [])[1]
    if (fx.live && !label.startsWith('sổ:')) {
      // CHẠY THẬT từng bước: vai Rồng/người kiểm/đồng bộ do Agent thật trả lời (ghi trong answers),
      // chưa có câu trả lời → dừng lượt, ghi prompt ra pending để Agent Vua giao đúng prompt này.
      const ans = existsSync(fx.live.answers) ? JSON.parse(readFileSync(fx.live.answers, 'utf8')) : {}
      if (ans[label]) { calls[calls.length - 1].answered_by = ans[label].answered_by; return ans[label].output }
      writeFileSync(fx.live.pending, JSON.stringify({ label, prompt, schema: opts.schema || null, model: opts.model || null }, null, 2))
      dead = true
      const e = new Killed(n); e.pending = label; throw e
    }
    if (label.startsWith('nghiệm thu')) {
      const r = await runCmd(cmd)
      const s = JSON.parse(r.stdout).result
      const ok = Object.values(s.children).every(c => c.state === 'VERIFIED_DONE')
      return { verdict: ok ? 'PASS' : 'FAIL', checks: (args.acceptance || []).map(t => ({ text: t, passed: ok, evidence: `[TEST_FIXTURE] sổ: ${Object.values(s.children).map(c => c.task_id + ':' + c.state).join(', ')}` })) }
    }
    if (label.startsWith('sổ:')) return runCmd(cmd)
    if (label.startsWith('Agent Vua: lập kế hoạch')) return { subtasks: fx.plan, reasoning: '[TEST_FIXTURE]' }
    if (label.startsWith('làm:')) {
      const key = label.split(':')[1]
      const f = fx.work?.[key]
      const v = typeof f === 'function' ? f(calls.filter(c => c.label.startsWith(`làm:${key}:`)).length) : f
      return v || { status: 'DONE', result_json: JSON.stringify({ test_fixture: true, key }), evidence: [`[TEST_FIXTURE] ${key}`] }
    }
    if (label.startsWith('kiểm:')) {
      const key = label.split(':')[1]
      const seq = fx.review?.[key] || ['PASS']
      const i = calls.filter(c => c.label.startsWith(`kiểm:${key}:`)).length - 1
      const v = seq[Math.min(i, seq.length - 1)]
      return { verdict: v, checks: [{ text: `[TEST_FIXTURE] kiểm ${key}`, passed: v === 'PASS', evidence: 'fixture' }], must_fix: v === 'PASS' ? [] : ['sửa theo fixture'] }
    }
    if (label.startsWith('đồng bộ:')) {
      const op = (prompt.match(/op_id "([^"]+)"/) || [])[1]
      const mode = typeof fx.sync === 'function' ? fx.sync(calls.filter(c => c.label.startsWith('đồng bộ:')).length) : (fx.sync || 'CONFIRMED')
      if (mode === 'NO_PERMISSION') return { status: 'NO_PERMISSION', reason: '[TEST_FIXTURE] phiên không có công cụ ArtifactData' }
      const f = localFile(op)
      mkdirSync(path.dirname(f), { recursive: true })
      writeFileSync(f, JSON.stringify({ op_id: op, test_fixture: true }))
      if (mode === 'WRITE_THEN_KILL') { dead = true; throw new Killed(n) }   // đã ghi tới đích nhưng chết trước khi báo
      return { status: 'CONFIRMED', receipt: `[TEST] ${path.relative(REPO, f)} sha256=${sha(f)}` }
    }
    if (label.startsWith('đối soát:')) {
      const op = (prompt.match(/op_id "([^"]+)"/) || [])[1]
      const f = localFile(op)
      return existsSync(f) ? { status: 'CONFIRMED', receipt: `[TEST] đối soát thấy ${path.relative(REPO, f)} sha256=${sha(f)}` } : { status: 'UNKNOWN', reason: 'chưa thấy' }
    }
    throw new Error(`Harness chưa biết vai: ${label}`)
  }
  async function runCmd(cmd) {
    try {
      const { stdout } = await sh(cmd, { cwd: REPO, shell: '/bin/bash' })
      return { exit_code: 0, stdout }
    } catch (e) { return { exit_code: e.code || 1, stdout: e.stdout || '' } }
  }
  async function parallel(thunks) {
    return Promise.all(thunks.map(t => t().catch(e => { if (e.killed) dead = true; return null })))
  }
  async function pipeline(items, ...stages) {
    return Promise.all(items.map(async (it, i) => { let v = it; for (const s of stages) { try { v = await s(v, it, i) } catch (e) { return null } } return v }))
  }
  const phase = t => { ctx.phase = t }
  const log = m => logs.push(m)
  const budget = { total: null, spent: () => 0, remaining: () => Infinity }
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
  const fn = new AsyncFunction('agent', 'parallel', 'pipeline', 'phase', 'log', 'args', 'budget', 'workflow', 'Date', 'Math', 'require', 'process', body)
  let result, error
  try { result = await fn(agent, parallel, pipeline, phase, log, args, budget, null, guardedDate(), guardedMath(), undefined, undefined) }
  catch (e) { error = e }
  return { meta, result, error: error && { message: error.message, killed: !!error.killed, pending: error.pending }, calls, logs }
}

function localFile(op) { return path.join(REPO, 'runtime', 'hoang_thanh_local', `${op.replace(/[^\w.-]/g, '_')}.json`) }
function sha(f) { return createHash('sha256').update(readFileSync(f)).digest('hex').slice(0, 16) }
