/* TOOL — mục Thiên Công của Hoàng Thành Rồng (05/11/2026).
   Năm màn hình: Kho công cụ · Tạo tool từ mẫu · Skill TOOL · Agent & lịch · Nhật ký.
   Trang chỉ HIỂN THỊ trạng thái thật do worker xuất từ repo (collection tool_state) và GHI yêu cầu của Chủ tịch
   (collection tool_requests + tệp mẫu trong kho asset). Trang không chạy mã tool: việc chạy do worker (phiên Claude) làm.
   Lịch: đọc/điều khiển tác vụ định kỳ thật qua connector "Claude Code Remote" khi người xem cho phép. */
(function(){
"use strict";
const root=document.getElementById("v-tool");
if(!root)return;
root.classList.add("btc");

const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const TZ="Asia/Ho_Chi_Minh";
const vnT=x=>{if(!x)return "—";const d=new Date(x);return isNaN(d)?"—":d.toLocaleString("vi-VN",{timeZone:TZ,hour12:false,day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});};
const vnParts=d=>{const p={};new Intl.DateTimeFormat("en-CA",{timeZone:TZ,year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}).formatToParts(d).forEach(x=>p[x.type]=x.value);return p;};
const fold=s=>String(s??"").toLowerCase().normalize("NFD").replace(/đ/g,"d").replace(/[̀-ͯ]/g,"");
const kb=n=>n==null?"—":n<1024?n+" B":n<1048576?(n/1024).toFixed(1).replace(".",",")+" KB":(n/1048576).toFixed(1).replace(".",",")+" MB";
const hex=n=>Array.from(crypto.getRandomValues(new Uint8Array(n)),b=>b.toString(16).padStart(2,"0")).join("");
const nowISO=()=>new Date().toISOString();
const CONNECTOR="Claude Code Remote";

const STYLE=`
#v-tool{display:grid;gap:12px}
#v-tool .tl-head{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline;justify-content:space-between}
#v-tool .tl-head h2{font-family:var(--f-display);font-size:1.5rem;margin:0}
#v-tool .tl-head small{color:var(--muted);font-size:.78rem}
#v-tool .tl-sub{display:flex;flex-wrap:wrap;gap:6px}
#v-tool .tl-sub button,#v-tool .tl-chip{font:inherit;font-size:.82rem;padding:6px 12px;min-height:36px;border-radius:999px;border:1px solid var(--line);background:var(--panel);color:var(--ink);cursor:pointer}
#v-tool .tl-sub button[aria-pressed="true"]{background:var(--teal);border-color:var(--teal);color:var(--bg);font-weight:600}
#v-tool .tl-chip[aria-pressed="true"]{border-color:var(--gold);color:var(--gold);font-weight:600}
#v-tool .tl-kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px}
#v-tool .tl-kpi div{border:1px solid var(--line);border-radius:8px;padding:8px 10px;background:var(--panel);min-width:0}
#v-tool .tl-kpi b{display:block;font-family:var(--f-display);font-size:1.7rem;line-height:1.05;font-variant-numeric:tabular-nums}
#v-tool .tl-kpi span{font-size:.74rem;color:var(--muted)}
#v-tool .tl-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
#v-tool .tl-in{font:inherit;font-size:.92rem;min-height:40px;padding:6px 10px;border-radius:8px;border:1px solid var(--line);background:var(--panel);color:var(--ink);min-width:0;max-width:100%;box-sizing:border-box}
#v-tool textarea.tl-in{width:100%;min-height:96px;resize:vertical;line-height:1.45}
#v-tool .tl-search{flex:1 1 260px}
#v-tool .tl-btn{font:inherit;font-size:.86rem;font-weight:600;min-height:40px;padding:0 16px;border-radius:8px;border:1px solid var(--line);background:var(--panel);color:var(--ink);cursor:pointer}
#v-tool .tl-btn.go{background:var(--teal);border-color:var(--teal);color:var(--bg)}
#v-tool .tl-btn.warn{color:var(--bad);border-color:var(--bad)}
#v-tool .tl-btn[disabled]{opacity:.5;cursor:not-allowed}
#v-tool .tl-tool{border:1px solid var(--line);border-radius:10px;background:var(--panel);padding:12px 14px;display:grid;gap:8px;min-width:0}
#v-tool .tl-tool h3{margin:0;font-family:var(--f-display);font-size:1.25rem;display:flex;flex-wrap:wrap;gap:6px 10px;align-items:baseline}
#v-tool .tl-id{font-family:var(--f-pixel);font-size:1.05rem;color:var(--muted)}
#v-tool .tl-meta{font-size:.78rem;color:var(--muted);display:flex;flex-wrap:wrap;gap:4px 14px}
#v-tool .tl-meta b{color:var(--ink);font-weight:500}
#v-tool .tl-pill{font-size:.7rem;font-weight:600;padding:1px 8px;border-radius:999px;border:1px solid currentColor;white-space:nowrap;letter-spacing:.03em}
#v-tool .p-ok{color:#2f8f5b}#v-tool .p-run{color:var(--ok)}#v-tool .p-wait{color:var(--wait)}#v-tool .p-warn{color:var(--warn)}#v-tool .p-bad{color:var(--bad)}#v-tool .p-off{color:var(--muted)}
#v-tool .tl-io{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:8px}
#v-tool .tl-io figure{margin:0;min-width:0}
#v-tool .tl-io figcaption{font-size:.72rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:3px}
#v-tool pre.tl-pre{margin:0;max-height:230px;overflow:auto;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font:12.5px/1.45 ui-monospace,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere}
#v-tool .tl-cmd{display:flex;gap:6px;align-items:stretch;min-width:0}
#v-tool .tl-cmd code{flex:1;min-width:0;overflow-x:auto;white-space:nowrap;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font:12.5px ui-monospace,Consolas,monospace}
#v-tool .tl-drop{border:2px dashed var(--line);border-radius:10px;padding:16px;text-align:center;color:var(--muted);background:var(--bg);font-size:.9rem}
#v-tool .tl-drop.over{border-color:var(--gold);color:var(--ink)}
#v-tool .tl-files{display:grid;gap:4px;font-size:.82rem}
#v-tool .tl-file{display:grid;grid-template-columns:1fr auto auto;gap:4px 10px;align-items:center;padding:4px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);min-width:0}
#v-tool .tl-file span:first-child{overflow-wrap:anywhere;min-width:0}
#v-tool .tl-file button{font:inherit;font-size:.78rem;border:0;background:none;color:var(--bad);cursor:pointer;min-height:28px}
#v-tool .tl-code{font-family:var(--f-pixel);font-size:1.5rem;letter-spacing:.04em;color:var(--gold)}
#v-tool .tl-steps{display:flex;flex-wrap:wrap;gap:4px}
#v-tool .tl-step{font-size:.7rem;padding:1px 7px;border-radius:4px;border:1px solid var(--line);color:var(--muted);white-space:nowrap}
#v-tool .tl-step.DONE{border-color:#2f8f5b;color:#2f8f5b}#v-tool .tl-step.SKIPPED{border-style:dashed}
#v-tool .tl-step.RUNNING{border-color:var(--ok);color:var(--ok);font-weight:600}#v-tool .tl-step.FAILED{border-color:var(--bad);color:var(--bad)}
#v-tool .tl-item{border-top:1px solid var(--line);padding:8px 0;display:grid;gap:4px;min-width:0}
#v-tool .tl-item:first-child{border-top:0}
#v-tool .tl-two{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
#v-tool .tl-note{font-size:.82rem;border-left:3px solid var(--gold);padding:6px 10px;background:var(--bg);border-radius:0 8px 8px 0}
#v-tool .tl-err{color:var(--bad);font-size:.82rem}
#v-tool .tl-empty{color:var(--muted);font-size:.9rem;padding:8px 0}
#v-tool details>summary{cursor:pointer;font-size:.82rem;color:var(--muted)}
#v-tool label.tl-f{display:grid;gap:3px;font-size:.78rem;color:var(--muted);min-width:0}
`;
const st=document.createElement("style");st.textContent=STYLE;document.head.appendChild(st);

/* ---------- định nghĩa ---------- */
const SUBS=[["kho","Kho công cụ"],["tao","Tạo tool từ mẫu"],["skill","Skill TOOL"],["agent","Agent & lịch"],["nhat-ky","Nhật ký"]];
const CATS=[["","Tất cả"],["video-image","Video & ảnh"],["data-files","Dữ liệu & tệp"],["web-app","Web & ứng dụng"],["automation","Tự động hóa"],["testing","Kiểm thử"]];
const JOB_STATE={READY:["Chờ người làm","p-wait"],RUNNING:["Đang làm","p-run"],BLOCKED:["Bị chặn","p-bad"],WAITING_APPROVAL:["Chờ duyệt","p-warn"],FAILED:["Thất bại","p-bad"],VERIFIED_DONE:["Đã xong, đã kiểm","p-ok"],CANCELLED:["Đã hủy","p-off"]};
const REQ_STATE={CHO_WORKER:["Chờ worker","p-wait"],DA_NHAN:["Worker đã nhận","p-run"],DANG_XU_LY:["Đang xử lý","p-run"],XONG:["Xong","p-ok"],LOI:["Lỗi","p-bad"],HUY:["Đã hủy","p-off"]};
const RUN_STATE={OK:["Đạt","p-ok"],ERROR:["Lỗi","p-bad"],DENIED:["Bị từ chối","p-bad"],TIMEOUT:["Quá giờ","p-warn"]};
const TRIG={scheduler:["LỊCH","p-ok","do bộ lập lịch kích hoạt"],manual:["TAY","p-warn","chạy tay"],web:["WEB","p-wait","lệnh từ web"],test:["THỬ","p-off","ca thử"]};
const SESS={NO_CHANGE:"Không có thay đổi",RELEASED:"Đã phát hành",ROLLED_BACK:"Đã khôi phục bản tốt",WORKED:"Đã làm việc",NO_RELEASE:"Không phát hành",SKIPPED_OVERLAP:"Bỏ qua: phiên trước còn chạy",PAUSED:"Lịch đang tạm dừng",ERROR:"Lỗi"};
const STEP_NAME={intake:"Nhận mẫu",research:"Tìm nguồn",analyze:"Phân tích",spec:"Đặc tả",build:"Lập trình",test:"Kiểm thử",publish:"Phát hành",verify:"Gọi thử"};
const TEXT_EXT=new Set("srt vtt ass txt md csv tsv json xml html htm css js ts jsx tsx py sh bat ps1 yaml yml ini toml log sql java c cpp h go rs php rb lua".split(" "));
const MEDIA={mp4:"video/mp4",webm:"video/webm",png:"image/png",jpg:"image/jpeg",jpeg:"image/jpeg",gif:"image/gif",webp:"image/webp",svg:"image/svg+xml",pdf:"application/pdf"};
const SECRET=[/^\.env/i,/\.key$/i,/\.pem$/i,/\.pfx$/i,/\.p12$/i,/\.kdbx$/i,/^auth.*\.json$/i,/secret/i,/cookie/i,/credential/i,/passw/i,/^id_rsa/i,/^id_ed25519/i,/^wallet.*\.dat$/i,/api_?key/i,/\.token$/i];
const MAX_FILE=20*1048576,MAX_FILES=200,MAX_INLINE=200*1024;

const S={db:null,canEdit:false,conn:"loading",main:null,catalog:null,jobs:null,runs:null,requests:[],sub:"kho",q:"",cat:"",
  open:{},busy:new Set(),err:{},note:{},files:[],skipped:[],lastJob:null,assets:null,mcp:null,perms:null,live:null,liveAt:null,liveErr:null,logFilter:"all"};
const subFromHash=()=>{const h=location.hash.slice(1).split("/");return h[0]==="tool"&&SUBS.some(s=>s[0]===h[1])?h[1]:null;};
S.sub=subFromHash()||S.sub;

/* ---------- tiện ích hiển thị ---------- */
const pill=(t,c)=>`<span class="tl-pill ${c||"p-off"}">${esc(t)}</span>`;
const card=(title,body,extra)=>`<section class="card"><h2>${esc(title)}${extra||""}</h2>${body}</section>`;
const btn=(act,id,label,cls,dis)=>`<button type="button" class="tl-btn ${cls||""}" data-act="${esc(act)}" data-id="${esc(id||"")}" ${dis||S.busy.has(act+":"+(id||""))?"disabled":""}>${esc(label)}</button>`;
const errOf=k=>S.err[k]?`<div class="tl-err" role="alert">${esc(S.err[k])}</div>`:"";
const noteOf=k=>S.note[k]?`<div class="tl-note">${S.note[k]}</div>`:"";
const copyBox=(text,id)=>`<div class="tl-cmd"><code id="${esc(id)}">${esc(text)}</code><button type="button" class="tl-btn" data-act="copy" data-id="${esc(id)}">Chép</button></div>`;
const sched=()=>(S.main&&S.main.schedule)||{};
const reqOfJob=jid=>S.requests.find(r=>r.kind==="job"&&r.payload&&r.payload.job_id===jid);

/* ---------- 1. Kho công cụ ---------- */
function vKho(){
  const tools=(S.catalog&&S.catalog.tools)||[];
  const words=fold(S.q).split(/[^0-9a-z]+/).filter(Boolean);
  const hit=t=>{if(S.cat&&t.category!==S.cat)return false;if(!words.length)return true;
    const hay=fold([t.id,t.name,t.summary].concat(t.keywords||[]).join(" "));return words.every(w=>hay.includes(w));};
  const shown=tools.filter(hit);
  const count=c=>tools.filter(t=>!c||t.category===c).length;
  let h=`<div class="tl-row"><input class="tl-in tl-search" id="tool-q" type="search" placeholder="Tìm theo chức năng, ví dụ: đánh số phụ đề" value="${esc(S.q)}" aria-label="Tìm công cụ theo chức năng">
    ${CATS.map(([k,l])=>`<button type="button" class="tl-chip" data-act="cat" data-id="${k}" aria-pressed="${S.cat===k}">${esc(l)} ${count(k)}</button>`).join("")}</div>`;
  if(!S.catalog)h+=`<section class="card tl-empty">Kho chưa có dữ liệu. Khi worker xuất trạng thái lần đầu, mỗi công cụ sẽ hiện ở đây cùng ví dụ đầu vào, đầu ra, phiên bản, nguồn và nút Chạy thử.</section>`;
  else if(!shown.length)h+=`<section class="card tl-empty">${tools.length?"Không có công cụ nào khớp. Thử từ khóa khác hoặc bỏ lọc nhóm.":"Kho chưa có công cụ nào được phát hành. Vào “Tạo tool từ mẫu” để gửi mẫu đầu tiên."}</section>`;
  shown.forEach(t=>{h+=toolCard(t);});
  return h;
}
function toolCard(t){
  const ex=(t.examples||[])[0],rel=t.release||{},open=S.open["try:"+t.id];
  const firstIn=ex&&Object.values(ex.inputs||{})[0],firstOut=ex&&Object.values(ex.expected||{})[0];
  const src=t.source||{},tp=(src.third_party||[]);
  let h=`<article class="tl-tool"><h3>${esc(t.name)} <span class="tl-id">${esc(t.id)}${t.current_version?"@"+esc(t.current_version):""}</span> ${pill(t.category_name,"p-wait")} ${t.callable?pill("Gọi được","p-ok"):pill("Đã thu hồi","p-bad")}</h3>
    <div>${esc(t.summary)}</div>
    <div class="tl-meta"><span>Phiên bản: ${(t.versions||[]).map(v=>`<b>${esc(v.version)}</b> ${v.status==="GOOD"?"tốt":"đã thu hồi"}`).join(" · ")}</span>
      ${rel.published_by?`<span>Phát hành: <b>${esc(rel.published_by)}</b> ${vnT(rel.published_at)}${rel.auto?" (tự động)":""}</span>`:""}
      ${rel.test?`<span>Ca thử: <b>${rel.test.cases.filter(c=>c.ok).length}/${rel.test.cases.length} đạt</b>${rel.tester?` · kiểm: ${esc(rel.tester)}`:""}${rel.builder?` · viết: ${esc(rel.builder)}`:""}</span>`:""}
      ${t.cost_usd_per_run?`<span>Chi phí: <b>${esc(t.cost_usd_per_run)} USD/lượt</b></span>`:`<span>Chi phí: <b>0</b></span>`}</div>
    <div class="tl-meta"><span>Nguồn: ${src.job_id?`việc <b>${esc(src.job_id)}</b>`:esc(src.kind||"—")}${src.sample?` · mẫu ${esc(src.sample.split("/").pop())}`:""}${src.spec?` · đặc tả ${esc(src.spec.split("/").pop())}`:""}${tp.length?" · mã bên thứ ba: "+tp.map(x=>`${esc(x.url)} @${esc(String(x.commit).slice(0,7))} (${esc(x.license)})`).join("; "):" · không dùng mã bên thứ ba"}</span>
      ${t.permissions?`<span>Quyền: mạng <b>${t.permissions.network?"có":"không"}</b>, lệnh ngoài <b>${(t.permissions.subprocess||[]).length?esc(t.permissions.subprocess.join(", ")):"không"}</b></span>`:""}</div>`;
  if(ex)h+=`<div class="tl-io"><figure><figcaption>Ví dụ đầu vào · ${esc(firstIn?firstIn.name:"")}</figcaption><pre class="tl-pre">${esc(firstIn&&firstIn.text!=null?firstIn.text:(firstIn&&firstIn.note)||"—")}</pre></figure>
      <figure><figcaption>Ví dụ đầu ra · ${esc(firstOut?firstOut.name:"")}</figcaption><pre class="tl-pre">${esc(firstOut&&firstOut.text!=null?firstOut.text:(firstOut&&firstOut.note)||"—")}</pre></figure></div>`;
  if(t.call)h+=`<details><summary>Cách một Agent gọi công cụ này</summary>${copyBox(t.call,"call-"+t.id)}</details>`;
  h+=`<div class="tl-row">${btn("try",t.id,open?"Đóng chạy thử":"Chạy thử","go",!t.callable)}
    ${t.callable&&S.canEdit?btn("rb-open",t.id,"Thu hồi bản hiện hành","warn"):""}</div>`;
  if(open)h+=tryPanel(t,firstIn);
  if(S.open["rb:"+t.id])h+=`<div class="tl-item"><label class="tl-f">Lý do thu hồi ${esc(t.id)}@${esc(t.current_version)} (bản tốt trước đó sẽ thành hiện hành)
      <input class="tl-in" id="rb-reason-${esc(t.id)}" type="text" placeholder="vd: kết quả sai với tệp CRLF"></label>
      <div class="tl-row">${btn("rb-send",t.id,"Gửi lệnh thu hồi","warn")}</div>${errOf("rb:"+t.id)}${noteOf("rb:"+t.id)}</div>`;
  const reqs=S.requests.filter(r=>(r.kind==="run"||r.kind==="rollback")&&r.payload&&r.payload.tool===t.id).slice(0,3);
  if(reqs.length)h+=`<div>${reqs.map(reqLine).join("")}</div>`;
  return h+`</article>`;
}
function tryPanel(t,firstIn){
  const inp=(t.interface&&t.interface.inputs&&t.interface.inputs[0])||{name:"input"};
  const def=S.open["tryText:"+t.id];
  const text=def!=null?def:(firstIn&&firstIn.text)||"";
  const name=S.open["tryName:"+t.id]||(firstIn&&firstIn.name)||("dau_vao"+((inp.ext||[".txt"])[0]));
  return `<div class="tl-item"><label class="tl-f">Dữ liệu chạy thử cho đầu vào “${esc(inp.name)}” (sửa trực tiếp, hoặc chọn một tệp văn bản dưới ${MAX_INLINE/1024} KB)
      <textarea class="tl-in" id="try-text-${esc(t.id)}" data-draft="tryText:${esc(t.id)}" spellcheck="false">${esc(text)}</textarea></label>
    <div class="tl-row"><label class="tl-f">Tên tệp<input class="tl-in" id="try-name-${esc(t.id)}" data-draft="tryName:${esc(t.id)}" type="text" value="${esc(name)}"></label>
      <label class="tl-btn" style="display:inline-flex;align-items:center">Chọn tệp…<input type="file" data-act="try-file" data-id="${esc(t.id)}" hidden></label>
      ${btn("try-send",t.id,"Chạy thử với dữ liệu này","go",!S.canEdit)}</div>
    ${S.canEdit?"":`<div class="tl-err">Chỉ chủ trang hoặc người có quyền sửa mới gửi được lượt chạy thử.</div>`}${errOf("try:"+t.id)}${noteOf("try:"+t.id)}</div>`;
}
function reqLine(r){
  const stt=REQ_STATE[r.state]||[r.state,"p-off"],res=r.result||{};
  let h=`<div class="tl-item"><div class="tl-meta"><span>${pill(stt[0],stt[1])} <b>${esc(r._id)}</b> · ${r.kind==="run"?"chạy thử":r.kind==="rollback"?"thu hồi":"tạo tool"} · gửi ${vnT(r.created_at)}${r.updated_at?` · cập nhật ${vnT(r.updated_at)}`:""}</span>
    ${res.run_id?`<span>Lượt chạy <b>${esc(res.run_id)}</b> · ${esc((RUN_STATE[res.status]||[res.status])[0])}</span>`:""}${res.job_id?`<span>Mã việc <b>${esc(res.job_id)}</b></span>`:""}</div>`;
  if(r.state==="CHO_WORKER")h+=waitWorker(r);
  if(res.error)h+=`<div class="tl-err">${esc(res.error)}</div>`;
  (res.outputs||[]).forEach(o=>{h+=`<figure style="margin:0"><figcaption class="tl-meta">Kết quả · ${esc(o.name)}</figcaption><pre class="tl-pre">${esc(o.text!=null?o.text:"(tệp nhị phân)")}</pre></figure>`;});
  if(res.note)h+=`<div class="tl-meta"><span>${esc(res.note)}</span></div>`;
  return h+`</div>`;
}
function waitWorker(r){
  const fired=r.fired_at?`Đã gọi worker lúc ${vnT(r.fired_at)}. Kết quả thường về sau 1–3 phút.`:"Chưa gọi được worker ngay.";
  const sch=sched(),next=nextRunText();
  return `<div class="tl-note"><b>Chờ worker.</b> ${esc(fired)} ${sch.trigger_id?`Nếu không, phiên Thiên Công AT kế tiếp (${esc(next)}) sẽ xử lý.`:"Chưa có lịch nào đăng ký."}
    ${S.mcp&&(sch.worker_trigger_id||sch.trigger_id)&&S.canEdit?btn("refire",r._id,"Gọi worker ngay"):""}${errOf("fire:"+r._id)}</div>`;
}

/* ---------- 2. Tạo tool từ mẫu ---------- */
function classify(f,path){
  const name=f.name,ext=(name.split(".").pop()||"").toLowerCase();
  if(SECRET.some(re=>re.test(name)))return {skip:"tên tệp giống bí mật — không tải lên"};
  if(f.size===0)return {skip:"tệp rỗng"};
  if(MEDIA[ext]){if(f.size>(ext==="svg"?2*1048576:MAX_FILE))return {skip:`quá ${ext==="svg"?"2":"20"} MB`};
    return {type:MEDIA[ext],kind:ext==="mp4"||ext==="webm"?"video":ext==="pdf"?"file":"image"};}
  if(["mov","mkv","avi","m4v","wmv"].includes(ext))return {skip:"web chỉ nhận video MP4 hoặc WebM — đổi định dạng hoặc gửi qua chat"};
  if(ext==="zip")return {zip:true};
  if(TEXT_EXT.has(ext)){if(f.size>MAX_FILE)return {skip:"quá 20 MB"};return {type:"text/plain",kind:"file",text:true};}
  return {skip:"định dạng web chưa nhận — gửi qua chat"};
}
async function addFiles(list){
  for(const it of list){
    const f=it.file||it,path=it.path||f.webkitRelativePath||f.name;
    if(S.files.length>=MAX_FILES){S.skipped.push({name:path,why:`vượt ${MAX_FILES} tệp mỗi lần gửi`});continue;}
    const c=classify(f,path);
    if(c.zip){await addZip(f);continue;}
    if(c.skip){S.skipped.push({name:path,why:c.skip});continue;}
    if(c.text){try{new TextDecoder("utf-8",{fatal:true}).decode(await f.arrayBuffer());}catch(_){S.skipped.push({name:path,why:"không phải văn bản UTF-8"});continue;}}
    if(S.files.some(x=>x.path===path&&x.file.size===f.size))continue;
    S.files.push({file:f,path,type:c.type,kind:c.kind,state:"cho"});
  }
  render();
}
let zipLoading=null;
function loadZip(){if(window.JSZip)return Promise.resolve(true);if(zipLoading)return zipLoading;
  zipLoading=new Promise(res=>{const s=document.createElement("script");s.src="https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js";s.onload=()=>res(true);s.onerror=()=>res(false);document.head.appendChild(s);});
  return zipLoading;}
async function addZip(f){
  if(!(await loadZip())){S.skipped.push({name:f.name,why:"không mở được ZIP trên web — gửi qua chat"});return;}
  try{const z=await window.JSZip.loadAsync(f);const entries=Object.values(z.files).filter(e=>!e.dir);
    if(entries.length>MAX_FILES){S.skipped.push({name:f.name,why:`ZIP có ${entries.length} tệp, vượt ${MAX_FILES}`});return;}
    const inner=[];for(const e of entries){if(e.name.includes("..")){S.skipped.push({name:e.name,why:"đường dẫn trong ZIP không hợp lệ"});continue;}
      const blob=await e.async("blob");inner.push({file:new File([blob],e.name.split("/").pop()),path:f.name.replace(/\.zip$/i,"")+"/"+e.name});}
    await addFiles(inner);
  }catch(_){S.skipped.push({name:f.name,why:"ZIP hỏng hoặc có mật khẩu"});}
}
async function walkEntry(entry,prefix,out){
  if(entry.isFile){await new Promise(r=>entry.file(f=>{out.push({file:f,path:prefix+f.name});r();},()=>r()));}
  else if(entry.isDirectory){const rd=entry.createReader();let batch;
    do{batch=await new Promise(r=>rd.readEntries(r,()=>r([])));for(const e of batch)await walkEntry(e,prefix+entry.name+"/",out);}while(batch.length);}
}
function vTao(){
  const total=S.files.reduce((a,f)=>a+f.file.size,0),busy=S.busy.has("job:");
  let h=card("Gửi mẫu",`
    <label class="tl-f">Anh muốn công cụ làm gì?<textarea class="tl-in" id="tool-prompt" data-draft="prompt" placeholder="vd: Viết tool đánh lại số phụ đề từ 1, giữ nguyên timecode và lời thoại">${esc(S.open.prompt||"")}</textarea></label>
    <label class="tl-f" style="margin-top:8px">Đường dẫn tham khảo (không bắt buộc)<input class="tl-in" id="tool-url" data-draft="url" type="url" inputmode="url" placeholder="https://…" value="${esc(S.open.url||"")}"></label>
    <div class="tl-drop" id="tool-drop" style="margin-top:8px" tabindex="0">Kéo thả hoặc dán video, ảnh, tệp, ZIP hay cả thư mục vào đây
      <div class="tl-row" style="justify-content:center;margin-top:8px">
        <label class="tl-btn" style="display:inline-flex;align-items:center">Chọn tệp<input id="tool-pick" type="file" multiple hidden></label>
        <label class="tl-btn" style="display:inline-flex;align-items:center">Chọn ZIP<input id="tool-zip" type="file" accept=".zip" hidden></label>
        <label class="tl-btn" style="display:inline-flex;align-items:center">Chọn cả thư mục<input id="tool-dir" type="file" webkitdirectory multiple hidden></label></div></div>
    ${S.files.length?`<div class="tl-files" style="margin-top:8px">${S.files.map((f,i)=>`<div class="tl-file"><span>${esc(f.path)}</span><span class="tl-meta">${esc(kb(f.file.size))} · ${f.state==="xong"?"đã tải lên":f.state==="dang"?"đang tải…":f.state==="loi"?"lỗi tải":"sẽ tải lên"}</span>${busy?"<span></span>":`<button type="button" data-act="rm-file" data-id="${i}" aria-label="Bỏ ${esc(f.path)}">Bỏ</button>`}</div>`).join("")}
      <div class="tl-meta"><span>${S.files.length} tệp · ${esc(kb(total))}</span></div></div>`:""}
    ${S.skipped.length?`<details style="margin-top:6px" open><summary>${S.skipped.length} mục không tải lên được</summary><div class="tl-files">${S.skipped.map(s=>`<div class="tl-file"><span>${esc(s.name)}</span><span class="tl-meta">${esc(s.why)}</span><span></span></div>`).join("")}</div></details>`:""}
    ${S.assets||!S.files.length?"":`<div class="tl-err" style="margin-top:6px">Phiên xem này không tải tệp lên được (cần quyền sửa trang). Anh vẫn gửi được prompt và đường dẫn.</div>`}
    <div class="tl-row" style="margin-top:10px">${btn("job","","Phân tích & tạo tool","go",!S.canEdit)}
      <span class="tl-meta"><span>Mẫu của anh được lưu riêng tư trong trang này, không đưa vào kho mã.</span></span></div>
    ${S.canEdit?"":`<div class="tl-err">Chỉ chủ trang hoặc người có quyền sửa mới gửi được mẫu.</div>`}${errOf("job:")}
    ${S.lastJob?`<div class="tl-note" style="margin-top:8px">Mã việc của anh: <span class="tl-code">${esc(S.lastJob)}</span><br>Theo dõi ngay bên dưới. ${S.note["job:"]||""}</div>`:""}`);
  const jobs=(S.jobs&&S.jobs.jobs)||[],known=new Set(jobs.map(j=>j.job_id));
  const pending=S.requests.filter(r=>r.kind==="job"&&r.payload&&!known.has(r.payload.job_id)&&r.state!=="HUY");
  let body="";
  pending.forEach(r=>{const stt=REQ_STATE[r.state]||[r.state,"p-off"];
    body+=`<div class="tl-item"><div class="tl-row"><span class="tl-id">${esc(r.payload.job_id)}</span>${pill(stt[0],stt[1])}<span>${esc((r.payload.prompt||"").slice(0,120))}</span></div>
      <div class="tl-meta"><span>Gửi ${vnT(r.created_at)} · ${(r.payload.files||[]).length} tệp${r.payload.url?" · 1 đường dẫn":""}</span></div>${r.state==="CHO_WORKER"?waitWorker(r):""}</div>`;});
  jobs.forEach(j=>{body+=jobItem(j);});
  h+=card("Theo dõi việc",body||`<div class="tl-empty">Chưa có việc nào. Việc anh gửi sẽ hiện ở đây với mã việc, tám bước và bằng chứng từng bước.</div>`);
  return h;
}
function jobItem(j){
  const stt=JOB_STATE[j.state]||[j.state,"p-off"],open=S.open["job:"+j.job_id];
  let h=`<div class="tl-item"><div class="tl-row"><span class="tl-id">${esc(j.job_id)}</span>${pill(stt[0],stt[1])}<span>${esc(j.title)}</span>
    ${j.tool_id?`<span class="tl-meta"><span>→ <b>${esc(j.tool_id)}${j.version?"@"+esc(j.version):""}</b></span></span>`:""}</div>
    <div class="tl-steps">${(j.steps||[]).map(s=>`<span class="tl-step ${esc(s.status)}" title="${esc(s.what||"")}">${esc(STEP_NAME[s.id]||s.id)}</span>`).join("")}</div>
    <div class="tl-meta"><span>Giao cho <b>${esc(j.assigned_to)}</b>${j.holder?` · đang giữ quyền ghi: <b>${esc(j.holder)}</b> tới ${vnT(j.lease_until)}`:""}${(j.workers||[]).length>1?` · đã qua tay: ${esc(j.workers.join(" → "))}`:""}</span><span>Cập nhật ${vnT(j.updated_at)}</span>
      <button type="button" class="tl-chip" data-act="job-open" data-id="${esc(j.job_id)}" aria-expanded="${!!open}">${open?"Thu gọn":"Xem bước và bằng chứng"}</button></div>`;
  if(open){h+=`<div class="tl-meta"><span>Yêu cầu: ${esc(j.prompt)}</span></div><div class="tw"><table><thead><tr><th>Bước</th><th>Trạng thái</th><th>Ai làm</th><th>Bằng chứng / ghi chú</th></tr></thead><tbody>
      ${(j.steps||[]).map(s=>`<tr><td>${esc(STEP_NAME[s.id]||s.id)}</td><td>${esc(s.status)}</td><td>${esc(s.by||"—")}${s.sub?"<br>"+esc(s.sub):""}</td><td>${(s.evidence||[]).map(esc).join("<br>")||""}${s.note?`<div class="muted">${esc(s.note)}</div>`:""}</td></tr>`).join("")}</tbody></table></div>
      <details><summary>${j.events_total||0} sự kiện (xem ${Math.min(6,(j.events_tail||[]).length)} gần nhất)</summary>${(j.events_tail||[]).map(e=>`<div class="tl-meta"><span>${vnT(e.at)} · <b>${esc(e.actor)}</b> · ${esc(e.action)} · ${esc(e.detail)}</span></div>`).join("")}</details>`;}
  return h+`</div>`;
}

/* ---------- 3. Skill TOOL ---------- */
function vSkill(){
  const M=S.main;if(!M)return `<section class="card tl-empty">Chưa có dữ liệu skill. Mỗi skill sẽ hiện kèm người cấp, phiên bản, kết quả thử và bản trước để quay lui.</section>`;
  const sk=M.skills||[],src=M.sources||[];
  let h=`<div class="tl-kpi"><div><b>${sk.filter(s=>s.current).length}</b><span>Skill đang dùng</span></div><div><b>${sk.reduce((a,s)=>a+s.versions.length,0)}</b><span>Phiên bản đã lưu</span></div>
    <div><b>${src.length}</b><span>Nguồn GitHub đã khảo sát</span></div><div><b>${src.filter(s=>s.watch).length}</b><span>Nguồn AT theo dõi mỗi giờ</span></div></div>`;
  let body="";
  sk.forEach(s=>{const cur=s.versions.find(v=>v.version===s.current);
    body+=`<div class="tl-item"><div class="tl-row"><b>${esc(s.name)}</b>${s.current?pill("đang dùng v"+s.current,"p-ok"):pill("chưa có bản dùng","p-warn")}</div>
      ${cur?`<div class="tl-meta"><span>${esc(cur.description||"")}</span></div><div class="tl-meta"><span>Cấp bởi <b>${esc(cur.granted_by)}</b> · ghi bởi ${esc(cur.recorded_by)} · ${vnT(cur.at)}</span><span>Nguồn: ${esc(cur.source)}</span><span>Kết quả thử: <b>${esc(cur.test.result)}</b>${cur.test.evidence?" · "+esc(cur.test.evidence):""}</span></div>`:""}
      <details><summary>${s.versions.length} phiên bản${(s.history||[]).some(x=>x.action==="rollback")?" · đã từng quay lui":""}</summary>
        ${s.versions.map(v=>`<div class="tl-meta"><span><b>v${v.version}</b> · ${esc(v.status)} · cấp bởi ${esc(v.granted_by)} · thử ${esc(v.test.result)} · ${vnT(v.at)} · ${esc(v.path)}${v.note?" · "+esc(v.note):""}</span></div>`).join("")}
        ${(s.history||[]).map(x=>`<div class="tl-meta"><span>${vnT(x.at)} · ${esc(x.actor)} · ${x.action==="rollback"?"quay lui":"kích hoạt"} v${esc(x.from??"—")} → v${esc(x.to??"—")}${x.note?" · "+esc(x.note):""}</span></div>`).join("")}</details></div>`;});
  h+=card("Skill của bộ phận TOOL",body||`<div class="tl-empty">Chưa có skill nào.</div>`);
  h+=card("Nguồn GitHub",`<p class="muted">Khảo sát chỉ đọc lúc ${vnT(M.sources_checked_at)}: không cài, không chạy mã trong repo. “Bọc mã” chỉ áp dụng cho giấy phép cho phép, có ghi công.</p>
    <div class="tw"><table><thead><tr><th>Nguồn</th><th>Commit</th><th>Giấy phép</th><th>Lấy gì</th><th>Cách tích hợp</th><th>Chi phí</th><th>Ca thử</th><th>AT theo dõi</th></tr></thead><tbody>
    ${src.map(s=>`<tr><td><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.repo||s.id)}</a></td><td><code>${esc(String(s.commit||"").slice(0,7))}</code></td><td>${esc(s.license)}</td><td>${esc(s.function_to_take)}</td>
      <td>${esc({"system-binary":"Gọi lệnh đã cài","adapter":"Bọc mã được phép","rewrite-from-spec":"Viết lại từ đặc tả","reference-only":"Chỉ tham khảo","reject":"Không dùng"}[s.integration]||s.integration)}</td>
      <td>${esc(s.cost)}</td><td>${esc(typeof s.test_case==="string"?s.test_case:(s.test_case&&(s.test_case.summary||s.test_case.description||JSON.stringify(s.test_case))))}</td>
      <td>${s.watch?`${esc(String(s.watch.fingerprint||"").slice(0,22))}<br><span class="muted">${vnT(s.watch.seen_at)}</span>`:"không"}</td></tr>`).join("")}</tbody></table></div>`);
  return h;
}

