// Chạy THẬT workflow agent-vua-vuong-toa từng bước trên sổ runtime/tasks.db (mode LIVE).
// Lệnh sổ chạy trực tiếp; mỗi vai cần Agent (A03 làm, A20 kiểm, đồng bộ, đối soát) dừng lại để
// Agent Vua giao đúng prompt cho Agent thật, ghi câu trả lời vào answers.json rồi chạy lại (takeover).
// node tests/workflow/live_step.mjs runtime/live_run
import { runWorkflow, REPO } from './harness.mjs'
import { readFileSync, writeFileSync, appendFileSync } from 'node:fs'
import path from 'node:path'

const dir = path.resolve(REPO, process.argv[2] || 'runtime/live_run')
const args = { ...JSON.parse(readFileSync(path.join(dir, 'args.json'), 'utf8')), takeover: true }
const r = await runWorkflow(path.join(REPO, '.claude/workflows/agent-vua-vuong-toa.js'), args,
  { live: { answers: path.join(dir, 'answers.json'), pending: path.join(dir, 'pending.json') } })
const line = { pending: r.error?.pending || null, error: r.error && !r.error.pending ? r.error.message : null,
  kind: r.result?.kind || null, agent_calls: r.calls.length,
  roles: r.calls.filter(c => !c.label.startsWith('sổ:')).map(c => `${c.label}${c.answered_by ? ' ← ' + c.answered_by : ''}`), notes: r.logs }
appendFileSync(path.join(dir, 'steps.jsonl'), JSON.stringify(line) + '\n')
if (r.result) writeFileSync(path.join(dir, 'result.json'), JSON.stringify(r.result, null, 2))
console.log(JSON.stringify(line, null, 1))
