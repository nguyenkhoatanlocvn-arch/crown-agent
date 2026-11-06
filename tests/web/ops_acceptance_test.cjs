// Nghiệm thu "Bộ não điều hành" bằng thao tác thật trên giao diện + máy chủ thật (tiến trình `serve`).
// Chạy: CROWN_INFINITY_GRAPHIFY=<lệnh graphify> node tests/web/ops_acceptance_test.cjs [thư-mục-ảnh]
// Thiếu Graphify → các bước bản đồ ghi SKIPPED (không giả là PASS).
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..', '..');
const PY = process.env.PYTHON || (process.platform === 'win32' ? 'py' : 'python3');
const GRAPHIFY = process.env.CROWN_INFINITY_GRAPHIFY || '';
const PW = 'mat-khau-chu-so-huu-01';
const FIRST = 'Tạm hoãn Voice Studio, Video Studio và SEO Studio. Ưu tiên nền tảng CROWN INFINITY.';
const shots = process.argv[2] || null;
const results = [];
const check = (name, status, detail = '') => { results.push({ name, status, detail }); console.log(`${status} ${name}${detail ? ` — ${detail}` : ''}`); };

function startServer(data) {
  const args = ['-m', 'crown_infinity', '--data-dir', data, 'serve', '--port', '0'];
  if (GRAPHIFY) args.push('--graphify', GRAPHIFY);
  const p = spawn(PY, args, { cwd: ROOT });
  const port = new Promise((resolve, reject) => {
    p.stdout.on('data', (b) => { const m = String(b).match(/:(\d+)\//); if (m) resolve(Number(m[1])); });
    p.on('exit', (c) => reject(new Error(`server exit ${c}`)));
    setTimeout(() => reject(new Error('server timeout')), 20000);
  });
  return { p, port };
}
const stopServer = (p) => new Promise((resolve) => { p.on('exit', (code) => resolve(code)); p.kill('SIGINT'); });

async function login(page, base) {
  await page.goto(`${base}/#/ops`);
  await page.waitForSelector('#login-form:visible, #ops-decisions', { timeout: 15000 });
  if (await page.isVisible('#login-form')) {
    await page.fill('input[name=username]', 'loc'); await page.fill('input[name=password]', PW); await page.click('#login-form button');
  }
  await page.waitForSelector('#ops-decisions h2');
}

async function main() {
  const data = fs.mkdtempSync(path.join(os.tmpdir(), 'ci-ops-'));
  const init = spawnSync(PY, ['-m', 'crown_infinity', '--data-dir', data, 'init-owner', '--username', 'loc', '--display-name', 'Nguyễn Khoa Tấn Lộc', '--password-stdin'], { cwd: ROOT, input: `${PW}\n`, encoding: 'utf8' });
  if (init.status !== 0) throw new Error(init.stderr);
  const head = spawnSync('git', ['-C', ROOT, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).stdout.trim();
  let srv = startServer(data); let port = await srv.port; let base = `http://localhost:${port}`;
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 } });
  const page = await ctx.newPage();
  const errors = []; page.on('pageerror', (e) => errors.push(e.message));
  try {
    // 1. Tạo quyết định trên giao diện
    await login(page, base);
    await page.click('#ops-decisions summary');
    await page.fill('#ops-decisions [aria-label="Tiêu đề quyết định"]', 'Ưu tiên nền tảng CROWN INFINITY');
    await page.fill('#ops-decisions [aria-label="Nội dung quyết định"]', FIRST);
    await page.fill('#ops-decisions [aria-label="Nguồn"]', 'Tin nhắn của Chủ tịch Nguyễn Khoa Tấn Lộc trong phiên Claude Code ngày 06/11/2026');
    await page.click('#ops-decisions >> text=Lưu quyết định');
    await page.waitForSelector(`#ops-decisions >> text=${FIRST}`);
    const badge1 = await page.textContent('#ops-decisions [data-decision] .badge');
    check('Tạo quyết định trên giao diện', badge1.includes('QUYẾT ĐỊNH ĐÃ XÁC NHẬN') ? 'PASS' : 'FAIL', badge1);

    // 2. Khởi động lại dịch vụ rồi đọc lại
    const code = await stopServer(srv.p);
    srv = startServer(data); port = await srv.port; base = `http://localhost:${port}`;
    await login(page, base);
    await page.waitForSelector(`#ops-decisions >> text=${FIRST}`);
    const meta = await page.textContent('#ops-decisions [data-decision] .small');
    check('Khởi động lại dịch vụ (Ctrl+C rồi chạy lại) vẫn đọc được quyết định', 'PASS', `thoát mã ${code}; ${meta.slice(0, 90)}`);

    // 3. Bản đồ Graphify: dựng, tra quan hệ, mở nguồn
    if (GRAPHIFY) {
      await page.click('text=Dựng bản đồ ở commit hiện tại');
      await page.waitForSelector('#ops-graph >> text=/Graphify 0\\.9\\.\\d+ · commit nguồn/', { timeout: 120000 });
      const info = await page.textContent('#ops-graph p.small');
      check('Dựng bản đồ Graphify ở commit hiện tại', info.includes(head.slice(0, 12)) ? 'PASS' : 'FAIL', info.slice(0, 160));
      await page.fill('#ops-graph [aria-label="Tên nút cần tra"]', 'create_backup');
      await page.click('#ops-graph >> text=Tra quan hệ');
      await page.waitForSelector('#ops-graph table');
      const row = page.locator('#ops-graph tbody tr', { hasText: 'calls' }).filter({ hasText: '←' }).first();
      const rowText = (await row.innerText()).replace(/\s+/g, ' ');
      await row.locator('td:last-child button').click();
      await page.waitForSelector('dialog .diff');
      const hl = await page.textContent('dialog .diff .insert');
      const origin = await page.textContent('dialog p.small');
      check('Tra một quan hệ thật có nguồn và mở tệp đối chiếu', hl.includes('create_backup') && origin.includes('git show') ? 'PASS' : 'FAIL',
        `${rowText} | dòng nguồn: ${hl.trim().slice(0, 100)} | ${origin.slice(0, 80)}`);
      if (shots) await page.screenshot({ path: path.join(shots, 'ops_source.png') });
      await page.click('dialog >> text=Đóng');
      // Nút có cả cạnh trích xuất và suy luận: lớp Response (httpcore)
      await page.fill('#ops-graph [aria-label="Tên nút cần tra"]', 'httpcore_response');
      await page.selectOption('#ops-graph [aria-label="Hướng"]', 'in');
      await page.click('#ops-graph >> text=Tra quan hệ');
      await page.waitForSelector('#ops-graph tbody .badge');
      const labels = await page.locator('#ops-graph tbody .badge').allTextContents();
      const inf = page.locator('#ops-graph tbody tr', { hasText: 'INFERRED' }).first();
      const infText = (await inf.innerText()).replace(/\s+/g, ' ');
      await inf.locator('td:last-child button').click();
      await page.waitForSelector('dialog .diff');
      const infLine = await page.textContent('dialog .diff .insert');
      if (shots) await page.screenshot({ path: path.join(shots, 'ops_inferred.png') });
      await page.click('dialog >> text=Đóng');
      check('Hiển thị rõ quan hệ trích xuất (EXTRACTED) và suy luận (INFERRED)', labels.includes('EXTRACTED') && labels.includes('INFERRED') ? 'PASS' : 'FAIL',
        `${labels.filter((l) => l === 'EXTRACTED').length} EXTRACTED, ${labels.filter((l) => l === 'INFERRED').length} INFERRED · ví dụ suy luận: ${infText} → nguồn: "${infLine.trim().slice(0, 90)}"`);
      // Agent dùng token: chỉ đọc đúng nhiệm vụ được cấp
      const agentRes = await page.evaluate(async () => {
        const me = await (await fetch('/api/v1/auth/me')).json();
        const H = { 'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf_token };
        const call = async (m, u, b) => (await fetch(`/api/v1${u}`, { method: m, headers: H, body: b ? JSON.stringify(b) : undefined })).json();
        const projects = await call('GET', '/projects');
        const pid = projects.items[0].id;
        await call('POST', '/agents/sync', {});
        const task = await call('POST', '/tasks', { title: 'A18 đọc bản đồ để đánh giá vùng ảnh hưởng', project_id: pid, assignee: 'A18' });
        await call('POST', '/agent-grants', { agent_id: 'A18', task_id: task.id, permission: 'READ' });
        const tok = await call('POST', '/agents/A18/token', {});
        const A = { Authorization: `Bearer ${tok.token}` };
        const q = await (await fetch(`/api/v1/agent/tasks/${task.id}/graph/query?node=create_backup&direction=in`, { headers: A })).json();
        const rel = q.relations.find((r) => r.relation === 'calls');
        const src = await (await fetch(`/api/v1/agent/tasks/${task.id}/graph/source?path=${encodeURIComponent(rel.evidence.source_file)}&line=${rel.evidence.source_line}`, { headers: A })).json();
        const other = await fetch('/api/v1/agent/tasks/T-khongduoccap/decisions', { headers: A });
        return { rel, line: src.lines.find((l) => l.n === src.line).text, origin: src.origin, otherStatus: other.status };
      });
      check('Agent (token A18) truy vấn quan hệ và mở nguồn trong phạm vi nhiệm vụ', agentRes.line.includes('create_backup') && agentRes.otherStatus === 403 ? 'PASS' : 'FAIL',
        `${agentRes.rel.other.label} --${agentRes.rel.relation}--> create_backup() [${agentRes.rel.confidence}] tại ${agentRes.rel.evidence.source_file}:${agentRes.rel.evidence.source_line}: "${agentRes.line.trim()}" · nhiệm vụ không được cấp → ${agentRes.otherStatus}`);
    } else {
      check('Bản đồ Graphify', 'SKIPPED', 'chưa đặt CROWN_INFINITY_GRAPHIFY');
    }

    // 4. Rà soát bản build
    await page.click('#ops-review button:text-is("Rà soát bản build")');
    await page.waitForSelector('#review-result [data-status]', { timeout: 60000 });
    const st1 = await page.getAttribute('#review-result [data-status]', 'data-status');
    const rep = await page.innerText('#review-result');
    const aiCols = await page.locator('#review-result table').last().locator('tbody td:nth-child(3)').allTextContents();
    check('Bấm "Rà soát bản build" nhận báo cáo', st1 === 'SUCCEEDED' && rep.includes('ĐÃ KIỂM CHỨNG') && rep.includes('CHỈ ĐƯỢC BÁO CÁO') && rep.includes(head.slice(0, 12)) ? 'PASS' : 'FAIL',
      `trạng thái ${st1}; có commit ${head.slice(0, 12)}; gọi AI thật: ${[...new Set(aiCols)].join('/')}`);
    check('Báo cáo có việc tiếp theo và người phụ trách', /Phụ trách/.test(rep) && /(Chủ tịch|A20|A18)/.test(rep) ? 'PASS' : 'FAIL');
    if (shots) await page.screenshot({ path: path.join(shots, 'ops_review.png'), fullPage: true });

    // 5. Chạy lại cùng đầu vào → không chạy trùng
    await page.click('#ops-review button:text-is("Rà soát bản build")');
    await page.waitForSelector('#review-result [data-status="SKIPPED_DUPLICATE"]', { timeout: 60000 });
    const dupText = await page.innerText('#review-result');
    check('Chạy lại cùng commit + cùng đầu vào → bỏ qua, trỏ về báo cáo gốc', dupText.includes('cùng commit và cùng đầu vào') ? 'PASS' : 'FAIL', dupText.split('\n')[0]);

    // 6. Bật lịch 2 giờ (sau khi đã chạy thủ công thành công)
    await page.check('#ops-schedule input[type=checkbox]');
    await page.click('dialog >> button.primary');
    await page.waitForSelector('#ops-schedule >> text=/Lần kế tiếp: \\d/');
    const sch = await page.innerText('#ops-schedule');
    check('Bật lịch mỗi 2 giờ: hiện lần kế tiếp và tình trạng dịch vụ', /ONLINE/.test(sch) ? 'PASS' : 'FAIL', sch.split('\n').filter((l) => /Lần|Dịch vụ|ONLINE|OFFLINE/.test(l)).join(' | ').slice(0, 220));
    if (shots) {
      const big = await browser.newContext({ viewport: { width: 1360, height: 900 }, deviceScaleFactor: 2, storageState: await ctx.storageState() });
      const bp = await big.newPage(); await bp.goto(`${base}/#/ops`); await bp.waitForSelector('#ops-decisions [data-decision]'); await bp.waitForTimeout(800);
      await bp.screenshot({ path: path.join(shots, 'ops_page.png'), fullPage: true }); await big.close();
      const mob = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, storageState: await ctx.storageState() });
      const mp = await mob.newPage(); await mp.goto(`${base}/#/ops`); await mp.waitForSelector('#ops-decisions [data-decision]');
      const ov = await mp.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      check('Màn Điều hành trên điện thoại 390px không cuộn ngang', ov <= 0 ? 'PASS' : 'FAIL', `lệch ${ov}px`); await mob.close();
    }
    check('Không có lỗi JavaScript', errors.length ? 'FAIL' : 'PASS', errors.join(' | '));
  } catch (e) {
    if (shots) await page.screenshot({ path: path.join(shots, 'ops_failure.png') }).catch(() => {});
    check('Kịch bản chạy hết', 'FAIL', String(e).slice(0, 300));
  } finally {
    await browser.close();
    await stopServer(srv.p);
    fs.rmSync(data, { recursive: true, force: true });
  }
  if (shots) fs.writeFileSync(path.join(shots, 'ops_acceptance.json'), JSON.stringify({ head, at: new Date().toISOString(), results }, null, 2));
  const failed = results.filter((r) => r.status === 'FAIL');
  console.log(`\n${results.filter((r) => r.status === 'PASS').length} PASS · ${failed.length} FAIL · ${results.filter((r) => r.status === 'SKIPPED').length} SKIPPED`);
  process.exit(failed.length ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