/* ---------- 4. Agent & lịch ---------- */
function nextScheduled(){const sch=sched(),m=Number(sch.minute??5),d=new Date();d.setSeconds(0,0);if(d.getMinutes()>=m)d.setHours(d.getHours()+1);d.setMinutes(m);return d;}
function nextRunText(){const lt=liveTrig(sched().trigger_id);if(lt&&lt.enabled&&lt.next_run_at)return vnT(lt.next_run_at);return vnT(nextScheduled().toISOString())+" theo lịch";}
const liveTrig=id=>id&&S.live?S.live.find(t=>t.id===id)||null:null;
function vAgent(){
  const M=S.main;if(!M)return `<section class="card tl-empty">Chưa có dữ liệu Agent. Hai Agent đồng điều hành Thiên Công và Thiên Công AT, năm nhánh Agent con và lịch tự tích hợp sẽ hiện ở đây.</section>`;
  const A=M.agents||{},sch=M.schedule||{},B=M.budget||{};
  let h=`<div class="tl-two">${(A.executives||[]).map(e=>`<section class="card"><h2>${esc(e.short)} ${pill(e.activity==="WORKING"?"Đang giữ việc":"Rảnh",e.activity==="WORKING"?"p-run":"p-off")}</h2>
    <div class="tl-meta"><span class="tl-id">${esc(e.id)}</span>${e.clone_of?`<span>bản nhân của <b>${esc(e.clone_of)}</b></span>`:""}</div>
    <p style="margin:6px 0">${esc(e.role)}</p>
    <div class="tl-meta"><span>Ngang quyền <b>${esc((e.peer_of||[]).join(", "))}</b> và Vương Tọa trong: ${esc(e.peer_scope)}</span></div>
    <div class="tl-meta"><span>Hộp việc: <b>${e.inbox_open}</b> tin chưa đóng / ${e.inbox_total}</span><span>Bộ nhớ riêng: ${esc(e.memory)}</span>
      <span>Đang giữ: <b>${(e.holding||[]).length?esc(e.holding.join(", ")):"không"}</b></span>
      ${e.last_session?`<span>Phiên gần nhất: <b>${esc(e.last_session.run_id)}</b> · ${esc(SESS[e.last_session.result]||e.last_session.result||"đang chạy")}</span>`:""}</div></section>`).join("")}</div>`;
  h+=card("Lịch tự tích hợp của Thiên Công AT",scheduleBody(sch));
  h+=card(`Năm nhánh Agent con`,`<p class="muted">Đang chạy <b>${(A.active||[]).length}/${A.max_concurrent}</b> Agent con (mặc định tối đa ${A.max_concurrent} đồng thời). Mỗi Agent con có mã, người giao, đầu ra, quyền và hạn mức riêng.</p>
    <div class="tw"><table><thead><tr><th>Nhánh</th><th>Việc</th><th>Quyền</th><th>Đang chạy</th><th>Đã tạo</th></tr></thead><tbody>
    ${(A.branches||[]).map(b=>`<tr><td><b>${esc(b.name)}</b></td><td>${esc(b.mission)}</td><td>${esc(b.tier)}</td><td>${(b.active||[]).map(esc).join(", ")||"—"}</td><td>${b.total}</td></tr>`).join("")}</tbody></table></div>
    ${(A.subagents||[]).length?`<details style="margin-top:8px" open><summary>${A.subagents.length} Agent con gần nhất</summary><div class="tw"><table><thead><tr><th>Mã</th><th>Người giao</th><th>Việc / bước</th><th>Đầu ra phải giao</th><th>Hạn mức</th><th>Trạng thái</th></tr></thead><tbody>
      ${A.subagents.map(s=>`<tr><td>${esc(s.id)}<br><span class="muted">${esc(s.branch_name)}</span></td><td>${esc(s.assigned_by)}</td><td>${esc(s.job_id||"—")}<br>${esc(STEP_NAME[s.step]||s.step||"")}</td><td>${esc(s.output)}</td>
        <td>${Math.round((s.limits||{}).max_runtime_s/60)} phút · ${(s.limits||{}).paid_usd||0} USD</td><td>${esc({ACTIVE:"Đang chạy",DONE:"Xong",FAILED:"Lỗi",EXPIRED:"Quá hạn"}[s.state]||s.state)}${s.result?`<br><span class="muted">${esc(s.result)}</span>`:""}</td></tr>`).join("")}</tbody></table></div></details>`:""}`);
  h+=card("Hạn mức CFO",`<div class="tl-kpi"><div><b>${esc(B.remaining??0)} ${esc(B.currency||"USD")}</b><span>Còn được chi cho dịch vụ tính phí</span></div>
    <div><b>${(B.approved||[]).length}</b><span>Khoản Chủ tịch đã duyệt</span></div><div><b>${(B.spent||[]).length}</b><span>Khoản đã chi</span></div></div>
    <p class="muted">${esc(B.source||"")}. Tool hoặc Agent con cần trả phí sẽ bị chặn cho tới khi có khoản được duyệt trên Kho Báu.</p>`);
  return h;
}
function scheduleBody(sch){
  const lt=liveTrig(sch.trigger_id),ls=sch.last_scheduler_session,lock=sch.lock||{};
  const worker=!!sch.trigger_id;
  const state=!worker?["Chờ worker","p-wait"]:lt?(lt.enabled?["Đang bật","p-ok"]:["Tạm dừng","p-warn"]):(sch.paused?["Tạm dừng","p-warn"]:["Đã đăng ký","p-ok"]);
  let h=`<div class="tl-kpi">
    <div><b style="font-size:1.25rem">Phút ${String(sch.minute??5).padStart(2,"0")} mỗi giờ</b><span>${esc(sch.tz||TZ)} · ${esc(sch.cron||"")}</span></div>
    <div><b style="font-size:1.25rem">${pill(state[0],state[1])}</b><span>Trạng thái lịch${lock.state==="HELD"?` · phiên ${esc(lock.run_id)} đang chạy`:""}</span></div>
    <div><b style="font-size:1.25rem">${ls?vnT(ls.started_at):lt&&lt.last_run?vnT(lt.last_run.fired_at):"Chưa có"}</b><span>Lần chạy thật gần nhất do lịch kích hoạt${ls?` · ${esc(SESS[ls.result]||ls.result||"đang chạy")}`:""}</span></div>
    <div><b style="font-size:1.25rem">${worker?esc(nextRunText()):"—"}</b><span>Lần chạy kế tiếp</span></div></div>
    <div class="tl-meta" style="margin-top:6px"><span>Mã đăng ký lịch: <b>${esc(sch.trigger_id||"chưa đăng ký")}</b></span>${sch.worker_trigger_id?`<span>Worker theo lệnh (Thiên Công): <b>${esc(sch.worker_trigger_id)}</b></span>`:""}
      ${ls?`<span>Phiên: <b>${esc(ls.run_id)}</b> · ${esc(ls.trigger_basis||"")}</span>`:""}
      ${lt?`<span>Bộ lập lịch báo lúc ${vnT(S.liveAt)}: lần gần nhất ${lt.last_run?vnT(lt.last_run.fired_at)+" ("+esc(String(lt.last_run.status||"").replace("ROUTINE_RUN_STATUS_",""))+")":"chưa chạy"}</span>`:""}</div>
    <div class="tl-row" style="margin-top:8px">${btn("at-run","","Chạy ngay","go",!worker||!S.canEdit)}
      ${btn("at-pause","","Tạm dừng","",!worker||!S.canEdit||(lt?!lt.enabled:sch.paused))}${btn("at-resume","","Tiếp tục","",!worker||!S.canEdit||(lt?lt.enabled:!sch.paused))}
      ${btn("at-live","","Làm mới từ bộ lập lịch","",!S.mcp)}</div>${errOf("at:")}${S.liveErr?`<div class="tl-err" role="alert">${esc(S.liveErr)}</div>`:""}${noteOf("at:")}
    <p class="muted" style="margin:6px 0 0">Lịch chạy trên máy chủ của Claude nên vẫn chạy khi đóng trang này hoặc tắt laptop. “Chạy ngay” được ghi là lượt chạy tay, không tính là lượt do lịch kích hoạt.</p>`;
  if(!worker||!S.mcp){const act=sch.activation||{};
    h+=`<div class="tl-note" style="margin-top:8px"><b>${!worker?"Chờ worker: chưa có lịch nào được đăng ký.":"Trang chưa nối được với bộ lập lịch trong phiên xem này."}</b> Để kích hoạt một phiên bằng tay, mở một đoạn chat mới trong Project và dán:
      ${copyBox(act.prompt||"Em là Thiên Công AT (agent-tool-at). Đọc tool/agents/agent-tool-at/CHARTER.md trong repo crown-agent và chạy một phiên tự tích hợp.","act-prompt")}
      Hoặc trong cửa sổ lệnh ở gốc repo:${copyBox(act.cli||'python -m tool at begin --agent agent-tool-at --marker "MANUAL: chạy tay"',"act-cli")}</div>`;}
  return h;
}

