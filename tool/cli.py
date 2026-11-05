"""CLI của bộ phận TOOL.  Dùng: python -m tool [--sync] <lệnh> ...

Tìm và gọi tool (mọi Agent trong danh bạ):
  find [từ khóa] [--category C]              Tìm theo chức năng
  show TOOL[@X.Y.Z]                          Xem mô tả, ví dụ, cách gọi
  run TOOL --caller ID --in tên=tệp [--out THƯ_MỤC] [--param k=v]

Làm tool (hai Agent điều hành: agent-tool, agent-tool-at):
  job new|list|show|backlog|claim|release|heartbeat|start|done|skip|fail|set|move
  sub spawn|finish|list                      Agent con theo 5 nhánh (tối đa 2 chạy đồng thời)
  intake JOB MỤC... [--public]               Nhận mẫu: tệp, thư mục, ZIP, URL, prompt
  frames JOB VIDEO [--every S|--at t1,t2]    Lấy khung hình bằng FFmpeg cục bộ
  test BUILD_DIR                             Soát quyền + chạy ca thử trong vùng cách ly
  publish BUILD_DIR --actor ID [--job J] [--auto --session RUN]
  rollback TOOL --actor ID --reason "..." [--to X.Y.Z]
  skill add|list|rollback                    Kho skill TOOL có phiên bản và quay lui
  msg send|list|close · mem add              Hộp việc và bộ nhớ riêng của từng Agent
  at begin|github|plan|note|release|end|status   Phiên tự tích hợp của Thiên Công AT
  schedule show|set · budget show|approve · export --out DIR · init · whoami ID

--sync: bọc lệnh trong một giao dịch git (kéo → sửa → commit → đẩy) để hai phiên ở hai máy không ghi chồng.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import export as export_mod
from .core import release, sandbox, video
from .core.agents import Mailbox, Memory
from .core.budget import Budget
from .core.catalog import CATEGORIES, Catalog, validate_manifest
from .core.gitsync import GitSync
from .core.identity import Directory
from .core.inbox import Inbox
from .core.jobs import JobStore
from .core.runner import Runner
from .core.scheduler import Scheduler
from .core.skills import Skills
from .core.subagents import SubAgents
from .core.util import DATA_DIRS, ToolError, atomic_write_json, read_json, tool_root, vn


def _j(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def _kv(items: list[str]) -> dict:
    out = {}
    for it in items or []:
        k, sep, v = it.partition("=")
        if not sep or not k:
            raise ToolError(f"Tham số phải dạng tên=giá_trị: {it!r}")
        out[k] = v
    return out


def _params(items: list[str]) -> dict:
    out = {}
    for k, v in _kv(items).items():
        try:
            out[k] = json.loads(v)
        except ValueError:
            out[k] = v
    return out


def _job_line(j: dict) -> str:
    nxt = JobStore.next_step(j)
    hold = f" · giữ: {j['lease']['holder']}" if j.get("lease") else ""
    return f"{j['job_id']}  {j['state']:<14} → {j['assigned_to']:<14}{hold} · bước kế: {nxt['id'] if nxt else '(hết)'} · {j['title']}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tool", description="CROWN AGENT — bộ phận TOOL (Thiên Công)")
    p.add_argument("--sync", action="store_true", help="bọc lệnh trong giao dịch git kéo-sửa-commit-đẩy")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")
    a = sub.add_parser("whoami"); a.add_argument("id")
    a = sub.add_parser("find"); a.add_argument("query", nargs="?", default=""); a.add_argument("--category")
    a.add_argument("--json", action="store_true")
    a = sub.add_parser("show"); a.add_argument("tool")
    a = sub.add_parser("run"); a.add_argument("tool"); a.add_argument("--caller", required=True)
    a.add_argument("--in", dest="inputs", action="append", default=[], metavar="tên=tệp")
    a.add_argument("--out"); a.add_argument("--param", action="append", default=[])
    a.add_argument("--purpose", default=""); a.add_argument("--trigger", default="manual"); a.add_argument("--session")
    a.add_argument("--json", action="store_true")

    j = sub.add_parser("job").add_subparsers(dest="jcmd", required=True)
    a = j.add_parser("new"); a.add_argument("--title", required=True); a.add_argument("--prompt", required=True)
    a.add_argument("--by", required=True); a.add_argument("--to", required=True); a.add_argument("--key")
    a.add_argument("--priority", type=int, default=5); a.add_argument("--origin", default="chat")
    a.add_argument("--id", dest="job_id", help="mã việc đã cấp sẵn (web)")
    a = j.add_parser("list"); a.add_argument("--state")
    j.add_parser("backlog")
    a = j.add_parser("show"); a.add_argument("job"); a.add_argument("--json", action="store_true")
    a = j.add_parser("claim"); a.add_argument("job"); a.add_argument("actor"); a.add_argument("--ttl", type=int, default=3600)
    a = j.add_parser("release"); a.add_argument("job"); a.add_argument("actor"); a.add_argument("token", type=int); a.add_argument("--note", default="")
    a = j.add_parser("heartbeat"); a.add_argument("job"); a.add_argument("actor"); a.add_argument("token", type=int); a.add_argument("--ttl", type=int, default=3600)
    a = j.add_parser("start"); a.add_argument("job"); a.add_argument("step"); a.add_argument("actor"); a.add_argument("token", type=int); a.add_argument("--sub")
    a = j.add_parser("done"); a.add_argument("job"); a.add_argument("step"); a.add_argument("actor"); a.add_argument("token", type=int)
    a.add_argument("--evidence", action="append", default=[]); a.add_argument("--note", default="")
    a = j.add_parser("skip"); a.add_argument("job"); a.add_argument("step"); a.add_argument("actor"); a.add_argument("token", type=int); a.add_argument("--note", required=True)
    a = j.add_parser("fail"); a.add_argument("job"); a.add_argument("step"); a.add_argument("actor"); a.add_argument("token", type=int); a.add_argument("--note", required=True)
    a = j.add_parser("set"); a.add_argument("job"); a.add_argument("actor"); a.add_argument("token", type=int)
    a.add_argument("--tool-id"); a.add_argument("--version")
    a = j.add_parser("move"); a.add_argument("job"); a.add_argument("state"); a.add_argument("actor")
    a.add_argument("--token", type=int); a.add_argument("--note", default="")

    s = sub.add_parser("sub").add_subparsers(dest="scmd", required=True)
    a = s.add_parser("spawn"); a.add_argument("--branch", required=True); a.add_argument("--by", required=True)
    a.add_argument("--job"); a.add_argument("--step"); a.add_argument("--output", required=True)
    a.add_argument("--worker", default="claude-subagent"); a.add_argument("--minutes", type=int, default=30)
    a = s.add_parser("finish"); a.add_argument("id"); a.add_argument("--by", required=True)
    g = a.add_mutually_exclusive_group(required=True); g.add_argument("--ok", action="store_true"); g.add_argument("--failed", action="store_true")
    a.add_argument("--result", required=True); a.add_argument("--evidence", action="append", default=[])
    a = s.add_parser("list"); a.add_argument("--state")

    a = sub.add_parser("intake"); a.add_argument("job"); a.add_argument("items", nargs="+")
    a.add_argument("--public", action="store_true", help="mẫu tự tạo, không riêng tư: cho vào git")
    a.add_argument("--pointer", help="con trỏ tới bản gốc (vd. mã asset trên web) để phiên khác tải lại")
    a = sub.add_parser("frames"); a.add_argument("job"); a.add_argument("video")
    a.add_argument("--every", type=float); a.add_argument("--at"); a.add_argument("--max", type=int, default=40)
    a = sub.add_parser("test"); a.add_argument("build_dir"); a.add_argument("--json", action="store_true")
    a = sub.add_parser("publish"); a.add_argument("build_dir"); a.add_argument("--actor", required=True)
    a.add_argument("--job"); a.add_argument("--auto", action="store_true"); a.add_argument("--session")
    a = sub.add_parser("smoke"); a.add_argument("tool"); a.add_argument("--caller", required=True)
    a.add_argument("--case", action="append", default=[], metavar="input=tệp,expect:tên=tệp",
                   help="dữ liệu khác mẫu: các cặp tên=tệp đầu vào, và expect:tên_kết_quả=tệp mong đợi")
    a.add_argument("--rollback-as", help="Agent điều hành: tự khôi phục bản tốt nếu gọi thử lỗi")
    a.add_argument("--session"); a.add_argument("--trigger", default="manual"); a.add_argument("--json", action="store_true")
    a = sub.add_parser("rollback"); a.add_argument("tool"); a.add_argument("--actor", required=True)
    a.add_argument("--reason", required=True); a.add_argument("--to")

    k = sub.add_parser("skill").add_subparsers(dest="kcmd", required=True)
    a = k.add_parser("add"); a.add_argument("path"); a.add_argument("--granted-by", required=True)
    a.add_argument("--actor", required=True); a.add_argument("--source", required=True)
    a.add_argument("--test", required=True, choices=["PASS", "FAIL", "NOT_RUN"]); a.add_argument("--evidence", default="")
    a.add_argument("--note", default="")
    k.add_parser("list")
    a = k.add_parser("rollback"); a.add_argument("name"); a.add_argument("--actor", required=True); a.add_argument("--reason", required=True)

    m = sub.add_parser("msg").add_subparsers(dest="mcmd", required=True)
    a = m.add_parser("send"); a.add_argument("--to", required=True); a.add_argument("--from", dest="sender", required=True)
    a.add_argument("--subject", required=True); a.add_argument("--body", default=""); a.add_argument("--job")
    a = m.add_parser("list"); a.add_argument("agent"); a.add_argument("--state")
    a = m.add_parser("close"); a.add_argument("agent"); a.add_argument("msg"); a.add_argument("--actor", required=True); a.add_argument("--note", default="")
    mm = sub.add_parser("mem").add_subparsers(dest="memcmd", required=True)
    a = mm.add_parser("add"); a.add_argument("agent"); a.add_argument("--actor", required=True); a.add_argument("--text", required=True)

    t = sub.add_parser("at").add_subparsers(dest="acmd", required=True)
    a = t.add_parser("begin"); a.add_argument("--agent", default="agent-tool-at"); a.add_argument("--marker"); a.add_argument("--trigger")
    a = t.add_parser("github"); a.add_argument("run")
    a = t.add_parser("plan"); a.add_argument("run"); a.add_argument("--requests")
    a = t.add_parser("note"); a.add_argument("run"); a.add_argument("text")
    a = t.add_parser("release"); a.add_argument("run"); a.add_argument("tool"); a.add_argument("version"); a.add_argument("smoke")
    a = t.add_parser("candidate"); a.add_argument("source"); a.add_argument("key"); a.add_argument("outcome"); a.add_argument("--note", default="")
    a = t.add_parser("end"); a.add_argument("run"); a.add_argument("result"); a.add_argument("--note", default="")
    t.add_parser("status")

    sc = sub.add_parser("schedule").add_subparsers(dest="sccmd", required=True)
    sc.add_parser("show")
    a = sc.add_parser("set"); a.add_argument("--actor", required=True); a.add_argument("--paused", choices=["true", "false"])
    a.add_argument("--trigger-id"); a.add_argument("--worker-trigger-id")
    b = sub.add_parser("budget").add_subparsers(dest="bcmd", required=True)
    b.add_parser("show")
    a = b.add_parser("approve"); a.add_argument("amount", type=float); a.add_argument("--what", required=True); a.add_argument("--ref", required=True)
    a = sub.add_parser("export"); a.add_argument("--out", required=True)
    a = sub.add_parser("webver", help="version của 4 tài liệu tool_state trên web (dùng làm if_version)")
    a.add_argument("pairs", nargs="*", metavar="tên=version")
    return p


def dispatch(args, root: Path) -> int:
    c = args.cmd
    if c == "init":
        for d in DATA_DIRS + ("config", "runs/sessions", "runs/locks", "agents/sub"):
            (root / d).mkdir(parents=True, exist_ok=True)
        print(f"Đã có đủ thư mục TOOL tại {root}")
        return 0
    d = Directory(root)
    if c == "whoami":
        _j(d.resolve(args.id)); return 0
    if c == "find":
        res = Catalog(root, d).find(args.query, args.category)
        if args.json:
            _j(res); return 0
        for r in res:
            print(f"{r['id']}@{r['version']}  [{r['category_name']}]  {r['name']} — {r['summary']}")
        print(f"({len(res)} tool gọi được" + (f" khớp '{args.query}'" if args.query else "") + ")")
        return 0
    if c == "show":
        cat = Catalog(root, d)
        tid, ver = cat.resolve(args.tool)
        m, t = cat.manifest(tid, ver), cat.tool(tid)
        rel = read_json(cat.version_dir(tid, ver) / "RELEASE.json")
        print(f"{tid}@{ver} — {m['name']}  [{CATEGORIES[m['category']]}]")
        print(f"  {m['summary']}")
        print(f"  Phát hành: {rel['published_by']} lúc {vn(rel['published_at'])} · việc {rel['job_id']} · viết: {rel['builder']} · kiểm: {rel['tester']}")
        print(f"  Quyền: mạng={'có' if m['permissions']['network'] else 'không'}, lệnh ngoài={m['permissions']['subprocess'] or 'không'}")
        print(f"  Nguồn: {json.dumps(m['source'], ensure_ascii=False)}")
        print("  Phiên bản: " + ", ".join(f"{v['version']}({v['status']})" for v in t["versions"]) + f" · hiện hành: {t['current_version']}")
        for i in m["interface"]["inputs"]:
            print(f"  Đầu vào  {i['name']}: {i.get('desc', '')} {i.get('ext', '')}")
        for o in m["interface"]["outputs"]:
            print(f"  Kết quả  {o['name']}: {o.get('desc', '')}")
        for ex in m["examples"]:
            print(f"  Ví dụ '{ex.get('name', 'mẫu')}': {ex['inputs']} → {ex['expected']}")
        print(f"  Gọi: python -m tool run {tid} --caller <ID> --in " + " ".join(f"{i['name']}=<tệp>" for i in m["interface"]["inputs"]) + " --out <thư mục>")
        return 0
    if c == "run":
        rec = Runner(root, d).run(args.tool, args.caller, _kv(args.inputs), params=_params(args.param), out_dir=args.out,
                                  purpose=args.purpose, trigger=args.trigger, session=args.session)
        if args.json:
            _j(rec)
        else:
            print(f"{rec['run_id']}  {rec['status']}  {rec['tool']}@{rec['version']}  người gọi {rec['caller']}  ({rec['duration_s']} giây)")
            for o in rec["outputs"]:
                print(f"  kết quả: {o['path']}  ({o['bytes']} byte, sha256 {o['sha256'][:12]})")
            if rec["report"]:
                print(f"  báo cáo: {json.dumps(rec['report'], ensure_ascii=False)}")
            if rec["error"]:
                print(f"  lỗi: {rec['error']}", file=sys.stderr)
            print(f"  bản ghi: tool/{rec['record']}")
        return 0 if rec["status"] == "OK" else 1

    jobs = JobStore(root, d)
    if c == "job":
        jc = args.jcmd
        if jc == "new":
            job = jobs.create(title=args.title, prompt=args.prompt, requested_by=args.by, assigned_to=args.to,
                              idempotency_key=args.key, priority=args.priority, origin=args.origin, job_id=args.job_id)
            Mailbox(root, d).send(args.to, args.by, f"Việc mới {job['job_id']}: {job['title']}", job["prompt"], job["job_id"])
            print(_job_line(job))
        elif jc == "list":
            rows = jobs.list(args.state)
            for r in rows:
                print(_job_line(r))
            print(f"({len(rows)} việc)")
        elif jc == "backlog":
            rows = jobs.backlog()
            for r in rows:
                print(_job_line(r))
            print(f"({len(rows)} việc cần người làm)")
        elif jc == "show":
            job = jobs.get(args.job)
            if args.json:
                _j(job); return 0
            print(_job_line(job))
            print(f"  Yêu cầu: {job['prompt']}")
            for st in job["steps"]:
                who = f"{st['by'] or '-'}" + (f"/{st['sub']}" if st["sub"] else "")
                print(f"  [{st['status']:<8}] {st['id']:<9} {who:<28} {('; '.join(st['evidence']) or st['note'])[:110]}")
            for e in jobs.events(args.job)[-12:]:
                print(f"    {vn(e['at'])}  {e['actor']:<14} {e['action']:<11} {e['detail'][:100]}")
        elif jc == "claim":
            print(f"Đã nhận quyền ghi. fencing token = {jobs.claim(args.job, args.actor, args.ttl)}")
        elif jc == "release":
            print(_job_line(jobs.release(args.job, args.actor, args.token, args.note)))
        elif jc == "heartbeat":
            jobs.heartbeat(args.job, args.actor, args.token, args.ttl); print("Đã gia hạn lease.")
        elif jc == "start":
            jobs.step_start(args.job, args.step, args.actor, args.token, args.sub); print(f"Bắt đầu bước {args.step}.")
        elif jc == "done":
            jobs.step_done(args.job, args.step, args.actor, args.token, args.evidence, args.note); print(f"Xong bước {args.step}.")
        elif jc == "skip":
            jobs.step_done(args.job, args.step, args.actor, args.token, [], args.note, skipped=True); print(f"Bỏ qua bước {args.step}.")
        elif jc == "fail":
            jobs.step_fail(args.job, args.step, args.actor, args.token, args.note); print(f"Ghi lỗi bước {args.step}.")
        elif jc == "set":
            f = {k: v for k, v in (("tool_id", args.tool_id), ("version", args.version)) if v}
            jobs.set_fields(args.job, args.actor, args.token, **f); print("Đã ghi.")
        elif jc == "move":
            print(_job_line(jobs.move(args.job, args.state, args.actor, args.token, args.note)))
        return 0
    if c == "sub":
        subs = SubAgents(root, d)
        if args.scmd == "spawn":
            _j(subs.spawn(branch=args.branch, assigned_by=args.by, job_id=args.job, step=args.step, output=args.output,
                          worker=args.worker, limits={"max_runtime_s": args.minutes * 60}))
        elif args.scmd == "finish":
            r = subs.finish(args.id, args.by, args.ok, args.result, args.evidence)
            print(f"{r['id']} → {r['state']}")
        else:
            rows = subs.list(args.state)
            for r in rows:
                print(f"{r['id']:<12} {r['state']:<8} {r['branch_name']:<18} giao bởi {r['assigned_by']:<14} việc {r['job_id'] or '-'}/{r['step'] or '-'} → {r['output'][:60]}")
            print(f"({len(rows)} Agent con; đang chạy tối đa {subs.max_concurrent})")
        return 0
    if c == "intake":
        jobs.get(args.job)
        ib = Inbox(root)
        for it in args.items:
            man = ib.add(args.job, it, private=not args.public, pointer=args.pointer)
        for i in man["items"]:
            print(f"  {i['kind']:<7} {i.get('name') or i.get('url') or i.get('path')}  {i.get('bytes', '')}  {'riêng tư' if i.get('private') else 'vào git'}")
        for sk in man["skipped"]:
            print(f"  BỎ QUA {sk['name']}: {sk['why']}")
        return 0
    if c == "frames":
        at = [float(x) for x in args.at.split(",")] if args.at else None
        meta = video.extract_frames(Path(args.video), jobs.job_dir(args.job), every=args.every, at=at, max_frames=args.max)
        print(f"Video {meta['video']}: {meta['duration']} giây, {meta['width']}x{meta['height']}, {meta['fps']} hình/giây; cách lấy: {meta['method']}")
        for f in meta["frames"]:
            print(f"  {f['t']:>7.2f}s  {f['file']}")
        print(f"Ảnh ghép: {meta['contact_sheet']}")
        return 0
    if c == "test":
        bd = Path(args.build_dir)
        m = read_json(bd / "manifest.json")
        errs = validate_manifest(m, bd)
        rep = sandbox.run_cases(bd, m) if not errs else {"ok": False, "lint": errs, "cases": [], "isolation": None}
        if args.json:
            _j(rep)
        else:
            for x in rep["lint"]:
                print(f"  LỖI {x}")
            for cs in rep["cases"]:
                print(f"  {'ĐẠT ' if cs['ok'] else 'TRƯỢT'} {cs['name']}  {cs['detail']}")
            print(f"{'ĐẠT' if rep['ok'] else 'KHÔNG ĐẠT'}: {sum(x['ok'] for x in rep['cases'])}/{len(rep['cases'])} ca; cách ly: {rep['isolation']}")
        return 0 if rep["ok"] else 1
    if c == "publish":
        sched = Scheduler(root, d)
        job = jobs.get(args.job) if args.job else None
        sess = sched.get(args.session) if args.session else None
        rel = Catalog(root, d).publish(Path(args.build_dir), args.actor, job=job, session=sess, auto=args.auto)
        print(f"ĐÃ PHÁT HÀNH {rel['tool_id']}@{rel['version']} bởi {rel['published_by']} lúc {vn(rel['published_at'])} "
              f"({len(rel['files'])} tệp, {len(rel['test']['cases'])} ca thử đạt)")
        return 0
    if c == "smoke":
        extra = []
        for spec in args.case:
            ins, exp = {}, {}
            for part in spec.split(","):
                k, _, v = part.partition("=")
                if k.startswith("expect:"):
                    exp[k[7:]] = v
                else:
                    ins[k] = v
            extra.append({"inputs": ins, "expected": exp})
        res = release.smoke(args.tool, args.caller, root=root, extra=extra, rollback_actor=args.rollback_as,
                            session=args.session, trigger=args.trigger)
        if args.json:
            _j(res)
        else:
            for cs in res["cases"]:
                print(f"  {'ĐẠT ' if cs['ok'] else 'TRƯỢT'} {cs['name']}  {cs['detail']}  [{cs['run_id']}]")
            print(f"{'GỌI THỬ TỪ KHO: ĐẠT' if res['ok'] else 'GỌI THỬ TỪ KHO: LỖI'} — {res['tool']}@{res['version']}, người gọi {res['caller']}")
            if res["rolled_back"]:
                rb = res["rolled_back"]
                print(f"ĐÃ KHÔI PHỤC: thu hồi {rb['withdrawn']}, bản hiện hành {rb['current_version'] or '(không còn)'}")
        return 0 if res["ok"] else 1
    if c == "rollback":
        r = Catalog(root, d).rollback(args.tool, args.actor, args.reason, args.to)
        print(f"Đã thu hồi {r['tool_id']}@{r['withdrawn']}; bản hiện hành: {r['current_version'] or '(không còn bản gọi được)'}")
        return 0
    if c == "skill":
        sk = Skills(root, d)
        if args.kcmd == "add":
            v = sk.add(Path(args.path), granted_by=args.granted_by, actor=args.actor, source=args.source,
                       test_result=args.test, test_evidence=args.evidence, note=args.note)
            print(f"Đã lưu {v['path']} (v{v['version']}, {v['status']}, cấp bởi {v['granted_by']})")
        elif args.kcmd == "list":
            for s in sk.registry()["skills"].values():
                print(f"{s['name']:<28} hiện hành: v{s['current']}  · " + ", ".join(f"v{v['version']}:{v['status']}({v['granted_by']})" for v in s["versions"]))
        else:
            r = sk.rollback(args.name, args.actor, args.reason)
            print(f"Đã thu hồi {r['name']} v{r['withdrawn']}; bản hiện hành: {('v' + str(r['current'])) if r['current'] else '(không còn)'}")
        return 0
    if c == "msg":
        mb = Mailbox(root, d)
        if args.mcmd == "send":
            print(mb.send(args.to, args.sender, args.subject, args.body, args.job)["id"])
        elif args.mcmd == "list":
            for msg in mb.list(args.agent, args.state):
                print(f"{msg['id']}  {msg['state']:<5} từ {msg['from']:<14} {msg['subject']}")
        else:
            mb.close(args.agent, args.msg, args.actor, args.note); print("Đã đóng tin.")
        return 0
    if c == "mem":
        print(Memory(root, d).append(args.agent, args.actor, args.text)); return 0
    if c == "at":
        sched = Scheduler(root, d)
        ac = args.acmd
        if ac == "begin":
            _j(sched.begin(args.agent, args.marker, args.trigger))
        elif ac == "github":
            gh = sched.check_github()
            sched.attach_github(args.run, gh)
            _j(gh)
        elif ac == "plan":
            s = sched.get(args.run)
            reqs = read_json(Path(args.requests)) if args.requests else None
            _j(sched.plan(args.run, s.get("github") or sched.check_github(), reqs))
        elif ac == "note":
            sched.note(args.run, args.text); print("Đã ghi.")
        elif ac == "release":
            sched.register_release(args.run, args.tool, args.version, args.smoke); print("Đã ghi bản phát hành của phiên.")
        elif ac == "candidate":
            sched.mark_candidate(args.source, args.key, args.outcome, args.note); print("Đã ghi ứng viên.")
        elif ac == "end":
            _j(sched.end(args.run, args.result, args.note))
        else:
            _j({"schedule": sched.schedule(), "lock": read_json(sched.lock_path, default={"state": "FREE"}),
                "last_sessions": [{k: s[k] for k in ("run_id", "trigger", "result", "started_at")} for s in sched.list(8)]})
        return 0
    if c == "schedule":
        sched = Scheduler(root, d)
        if args.sccmd == "set":
            f = {}
            if args.paused:
                f["paused"] = args.paused == "true"
            if args.trigger_id:
                f["trigger_id"] = args.trigger_id
            if args.worker_trigger_id:
                f["worker_trigger_id"] = args.worker_trigger_id
            sched.set_schedule(args.actor, **f)
        _j(sched.schedule()); return 0
    if c == "budget":
        b = Budget(root)
        if args.bcmd == "approve":
            b.approve(args.amount, args.what, args.ref)
        _j(dict(b.load(), remaining=b.remaining())); return 0
    if c == "export":
        for pth in export_mod.write(Path(args.out), root):
            print(pth)
        _j({"if_version": read_json(root / "runs" / "web_versions.json", default={})})
        return 0
    if c == "webver":
        pth = root / "runs" / "web_versions.json"
        cur = read_json(pth, default={})
        for k, v in _kv(args.pairs).items():
            if k not in ("main", "catalog", "jobs", "runs") or not v.isdigit():
                raise ToolError("Dùng: webver main=<số> catalog=<số> jobs=<số> runs=<số>")
            cur[k] = int(v)
        if args.pairs:
            atomic_write_json(pth, cur)
        _j(cur)
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = tool_root()
    try:
        if args.sync:
            gs = GitSync()
            label = " ".join(str(x) for x in (argv or sys.argv[1:]) if x != "--sync")[:110]
            with gs.tx(f"tool: {label}"):
                rc = dispatch(args, root)
            return rc
        return dispatch(args, root)
    except ToolError as e:
        print(f"TỪ CHỐI: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
