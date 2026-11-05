/* Thử tab TOOL của web Hoàng Thành bằng trình duyệt thật (Playwright) + runtime giả.
   Dùng: node tests/web/tool_tab_test.cjs [thư mục xuất của `python -m tool export`] [thư mục lưu ảnh] */
const { chromium } = require('playwright');
const fs = require('fs'), path = require('path');
const ROOT = path.resolve(__dirname, '..', '..');
const WEB = path.join(ROOT, 'office', 'web');
const seedDir = process.argv[2], shotDir = process.argv[3];
const SKELETON = '<!doctype html><html><head><meta charset=utf8><meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover"><style>:root{color-scheme:light;box-sizing:border-box}body{margin:0;padding:0;font:14px sans-serif;background:#fff;color:#000}img{max-width:100%}[hidden]:not([hidden=until-found i]){display:none!important}</style></head><body>';
let failed = 0;
const check = (name, ok, detail) => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok || !detail ? '' : '  → ' + detail}`); if (!ok) failed++; };

function seed() {
  if (!seedDir) return {};
  const d = path.join(seedDir, 'tool_state'), out = {tool_state: {}};
  for (const f of fs.readdirSync(d)) out.tool_state[f.replace('.json', '')] = JSON.parse(fs.readFileSync(path.join(d, f), 'utf8'));
  return out;
}
async function open(browser, fake, opts = {}) {
  const ctx = await browser.newContext({viewport: opts.viewport || {width: 1280, height: 900}, colorScheme: opts.dark ? 'dark' : 'light', deviceScaleFactor: 1});
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  await page.route('**/*', route => {
    const u = new URL(route.request().url());
    if (u.hostname !== 'tool.test') return route.fulfill({status: 200, contentType: u.pathname.endsWith('.css') || u.hostname.includes('fonts') ? 'text/css' : 'text/javascript', body: ''});
    if (u.pathname === '/') return route.fulfill({status: 200, contentType: 'text/html; charset=utf-8',
      body: SKELETON + `<script>window.__FAKE__=${JSON.stringify(fake)};</script><script>${fs.readFileSync(path.join(__dirname, 'fake_runtime.js'), 'utf8')}</script>` + fs.readFileSync(path.join(WEB, 'index.html'), 'utf8') + '</body></html>'});
    if (u.pathname === '/tool.js') return route.fulfill({status: 200, contentType: 'text/javascript', body: fs.readFileSync(path.join(WEB, 'tool.js'), 'utf8')});
    return route.fulfill({status: 200, contentType: 'text/javascript', body: ''});   // council.js, kho.js: không thuộc phạm vi thử
  });
  await page.goto('http://tool.test/#tool' + (opts.sub ? '/' + opts.sub : ''));
  await page.waitForSelector('#v-tool .tl-sub');
  await page.waitForTimeout(150);
  return {page, errors, ctx};
}
const sub = (page, k) => page.click(`#v-tool [data-sub="${k}"]`);
const text = page => page.locator('#v-tool').innerText();
const overflow = page => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