/* ---------- 5. Nhật ký ---------- */
function vLog(){
  const R=S.runs;if(!R)return `<section class="card tl-empty">Chưa có nhật ký. Mỗi lượt chạy tool, mỗi phiên AT, mỗi lần phát hành hay quay lui sẽ được ghi ở đây kèm bằng chứng.</section>`;
  const F=[["all","Tất cả"],["sess","Phiên AT"],["run","Lượt chạy tool"],["rel","Phát hành & quay lui"],["bad","Lỗi & từ chối"]];
  let h=`<div class="tl-row">${F.map(([k,l])=>`<button type="button" class="tl-chip" data-act="logf" data-id="${k}" aria-pressed="${S.logFilter===k}">${esc(l)}</button>`).join("")}</div>`;
  const f=S.logFilter,sess=R.sessions||[],runs=R.runs||[];
  if(f==="all"||f==="sess"||f==="bad"){const ss=f==="bad"?sess.filter(s=>s.result==="ERROR"):sess;
    h+=card(`Phiên Thiên Công AT`,ss.length?ss.map(s=>{const tg=TRIG[s.trigger]||[s.trigger,"p-off",""];
      return `<div class="tl-item"><div class="tl-row"><span class="tl-id">${esc(s.run_id)}</span>${pill(tg[0],tg[1])}${pill(SESS[s.result]||s.result||"đang chạy",s.result==="ERROR"?"p-bad":s.result==="RELEASED"?"p-ok":"p-off")}<span class="tl-meta"><span>${vnT(s.started_at)} → ${vnT(s.finished_at)}</span></span></div>
        <div class="tl-meta"><span>${esc(tg[2])}: ${esc(s.trigger_basis||"")}</span></div>
        ${(s.releases||[]).map(r=>`<div class="tl-meta"><span>Phát hành <b>${esc(r.tool)}@${esc(r.version)}</b> · gọi thử: ${esc(r.smoke)}</span></div>`).join("")}
        ${s.plan&&s.plan.action?`<div class="tl-meta"><span>Việc chọn: <b>${esc({NO_CHANGE:"không có thay đổi",RESUME_JOB:"làm tiếp việc "+(s.plan.action.job_id||""),EVALUATE_SOURCE:"đánh giá nguồn "+(s.plan.action.id||""),WEB_REQUEST:"lệnh từ web"}[s.plan.action.do]||s.plan.action.do)}</b> · việc tồn ${(s.plan.backlog||[]).length} · GitHub đổi ${(s.plan.github_changed||[]).length} (trùng ${(s.plan.github_duplicates||[]).length})</span></div>`:""}
        ${(s.notes||[]).map(n=>`<div class="tl-meta"><span>${esc(n)}</span></div>`).join("")}</div>`;}).join(""):`<div class="tl-empty">Chưa có phiên nào${f==="bad"?" bị lỗi":""}.</div>`,
      ` <span class="badge b-VERIFIED">LỊCH = bộ lập lịch · TAY = bấm tay</span>`);}
  if(f==="all"||f==="run"||f==="bad"){const rr=f==="bad"?runs.filter(r=>r.status!=="OK"):runs;
    h+=card("Lượt chạy tool",rr.length?rr.map(r=>{const stt=RUN_STATE[r.status]||[r.status,"p-off"],tg=TRIG[r.trigger]||[r.trigger,"p-off"];
      return `<div class="tl-item"><div class="tl-row"><span class="tl-id">${esc(r.run_id)}</span>${pill(stt[0],stt[1])}${pill(tg[0],tg[1])}<span><b>${esc(r.tool)}${r.version?"@"+esc(r.version):""}</b> · người gọi <b>${esc(r.caller)}</b></span></div>
        <div class="tl-meta"><span>${vnT(r.started_at)}${r.duration_s!=null?` · ${String(r.duration_s).replace(".",",")} giây`:""}</span>${r.purpose?`<span>${esc(r.purpose)}</span>`:""}
          <span>Vào: ${(r.inputs||[]).map(i=>`${esc(i.file)} (${esc(kb(i.bytes))})`).join(", ")||"—"} · Ra: ${(r.outputs||[]).map(o=>`${esc(o.name)} (${esc(kb(o.bytes))})`).join(", ")||"—"}</span></div>
        ${(r.access||[]).map(a=>`<div class="tl-meta"><span>${a.allow?"Cho":"CHẶN"} ${a.mode==="read"?"đọc":"ghi"} ${esc(a.file)}: ${esc(a.rule)}</span></div>`).join("")}
        ${r.error?`<div class="tl-err">${esc(r.error)}</div>`:""}
        <div class="tl-meta"><span>Bằng chứng: tool/${esc(r.record||"")}${r.isolation?` · cách ly: thư mục tạm, môi trường sạch${r.isolation.netns?", không mạng":""}${r.isolation.rlimits?", giới hạn tài nguyên":""}`:""}</span></div></div>`;}).join(""):`<div class="tl-empty">Chưa có lượt chạy nào${f==="bad"?" bị lỗi hoặc bị từ chối":""}.</div>`);}
  if(f==="all"||f==="rel"){const ev=[];((S.catalog&&S.catalog.tools)||[]).forEach(t=>(t.history||[]).forEach(x=>ev.push(Object.assign({tool:t.id},x))));
    ev.sort((a,b)=>String(b.at).localeCompare(String(a.at)));
    h+=card("Phát hành & quay lui",ev.length?ev.map(x=>`<div class="tl-item"><div class="tl-row">${pill(x.action==="rollback"?"QUAY LUI":"PHÁT HÀNH",x.action==="rollback"?"p-warn":"p-ok")}<b>${esc(x.tool)}@${esc(x.version)}</b><span class="tl-meta"><span>${vnT(x.at)} · ${esc(x.actor)}</span></span></div>
      <div class="tl-meta"><span>Bản hiện hành: ${esc(x.from??"—")} → <b>${esc(x.to??"không còn bản gọi được")}</b>${x.note?" · "+esc(x.note):""}</span></div></div>`).join(""):`<div class="tl-empty">Chưa có bản phát hành nào.</div>`);}
  return h;
}

