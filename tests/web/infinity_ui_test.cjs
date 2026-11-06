// Kiểm thử giao diện CROWN INFINITY bằng trình duyệt thật (Playwright/Chromium) trên máy chủ thật.
// Tiêu chí 12 (autosave, phục hồi bản nháp, không ghi đè bản phát hành) và 13 (không báo thành công khi API lỗi).
// Chạy: node tests/web/infinity_ui_test.cjs [thư-mục-ảnh]
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..', '..');
const PY = process.env.PYTHON || (process.platform === 'win32' ? 'py' : 'python3');
const PW = 'mat-khau-chu-so-huu-01';
const shots = process.argv[2] || null;
const results = [];
const check = (name, ok, detail = '') => { results.push({ name, ok, detail }); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`); };

async function main() {
  const data = fs.mkdtempSync(path.join(os.tmpdir(), 'ci-ui-'));
  const init = spawnSync(PY, ['-m', 'crown_infinity', '--data-dir', data, 'init-owner', '--username', 'loc', '--password-stdin'], { cwd: ROOT, input: `${PW}\n`, encoding: 'utf8' });
  if (init.status !== 0) throw new Error(init.stderr);
  const srv = spawn(PY, ['-m', 'crown_infinity', '--data-dir', data, 'serve', '--port', '0'], { cwd: ROOT });
  const port = await new Promise((resolve, reject) => {
    srv.stdout.on('data', (b) => { const m = String(b).match(/:(\d+)\//); if (m) resolve(Number(m[1])); });
    srv.on('exit', (c) => reject(new Error(`server exit ${c}`)));
    setTimeout(() => reject(new Error('server timeout')), 15000);
  });
  const base = `http://localhost:${port}`;
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 } });
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message));
    await page.goto(base);
    await page.fill('input[name=username]', 'loc');
    await page.fill('input[name=password]', PW);
    await page.click('#login-form button');
    await page.waitForSelector('.kpi');
    check('Đăng nhập và dashboard hiển thị số liệu', true);

    // Tạo prompt tiếng Việt
    await page.goto(`${base}/#/prompts`);
    await page.click('.lib >> text=+ Prompt mới');
    await page.fill('dialog input >> nth=0', 'Kịch bản chăm sóc khách Etsy');
    await page.click('dialog >> text=Tạo');
    const ed = '.editor textarea.content';
    await page.waitForSelector(ed);
    const status = () => page.textContent('.save-state');
    const waitStatus = (s, timeout = 8000) => page.waitForFunction((x) => (document.querySelector('.save-state') || {}).textContent?.startsWith(x), s, { timeout });
    const text1 = '# Mục tiêu\nTrả lời khách bằng tiếng Việt có dấu: ắ ặ ố ồ đ.\n## Định dạng đầu ra\nMarkdown, tối đa 5 câu.';
    await page.fill(ed, text1);
    await waitStatus('Đã lưu');
    const pid = page.url().split('/').pop();
    check('Autosave lưu bản nháp lên máy chủ', true, pid);

    await page.reload();
    await page.waitForSelector(ed);
    check('Mở lại sau tải lại trang vẫn còn nội dung (đọc từ database)', (await page.inputValue(ed)) === text1);

    // Lưu phiên bản 1 và duyệt
    await page.click('button:text-is("Lưu phiên bản")');
    await page.fill('dialog input', 'Bản đầu được duyệt');
    await page.click('dialog >> button.primary');
    await page.waitForSelector('.version-row');
    await page.click('.version-row button:text-is("Duyệt")');
    await page.waitForSelector('.version-row .b-APPROVED');
    const v1 = await page.evaluate(async (id) => (await (await fetch(`/api/v1/prompts/${id}/versions`)).json()).items[0], pid);
    check('Lưu phiên bản và duyệt v1', v1.version_no >= 1 && v1.review_status === 'APPROVED');

    // Tiêu chí 13: API lỗi → không báo "Đã lưu"
    await page.route('**/api/v1/prompts/*/draft', (r) => r.fulfill({ status: 500, contentType: 'application/json', body: '{"error":{"code":"INTERNAL_ERROR","message":"Lỗi máy chủ"}}' }));
    const text2 = `${text1}\nDòng thêm khi máy chủ lỗi.`;
    await page.fill(ed, text2);
    await waitStatus('Lỗi lưu');
    check('API lỗi 500 → hiển thị "Lỗi lưu", không báo đã lưu', (await status()).startsWith('Lỗi lưu'), await status());
    const cached = await page.evaluate((id) => localStorage.getItem(`ci.draft.${id}`), pid);
    check('Bản nháp được giữ trong trình duyệt khi lưu lỗi', !!cached && JSON.parse(cached).text === text2);

    // Mất mạng hẳn
    await page.unroute('**/api/v1/prompts/*/draft');
    await page.route('**/api/v1/prompts/*/draft', (r) => r.abort('internetdisconnected'));
    await page.fill(ed, `${text2}\nVà khi mất mạng.`);
    await waitStatus('Lỗi lưu');
    check('Mất kết nối → "Lỗi lưu: Mất kết nối…"', (await status()).includes('Mất kết nối'), await status());
    const text3 = await page.inputValue(ed);

    // Tiêu chí 12: tải lại khi bản nháp chưa lên máy chủ → đề nghị khôi phục → lưu thành công
    await page.unroute('**/api/v1/prompts/*/draft');
    page.once('dialog', (d) => d.accept());
    await page.reload();
    await page.waitForSelector('text=Có bản nháp chưa lưu lên máy chủ');
    check('Sau tải lại: hiện thông báo phục hồi bản nháp', true);
    if (shots) await page.screenshot({ path: path.join(shots, 'ci_recovery.png') });
    await page.click('text=Khôi phục bản nháp này');
    await waitStatus('Đã lưu');
    const srvDraft = await page.evaluate(async (id) => (await (await fetch(`/api/v1/prompts/${id}`)).json()).draft_content, pid);
    check('Khôi phục bản nháp và lưu lên máy chủ', srvDraft === text3);
    const v1After = await page.evaluate(async (id) => (await (await fetch(`/api/v1/prompts/${id}/versions`)).json()).items, pid);
    check('Autosave không ghi đè phiên bản APPROVED', v1After.length === 1 && v1After[0].review_status === 'APPROVED' && v1After[0].checksum === v1.checksum);

    // Xung đột hai tab
    const page2 = await ctx.newPage();
    await page2.goto(`${base}/#/prompts/${pid}`);
    await page2.waitForSelector(ed);
    await page2.fill(ed, `${text3}\nTab 2 sửa trước.`);
    await page2.waitForFunction(() => document.querySelector('.save-state').textContent === 'Đã lưu');
    await page.fill(ed, `${text3}\nTab 1 sửa sau.`);
    await page.waitForSelector('text=Xung đột: bản nháp đã được sửa ở nơi khác', { timeout: 8000 });
    check('Hai tab sửa cùng revision → báo xung đột kèm diff', true);
    if (shots) await page.screenshot({ path: path.join(shots, 'ci_conflict.png') });
    const keep = await page.evaluate(async (id) => (await (await fetch(`/api/v1/prompts/${id}`)).json()).draft_content, pid);
    check('Bản của tab 2 không bị ghi đè âm thầm', keep.endsWith('Tab 2 sửa trước.'));
    await page.click('text=Giữ bản của tôi (ghi đè có chủ đích)');
    await waitStatus('Đã lưu');
    const after = await page.evaluate(async (id) => (await (await fetch(`/api/v1/prompts/${id}`)).json()).draft_content, pid);
    check('Chọn "Giữ bản của tôi" → lưu có chủ đích', after.endsWith('Tab 1 sửa sau.'));
    await page2.close();

    // So sánh phiên bản & điền biến
    await page.click('text=Biến & xuất');
    await page.fill(ed, 'Chào {{khach}}, cảm ơn đã mua {{san_pham}}.');
    await waitStatus('Đã lưu');
    await page.click('text=Biến & xuất'); // dựng lại tab với biến mới
    await page.click('text=Dựng prompt');
    await page.waitForSelector('text=Thiếu biến: khach, san_pham');
    check('Dựng prompt thiếu biến → báo rõ, không tự đoán', true);

    // Giao diện điện thoại
    const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, storageState: await ctx.storageState() });
    const mp = await mobile.newPage();
    await mp.goto(`${base}/#/prompts/${pid}`);
    await mp.waitForSelector(ed);
    const overflow = await mp.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check('Màn hình điện thoại 390px không cuộn ngang', overflow <= 0, `lệch ${overflow}px`);
    if (shots) await mp.screenshot({ path: path.join(shots, 'ci_mobile.png') });
    await mobile.close();

    if (shots) {
      const big = await browser.newContext({ viewport: { width: 1360, height: 900 }, deviceScaleFactor: 2, storageState: await ctx.storageState() });
      const bp = await big.newPage();
      await bp.goto(`${base}/#/prompts/${pid}`); await bp.waitForSelector(ed); await bp.waitForTimeout(600);
      await bp.screenshot({ path: path.join(shots, 'ci_studio.png') });
      await bp.goto(`${base}/#/dashboard`); await bp.waitForSelector('.kpi'); await bp.waitForTimeout(300);
      await bp.screenshot({ path: path.join(shots, 'ci_dashboard.png') });
      await bp.goto(`${base}/#/modules`); await bp.waitForSelector('.card h2'); await bp.waitForTimeout(300);
      await bp.screenshot({ path: path.join(shots, 'ci_modules.png'), fullPage: true });
      await big.close();
    }
    check('Không có lỗi JavaScript trên trang', pageErrors.length === 0, pageErrors.join(' | '));
  } catch (e) {
    if (shots) { for (const ctx of browser.contexts()) for (const p of ctx.pages()) await p.screenshot({ path: path.join(shots, 'ci_failure.png') }).catch(() => {}); }
    throw e;
  } finally {
    await browser.close();
    srv.kill('SIGINT');
    fs.rmSync(data, { recursive: true, force: true });
  }
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} kiểm tra đạt`);
  process.exit(failed.length ? 1 : 0);
}

main().catch((e) => { console.error('LỖI', e); process.exit(1); });