(async () => {
  const browser = await chromium.launch();
  const S = seed(), seeded = !!seedDir;

  // 1. Trạng thái rỗng: 5 màn hình đều có lời dẫn, không lỗi
  { const {page, errors, ctx} = await open(browser, {seed: {}});
    check('nút TOOL trên thanh chế độ được chọn khi mở #tool', await page.getAttribute('nav.modes [data-mode="tool"]', 'aria-pressed') === 'true');
    check('vùng TOOL hiện, vùng Hoàng thành ẩn', await page.isVisible('#v-tool') && !(await page.isVisible('#v-city')));
    const labels = await page.locator('#v-tool .tl-sub button').allInnerTexts();
    check('đủ 5 màn hình', JSON.stringify(labels) === JSON.stringify(['Kho công cụ', 'Tạo tool từ mẫu', 'Skill TOOL', 'Agent & lịch', 'Nhật ký']), labels.join('|'));
    for (const [k, needle] of [['kho', 'Kho chưa có dữ liệu'], ['tao', 'Chưa có việc nào'], ['skill', 'Chưa có dữ liệu skill'], ['agent', 'Chưa có dữ liệu Agent'], ['nhat-ky', 'Chưa có nhật ký']]) {
      await sub(page, k); check(`trạng thái rỗng: ${k}`, (await text(page)).includes(needle)); }
    check('không lỗi console khi rỗng', errors.length === 0, errors.join(' | '));
    await ctx.close(); }

  // 2. Không đăng nhập (không có db)
  { const {page, ctx} = await open(browser, {noDb: true});
    check('không có db → báo cần đăng nhập', (await text(page)).includes('Cần mở trang trong Claude'));
    await ctx.close(); }

  if (seeded) {
    const tool = S.tool_state.catalog.tools[0];
    const sched = S.tool_state.main.schedule;
    const withTrig = JSON.parse(JSON.stringify(S));
    withTrig.tool_state.main.schedule.trigger_id = 'trig_TEST'; withTrig.tool_state.main.schedule.worker_trigger_id = 'trig_WORKER';
    const {page, errors, ctx} = await open(browser, {seed: withTrig});

    // 3. Kho công cụ: tìm theo chức năng, lọc nhóm, ví dụ, phiên bản, nguồn
    check('kho hiện tool đã phát hành', (await text(page)).includes(tool.id));
    check('thẻ tool có ví dụ đầu vào và đầu ra', await page.locator('#v-tool .tl-tool .tl-io pre').count() >= 2);
    check('thẻ tool có phiên bản, nguồn, nút Chạy thử', ['Phiên bản:', 'Nguồn:', 'Chạy thử'].every(async s => (await text(page)).includes(s)) && (await text(page)).includes('Nguồn:') && (await text(page)).includes('Phiên bản:'));
    await page.fill('#tool-q', 'danh so phu de'); await page.waitForTimeout(80);
    check('tìm không dấu "danh so phu de" ra tool', await page.locator('#v-tool .tl-tool').count() === 1);
    check('ô tìm giữ con trỏ sau khi vẽ lại', await page.evaluate(() => document.activeElement && document.activeElement.id) === 'tool-q');
    await page.fill('#tool-q', 'ke toan thue'); await page.waitForTimeout(80);
    check('tìm sai chức năng → không có kết quả', await page.locator('#v-tool .tl-tool').count() === 0 && (await text(page)).includes('Không có công cụ nào khớp'));
    await page.fill('#tool-q', ''); await page.click('#v-tool [data-act="cat"][data-id="video-image"]');
    check('lọc "Video & ảnh" → không có tool dữ liệu', await page.locator('#v-tool .tl-tool').count() === 0);
    await page.click(`#v-tool [data-act="cat"][data-id="${tool.category}"]`);
    check('lọc đúng nhóm của tool → có', await page.locator('#v-tool .tl-tool').count() === 1);

    // 4. Chạy thử: ghi yêu cầu + gọi worker + hiện kết quả khi worker trả về
    await page.click(`#v-tool [data-act="try"][data-id="${tool.id}"]`);
    const pre = await page.inputValue(`#try-text-${tool.id}`);
    check('ô chạy thử điền sẵn ví dụ', pre.length > 20);
    await page.fill(`#try-text-${tool.id}`, '7\n00:00:01,000 --> 00:00:02,000\nXin chào\n\n19\n00:00:03,000 --> 00:00:04,000\nTạm biệt\n');
    await page.fill(`#try-name-${tool.id}`, 'thu.srt');
    await page.click(`#v-tool [data-act="try-send"][data-id="${tool.id}"]`); await page.waitForTimeout(200);
    const reqs = await page.evaluate(() => window.__STORE__.tool_requests || {});
    const rid = Object.keys(reqs)[0], rq = reqs[rid] || {};
    check('chạy thử tạo yêu cầu kind=run, CHO_WORKER', rq.kind === 'run' && rq.payload.tool === tool.id && rq.payload.filename === 'thu.srt' && rq.payload.input_text.startsWith('7\n'), JSON.stringify(rq).slice(0, 200));
    let calls = await page.evaluate(() => window.__CALLS__);
    const fireCall = calls.mcp.find(c => c.tool === 'fire_trigger');
    check('chạy thử gọi worker qua bộ lập lịch (fire_trigger, dấu WEB: RUN)', !!fireCall && fireCall.server === 'Claude Code Remote' && fireCall.input.trigger_id === 'trig_WORKER' && fireCall.input.text.startsWith('WEB: RUN ' + rid), JSON.stringify(fireCall));
    check('trang báo đang chờ worker', (await text(page)).includes('Chờ worker'));
    await page.evaluate(id => window.__PUT__('tool_requests', id, {state: 'XONG', updated_at: new Date().toISOString(), result: {run_id: 'R-TEST', status: 'OK', outputs: [{name: 'thu.srt', text: '1\n00:00:01,000 --> 00:00:02,000\nXin chào\n\n2\n...'}]}}, true), rid);
    await page.waitForTimeout(120);
    check('kết quả worker hiện trên trang', (await text(page)).includes('R-TEST') && (await page.locator('#v-tool pre.tl-pre').allInnerTexts()).some(t => t.startsWith('1\n00:00:01,000')));

    // 5. Tạo tool từ mẫu: tệp, bỏ tệp bí mật/không hỗ trợ, trả mã việc
    await sub(page, 'tao');
    await page.fill('#tool-prompt', 'Viết tool gộp hai tệp phụ đề thành một');
    await page.fill('#tool-url', 'https://example.com/mau');
    await page.setInputFiles('#tool-pick', [
      {name: 'mau.srt', mimeType: 'application/x-subrip', buffer: Buffer.from('1\n00:00:01,000 --> 00:00:02,000\nChào\n', 'utf8')},
      {name: '.env', mimeType: 'text/plain', buffer: Buffer.from('KEY=abc')},
      {name: 'quay.mov', mimeType: 'video/quicktime', buffer: Buffer.from('xx')},
      {name: 'latin1.txt', mimeType: 'text/plain', buffer: Buffer.from([0xff, 0xfe, 0x41])},
      {name: 'man_hinh.png', mimeType: 'image/png', buffer: Buffer.from([0x89, 0x50, 0x4e, 0x47])}]);
    await page.waitForTimeout(150);
    let t = await text(page);
    check('danh sách: 2 tệp sẽ tải, 3 mục bị bỏ kèm lý do', await page.locator('#v-tool .tl-files .tl-file').count() === 5 && t.includes('tên tệp giống bí mật') && t.includes('MP4 hoặc WebM') && t.includes('không phải văn bản UTF-8'));
    await page.click('#v-tool [data-act="job"]'); await page.waitForTimeout(300);
    t = await text(page);
    const code = (t.match(/TJ-\d{8}-[0-9a-f]{6}/) || [])[0];
    check('bấm "Phân tích & tạo tool" trả mã việc', !!code, t.slice(0, 300));
    const all = await page.evaluate(() => window.__STORE__.tool_requests);
    const jobReq = Object.values(all).find(r => r.kind === 'job');
    check('yêu cầu job mang mã việc, prompt, url, 2 tệp có asset_id, 3 mục bỏ', !!jobReq && jobReq.payload.job_id === code && jobReq.payload.url === 'https://example.com/mau' && jobReq.payload.files.length === 2 && jobReq.payload.files.every(f => /^[0-9a-f]{32}$/.test(f.asset_id)) && jobReq.payload.skipped.length === 3, JSON.stringify(jobReq).slice(0, 300));
    calls = await page.evaluate(() => window.__CALLS__);
    check('tệp văn bản tải lên dạng text/plain, ảnh dạng image/png', calls.uploads.length === 2 && calls.uploads[0].type === 'text/plain' && calls.uploads[1].type === 'image/png', JSON.stringify(calls.uploads));
    check('việc vừa gửi hiện trong "Theo dõi việc"', (await page.locator('#v-tool .tl-item .tl-id').allInnerTexts()).includes(code));
    await page.fill('#tool-prompt', 'ngắn'); await page.click('#v-tool [data-act="job"]'); await page.waitForTimeout(100);
    check('prompt quá ngắn bị chặn với lời hướng dẫn', (await text(page)).includes('ít nhất 8 ký tự'));

    // 6. Skill
    await sub(page, 'skill'); t = await text(page);
    check('Skill TOOL: có bảng nguồn GitHub với giấy phép', t.includes('Nguồn GitHub') && t.includes('Giấy phép') && S.tool_state.main.sources.every(s => t.includes(s.repo || s.id)));
    check('Skill TOOL: mỗi skill có người cấp và kết quả thử', S.tool_state.main.skills.length === 0 || (t.includes('Cấp bởi') && t.includes('Kết quả thử')));

    // 7. Agent & lịch
    await sub(page, 'agent'); await page.waitForTimeout(200); t = await text(page);
    check('hai Agent đồng điều hành hiện đủ', t.includes('Thiên Công') && t.includes('Thiên Công AT') && t.includes('agent-tool-at') && t.includes('Ngang quyền'));
    check('năm nhánh Agent con và giới hạn đồng thời', ['Nghiên cứu GitHub', 'Phân tích mẫu', 'Lập trình', 'Kiểm thử', 'Tích hợp'].every(n => t.includes(n)) && /Đang chạy \d+\/2 Agent con/.test(t));
    check('lịch: phút 05 mỗi giờ, mã đăng ký, lần kế tiếp', t.includes('Phút 05 mỗi giờ') && t.includes('trig_TEST') && t.includes('Lần chạy kế tiếp'));
    calls = await page.evaluate(() => window.__CALLS__);
    check('mở màn hình lịch tự đọc bộ lập lịch khi đã được phép', calls.mcp.some(c => c.tool === 'list_triggers'));
    check('trạng thái lịch lấy từ bộ lập lịch thật: Đang bật', t.includes('Đang bật') && t.includes('Bộ lập lịch báo lúc'));
    await page.click('#v-tool [data-act="at-pause"]'); await page.waitForTimeout(200);
    calls = await page.evaluate(() => window.__CALLS__);
    const upd = calls.mcp.filter(c => c.tool === 'update_trigger').pop();
    check('Tạm dừng → update_trigger enabled=false đúng mã lịch', !!upd && upd.input.trigger_id === 'trig_TEST' && upd.input.enabled === false);
    check('sau tạm dừng: trạng thái Tạm dừng, nút Tiếp tục bật', (await text(page)).includes('Tạm dừng') && !(await page.isDisabled('#v-tool [data-act="at-resume"]')) && await page.isDisabled('#v-tool [data-act="at-pause"]'));
    await page.click('#v-tool [data-act="at-resume"]'); await page.waitForTimeout(200);
    await page.click('#v-tool [data-act="at-run"]'); await page.waitForTimeout(200);
    calls = await page.evaluate(() => window.__CALLS__);
    const run = calls.mcp.filter(c => c.tool === 'fire_trigger').pop();
    check('Chạy ngay → fire_trigger lịch AT với dấu MANUAL (không tính là lượt lịch)', run.input.trigger_id === 'trig_TEST' && run.input.text.startsWith('MANUAL:'));

    // 8. Nhật ký
    await sub(page, 'nhat-ky'); t = await text(page);
    check('Nhật ký: có lượt chạy tool kèm người gọi và bằng chứng', t.includes('Lượt chạy tool') && (S.tool_state.runs.runs.length === 0 || (t.includes('người gọi') && t.includes('Bằng chứng: tool/runs/'))));
    check('Nhật ký: có phát hành/quay lui', t.includes('PHÁT HÀNH'));
    await page.click('#v-tool [data-act="logf"][data-id="bad"]'); await page.waitForTimeout(80);
    const denied = S.tool_state.runs.runs.filter(r => r.status !== 'OK').length;
    check('lọc "Lỗi & từ chối" chỉ còn lượt không đạt', await page.locator('#v-tool .tl-item .p-ok').count() === 0 || denied === 0, 'denied=' + denied);
    check('không lỗi console (bản có dữ liệu)', errors.length === 0, errors.join(' | '));
    if (shotDir) { await sub(page, 'kho'); await page.click('#v-tool [data-act="cat"][data-id=""]'); await page.screenshot({path: path.join(shotDir, 'tool_kho_desktop.png'), fullPage: true});
      await sub(page, 'agent'); await page.waitForTimeout(150); await page.screenshot({path: path.join(shotDir, 'tool_agent_desktop.png'), fullPage: true}); }
    await ctx.close();

    // 9. Điện thoại 390px, giao diện tối: không tràn ngang ở cả 5 màn hình
    { const {page, errors, ctx} = await open(browser, {seed: withTrig}, {viewport: {width: 390, height: 844}, dark: true});
      for (const k of ['kho', 'tao', 'skill', 'agent', 'nhat-ky']) { await sub(page, k); await page.waitForTimeout(120);
        if (k === 'kho') await page.click(`#v-tool [data-act="try"][data-id="${tool.id}"]`);
        const o = await overflow(page); check(`390px không tràn ngang: ${k}`, o <= 0, 'tràn ' + o + 'px');
        if (shotDir && (k === 'kho' || k === 'tao')) await page.screenshot({path: path.join(shotDir, `tool_${k}_mobile_dark.png`), fullPage: true}); }
      check('không lỗi console (điện thoại)', errors.length === 0, errors.join(' | '));
      await ctx.close(); }

    // 10. Thiếu worker: chưa đăng ký lịch / không nối được bộ lập lịch
    { const {page, ctx} = await open(browser, {seed: S, noMcp: true}, {sub: 'agent'});
      const t = await text(page);
      check('chưa có lịch → hiện "Chờ worker" và lệnh kích hoạt', sched.trigger_id ? true : (t.includes('Chờ worker') && t.includes('python -m tool at begin') && t.includes('Thiên Công AT')));
      check('không nối được bộ lập lịch → nút Chạy ngay/Làm mới bị khóa', await page.isDisabled('#v-tool [data-act="at-live"]'));
      await sub(page, 'kho'); await page.click(`#v-tool [data-act="try"][data-id="${tool.id}"]`);
      await page.click(`#v-tool [data-act="try-send"][data-id="${tool.id}"]`); await page.waitForTimeout(200);
      check('chạy thử khi thiếu worker: vẫn ghi yêu cầu và nói rõ đang chờ', Object.keys(await page.evaluate(() => window.__STORE__.tool_requests || {})).length === 1 && (await text(page)).includes('Chờ worker'));
      await ctx.close(); }

    // 11. Người chỉ xem: không gửi được
    { const {page, ctx} = await open(browser, {seed: withTrig, canEdit: false, noAssets: true}, {sub: 'tao'});
      check('người chỉ xem: nút gửi mẫu bị khóa, có giải thích', await page.isDisabled('#v-tool [data-act="job"]') && (await text(page)).includes('quyền sửa'));
      await ctx.close(); }

    // 12. Lỗi bộ lập lịch được nói rõ theo mã lỗi
    { const {page, ctx} = await open(browser, {seed: withTrig, mcpError: 'not_in_manifest', mcpState: 'prompt'}, {sub: 'agent'});
      await page.click('#v-tool [data-act="at-run"]'); await page.waitForTimeout(200);
      check('lỗi quyền lịch có thông báo riêng', (await text(page)).includes('từ chối quyền điều khiển lịch'));
      await ctx.close(); }
  } else console.log('(bỏ qua phần có dữ liệu: chưa truyền thư mục xuất)');

  await browser.close();
  console.log(failed ? `\n${failed} kiểm tra TRƯỢT` : '\nTất cả kiểm tra ĐẠT');
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