/* ---------- khung ---------- */
function render(){
  const act=document.activeElement,keep=act&&root.contains(act)&&act.id?{id:act.id,s:act.selectionStart,e:act.selectionEnd}:null;
  const M=S.main,sm=(M&&M.summary)||{};
  let body;
  if(S.conn==="none")body=`<section class="card muted">Cần mở trang trong Claude và đăng nhập để xem dữ liệu thật của TOOL.</section>`;
  else if(S.conn==="err")body=`<section class="card muted">Mất kết nối dữ liệu. Tải lại trang để thử lại.</section>`;
  else body=S.sub==="kho"?vKho():S.sub==="tao"?vTao():S.sub==="skill"?vSkill():S.sub==="agent"?vAgent():vLog();
  const openJobs=((S.jobs&&S.jobs.jobs)||[]).filter(j=>["READY","RUNNING","BLOCKED","WAITING_APPROVAL"].includes(j.state)).length+S.requests.filter(r=>r.kind==="job"&&r.state==="CHO_WORKER").length;
  root.innerHTML=`<div class="tl-head"><h2>TOOL · Thiên Công</h2><small>${M?`Trạng thái xuất lúc ${vnT(M.updated_at)} · commit ${esc(M.commit)}`:S.conn==="loading"?"Đang nối dữ liệu…":"Chưa có lần xuất trạng thái nào"}</small></div>
    <div class="tl-kpi"><div><b>${sm.tools_callable??0}</b><span>Công cụ gọi được</span></div><div><b>${openJobs}</b><span>Việc đang mở</span></div>
      <div><b>${sm.runs_24h??0}</b><span>Lượt chạy 24 giờ${sm.runs_denied_24h?` (${sm.runs_denied_24h} bị từ chối)`:""}</span></div><div><b>${sm.skills_current??0}</b><span>Skill đang dùng</span></div></div>
    <nav class="tl-sub" aria-label="Màn hình của TOOL">${SUBS.map(([k,l])=>`<button type="button" data-sub="${k}" aria-pressed="${S.sub===k}">${esc(l)}</button>`).join("")}</nav>${body}`;
  if(keep){const el=document.getElementById(keep.id);if(el){el.focus({preventScroll:true});try{if(keep.s!=null)el.setSelectionRange(keep.s,keep.e);}catch(_){}}}
}

/* ---------- hành động ---------- */
function fail(m){const e=new Error(m);e.user=true;throw e;}
async function guard(key,fn,busyKey){const bk=busyKey||key;if(S.busy.has(bk))return;S.busy.add(bk);delete S.err[key];delete S.note[key];render();
  try{await fn();}catch(e){S.err[key]=e&&e.user?e.message:"Không thực hiện được: "+((e&&(e.message||e.code))||"lỗi không rõ");}
  S.busy.delete(bk);render();}
const MCP_MSG={needs_reauth:`Kết nối “${CONNECTOR}” đã hết hạn. Nối lại trong claude.ai → Settings → Connectors.`,server_not_connected:`Tài khoản xem trang chưa bật connector “${CONNECTOR}”.`,
  not_in_manifest:"Anh đã tắt hoặc từ chối quyền điều khiển lịch cho trang này. Bật lại trong mục Permissions của trang.",approval_required:"Cần anh bấm cho phép khi được hỏi.",
  blocked_by_policy:"Chính sách của tổ chức chặn thao tác này.",not_granted:"Phiên xem này không được cấp quyền điều khiển lịch.",capability_disabled:"Phiên xem này không dùng được kết nối lịch.",
  server_unavailable:"Bộ lập lịch tạm thời không trả lời. Thử lại sau ít phút.",tool_error:"Bộ lập lịch từ chối lệnh."};
const mcpMsg=e=>(MCP_MSG[e&&e.code]||"Không gọi được bộ lập lịch")+(e&&e.code==="tool_error"&&e.message?` (${String(e.message).slice(0,160)})`:"");
async function fireWorker(kind,text){
  const sch=sched(),id=kind==="at"?sch.trigger_id:(sch.worker_trigger_id||sch.trigger_id);
  if(!S.mcp||!id)return {ok:false,why:!id?"Chưa có lịch nào đăng ký làm worker.":"Trang chưa nối được với bộ lập lịch trong phiên xem này."};
  try{await S.mcp.callTool(CONNECTOR,"fire_trigger",{trigger_id:id,text});return {ok:true};}catch(e){return {ok:false,why:mcpMsg(e)};}
}
async function loadLive(){
  if(!S.mcp)return;
  try{const r=await S.mcp.callTool(CONNECTOR,"list_triggers",{limit:100},{cache:false});const p=r&&r.payload;
    const arr=p&&Array.isArray(p.data)?p.data:Array.isArray(p)?p:null;if(!arr)fail("Bộ lập lịch trả dữ liệu không đọc được.");
    S.live=arr;S.liveAt=nowISO();S.liveErr=null;}catch(e){S.liveErr=e&&e.user?e.message:mcpMsg(e);}
}
async function newRequest(kind,payload,fireText){
  const id="RQ-"+Date.now().toString(36)+"-"+hex(2);
  const doc={kind,state:"CHO_WORKER",created_at:nowISO(),by:"CHAIRMAN",payload,result:null};
  await S.db.doc("tool_requests/"+id).set(doc);
  const f=await fireWorker("worker",`WEB: ${fireText} ${id}`);
  if(f.ok){try{await S.db.doc("tool_requests/"+id).update({fired_at:nowISO()});}catch(_){}}
  return {id,fired:f};
}
const ACT={
  cat:id=>{S.cat=id;render();},
  logf:id=>{S.logFilter=id;render();},
  "job-open":id=>{S.open["job:"+id]=!S.open["job:"+id];render();},
  try:id=>{S.open["try:"+id]=!S.open["try:"+id];render();},
  "rb-open":id=>{S.open["rb:"+id]=!S.open["rb:"+id];render();},
  "rm-file":i=>{S.files.splice(Number(i),1);render();},
  copy:async id=>{const el=document.getElementById(id);if(!el)return;try{await navigator.clipboard.writeText(el.textContent);}catch(_){const r=document.createRange();r.selectNodeContents(el);const s=getSelection();s.removeAllRanges();s.addRange(r);}},
  "try-send":id=>guard("try:"+id,async()=>{
    const t=((S.catalog&&S.catalog.tools)||[]).find(x=>x.id===id);if(!t||!S.db)fail("Chưa nối được dữ liệu.");
    const text=(document.getElementById("try-text-"+id)||{}).value||"",name=((document.getElementById("try-name-"+id)||{}).value||"").trim();
    if(!text.trim())fail("Nhập hoặc chọn dữ liệu để chạy thử.");
    if(new Blob([text]).size>MAX_INLINE)fail(`Dữ liệu chạy thử trên web tối đa ${MAX_INLINE/1024} KB. Tệp lớn hơn hãy giao cho một Agent chạy.`);
    if(!/^[^\\/]+\.[A-Za-z0-9]+$/.test(name))fail("Tên tệp cần có đuôi, ví dụ phim.srt, và không chứa dấu gạch chéo.");
    const inp=(t.interface.inputs[0]||{name:"input"});
    const r=await newRequest("run",{tool:id,version:t.current_version,input_name:inp.name,filename:name,input_text:text,params:{}},"RUN");
    S.note["try:"+id]=r.fired.ok?`Đã gửi lượt chạy thử <b>${esc(r.id)}</b> và gọi worker. Kết quả hiện ngay bên dưới khi xong.`:`Đã gửi lượt chạy thử <b>${esc(r.id)}</b>. ${esc(r.fired.why)}`;},"try-send:"+id),
  "rb-send":id=>guard("rb:"+id,async()=>{
    const t=((S.catalog&&S.catalog.tools)||[]).find(x=>x.id===id),why=((document.getElementById("rb-reason-"+id)||{}).value||"").trim();
    if(!t||!S.db)fail("Chưa nối được dữ liệu.");if(why.length<5)fail("Ghi lý do thu hồi (ít nhất 5 ký tự).");
    const r=await newRequest("rollback",{tool:id,version:t.current_version,reason:why},"ROLLBACK");
    S.note["rb:"+id]=`Đã gửi lệnh thu hồi <b>${esc(r.id)}</b>. ${r.fired.ok?"Đã gọi worker.":esc(r.fired.why)}`;},"rb-send:"+id),
  refire:id=>guard("fire:"+id,async()=>{const r=S.requests.find(x=>x._id===id);if(!r)return;
    const f=await fireWorker("worker",`WEB: ${r.kind==="run"?"RUN":r.kind==="job"?"JOB":"ROLLBACK"} ${id}`);if(!f.ok)fail(f.why);
    await S.db.doc("tool_requests/"+id).update({fired_at:nowISO()});},"refire:"+id),
  job:()=>guard("job:",async()=>{
    if(!S.db)fail("Chưa nối được dữ liệu.");
    const prompt=(S.open.prompt||"").trim(),url=(S.open.url||"").trim();
    if(prompt.length<8)fail("Viết một câu mô tả công cụ anh cần (ít nhất 8 ký tự).");
    if(url&&!/^https?:\/\/\S+$/.test(url))fail("Đường dẫn cần bắt đầu bằng http:// hoặc https://.");
    if(S.files.length&&!S.assets)fail("Phiên xem này không tải tệp lên được. Bỏ tệp ra khỏi danh sách hoặc mở trang bằng tài khoản có quyền sửa.");
    const p=vnParts(new Date()),jobId=`TJ-${p.year}${p.month}${p.day}-${hex(3)}`,files=[];
    for(const f of S.files){f.state="dang";render();
      try{const up=await S.assets.upload(f.file,{type:f.type});f.state="xong";
        files.push({name:f.file.name,path:f.path,asset_id:up.id,bytes:up.sizeBytes,content_type:up.contentType,kind:f.kind});}
      catch(e){f.state="loi";render();
        fail(({too_large:`“${f.path}” quá lớn.`,unsupported_type:`“${f.path}” không thuộc định dạng web nhận.`,quota_or_state:"Kho tệp của trang đã đầy. Bỏ bớt tệp hoặc gửi qua chat.",invalid_request:`“${f.path}” không tải lên được (tệp rỗng hoặc không phải UTF-8).`}[e&&e.code])||`Tải “${f.path}” lên bị lỗi. Thử lại.`);}}
    const r=await newRequest("job",{job_id:jobId,prompt,url:url||null,files,skipped:S.skipped.slice(0,100)},"JOB");
    S.lastJob=jobId;S.note["job:"]=r.fired.ok?"Đã gọi worker; việc thường bắt đầu sau 1–3 phút.":esc(r.fired.why)+" Việc sẽ được nhận ở phiên kế tiếp.";
    S.files=[];S.skipped=[];S.open.prompt="";S.open.url="";}),
  "at-live":()=>guard("at:",async()=>{await loadLive();},"at-live:"),
  "at-run":()=>guard("at:",async()=>{const f=await fireWorker("at","MANUAL: Chủ tịch bấm Chạy ngay trên web lúc "+vnT(nowISO()));if(!f.ok)fail(f.why);
    S.note["at:"]="Đã gửi lệnh chạy ngay. Phiên này sẽ được ghi là lượt chạy tay; kết quả hiện trong Nhật ký sau khi phiên kết thúc.";await loadLive();},"at-run:"),
  "at-pause":()=>toggleSchedule(false),
  "at-resume":()=>toggleSchedule(true),
};
function toggleSchedule(on){return guard("at:",async()=>{
  const id=sched().trigger_id;if(!id)fail("Chưa có lịch nào đăng ký.");
  if(S.mcp){try{await S.mcp.callTool(CONNECTOR,"update_trigger",{trigger_id:id,enabled:on});}catch(e){fail(mcpMsg(e));}
    S.note["at:"]=on?"Đã bật lại lịch trên bộ lập lịch.":"Đã tạm dừng lịch trên bộ lập lịch. Không phiên nào tự chạy cho tới khi anh bấm Tiếp tục.";await loadLive();}
  else{await newRequest("schedule",{paused:!on},"SCHEDULE");S.note["at:"]=`Đã ghi lệnh ${on?"tiếp tục":"tạm dừng"}. Vì trang chưa nối được bộ lập lịch, lệnh có hiệu lực từ phiên kế tiếp.`;}
},on?"at-resume:":"at-pause:");}

root.addEventListener("click",e=>{
  const sb=e.target.closest("[data-sub]");if(sb){S.sub=sb.dataset.sub;try{history.replaceState(null,"","#tool/"+S.sub);}catch(_){}render();
    if(S.sub==="agent"&&S.mcp&&S.perms&&!S.live)S.perms.state("mcp:"+CONNECTOR).then(v=>{if(v==="granted")loadLive().then(render);}).catch(()=>{});return;}
  const b=e.target.closest("[data-act]");if(!b||b.tagName==="INPUT")return;const fn=ACT[b.dataset.act];if(fn)fn(b.dataset.id);
});
root.addEventListener("input",e=>{const t=e.target;
  if(t.id==="tool-q"){S.q=t.value;render();return;}
  if(t.dataset&&t.dataset.draft)S.open[t.dataset.draft]=t.value;});
root.addEventListener("change",async e=>{const t=e.target;
  if(t.id==="tool-pick"||t.id==="tool-zip"||t.id==="tool-dir"){const fs=Array.from(t.files||[]);t.value="";await addFiles(fs);return;}
  if(t.dataset&&t.dataset.act==="try-file"){const f=(t.files||[])[0],id=t.dataset.id;t.value="";if(!f)return;
    if(f.size>MAX_INLINE){S.err["try:"+id]=`Tệp chạy thử trên web tối đa ${MAX_INLINE/1024} KB.`;render();return;}
    try{S.open["tryText:"+id]=new TextDecoder("utf-8",{fatal:true}).decode(await f.arrayBuffer());S.open["tryName:"+id]=f.name;delete S.err["try:"+id];}
    catch(_){S.err["try:"+id]="Tệp không phải văn bản UTF-8.";}render();}});
root.addEventListener("dragover",e=>{const d=e.target.closest("#tool-drop");if(d){e.preventDefault();d.classList.add("over");}});
root.addEventListener("dragleave",e=>{const d=e.target.closest("#tool-drop");if(d)d.classList.remove("over");});
root.addEventListener("drop",async e=>{const d=e.target.closest("#tool-drop");if(!d)return;e.preventDefault();d.classList.remove("over");
  const items=Array.from((e.dataTransfer&&e.dataTransfer.items)||[]),out=[];
  const entries=items.map(i=>i.webkitGetAsEntry?i.webkitGetAsEntry():null).filter(Boolean);
  if(entries.length){for(const en of entries)await walkEntry(en,"",out);}else out.push(...Array.from((e.dataTransfer&&e.dataTransfer.files)||[]));
  await addFiles(out);});
root.addEventListener("paste",async e=>{if(S.sub!=="tao")return;const fs=Array.from((e.clipboardData&&e.clipboardData.files)||[]);if(!fs.length)return;e.preventDefault();
  await addFiles(fs.map((f,i)=>{const ext=(f.type.split("/")[1]||"bin").replace("jpeg","jpg");
    const file=f.name&&f.name!=="image.png"?f:new File([f],`dan_${Date.now()}_${i}.${ext}`,{type:f.type});return {file,path:file.name};}));});
addEventListener("hashchange",()=>{const s=subFromHash();if(s&&s!==S.sub){S.sub=s;render();}});

/* ---------- dữ liệu ---------- */
render();
(async()=>{
  const timer=setTimeout(()=>{if(S.conn==="loading"){S.conn="none";render();}},10000);
  const C=window.claude;
  const db=C?await C.use("db"):null;
  clearTimeout(timer);
  if(!db){S.conn="none";render();return;}
  S.db=db;
  try{const u=await C.use("user");S.canEdit=u?await u.canEdit():false;}catch(_){S.canEdit=false;}
  db.collection("tool_state").onSnapshot(snap=>{snap.docs.forEach(d=>{if(d.exists&&["main","catalog","jobs","runs"].includes(d.id))S[d.id]=d.data();});S.conn="on";render();},()=>{S.conn="err";render();});
  db.collection("tool_requests").onSnapshot(snap=>{const a=[];snap.docs.forEach(d=>{if(d.exists)a.push(Object.assign({},d.data(),{_id:d.id}));});
    a.sort((x,y)=>String(y.created_at).localeCompare(String(x.created_at)));S.requests=a;render();},()=>{});
  C.use("assets").then(a=>{S.assets=a;render();}).catch(()=>{});
  C.use("permissions").then(p=>{S.perms=p;}).catch(()=>{});
  C.use("mcp").then(m=>{S.mcp=m;render();}).catch(()=>{});
})();
})();
