"""CLI CROWN AGENT.

  python -m crown org                  Sơ đồ tổ chức
  python -m crown agent A03            Hồ sơ một Agent
  python -m crown companies            Công ty con
  python -m crown legacy               Bảng chuyển 25 mã KING AI cũ
  python -m crown check                Kiểm tra sổ đăng ký
  python -m crown task new OWNER "mục tiêu" "tiêu chí xong" --actor ID [--company X] [--key K] [--ready]
  python -m crown task list [--state S]
  python -m crown task claim TASK WORKER [--ttl 900]
  python -m crown task record TASK WORKER TOKEN [--artifact A] [--evidence E] [--next N]
  python -m crown task move TASK STATE ACTOR [--token N] [--note "..."] [--approval "mã quyết định"]
  python -m crown task show TASK
DB mặc định: runtime/tasks.db (đổi bằng biến CROWN_DB).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import registry as R
from .tasks import TaskError, TaskStore, to_vn

STATUS_ICON = {"ACTIVE": "●", "REGISTERED": "○", "DISABLED": "✕"}


def _db_path() -> Path:
    p = Path(os.environ.get("CROWN_DB", R.ROOT / "runtime" / "tasks.db"))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def cmd_org(reg: R.Registry) -> None:
    def leader_line(lid, depth):
        l = reg.leaders[lid]
        print("  " * depth + f"{STATUS_ICON.get(l['status'], '?')} {lid} — {l['name']} [{l['tier']}]")
        for aid in reg.team_of(lid):
            a = reg.agents[aid]
            print("  " * (depth + 1) + f"{STATUS_ICON[a['status']]} {aid} {a['name']} [{a['tier']}]")
        for cid, c in sorted(reg.companies.items()):
            if c["owner"] == lid:
                print("  " * (depth + 1) + f"▣ Công ty {cid} — {c['status']}")
        for uid, u in sorted(reg.units.items()):
            if u.get("supervisor") == lid:
                via = " (không qua " + ", ".join(u["bypass"]) + ")" if u.get("bypass") else ""
                print("  " * (depth + 1) + f"◆ {uid} — {u['name']} [{u['tier']}]{via}")
        for sid, st in sorted(reg.tn_seats.items()):
            if not st.get("holder") and st.get("leader") == lid:
                print("  " * (depth + 1) + f"{STATUS_ICON.get(st['status'], '?')} {sid} {st['name']} [{st['tier']}]")
        order = {"THIEN_NAO": 0, "VUA_CEO": 1, "CFO": 2, "CMO-01": 3, "CMO-02": 4}
        for sub in sorted((x for x, v in reg.leaders.items() if v.get("supervisor") == lid),
                          key=lambda x: (order.get(x, 9), x)):
            leader_line(sub, depth + 1)

    leader_line("CHAIRMAN", 0)
    n_active = sum(a["status"] == "ACTIVE" for a in reg.agents.values())
    print(f"\n● đang giao việc  ○ đã đăng ký, chưa kích hoạt   |   Agent ACTIVE: {n_active}/20")


def cmd_agent(reg: R.Registry, aid: str) -> int:
    if aid not in reg.agents:
        print(f"Không có Agent {aid}", file=sys.stderr)
        return 1
    a = reg.agents[aid]
    print(f"{aid} — {a['name']}")
    for k, label in [("status", "Trạng thái"), ("tier", "Mức quyền"), ("cluster", "Cụm"), ("mission", "Nhiệm vụ"),
                     ("memory_ns", "Bộ nhớ"), ("out_of_scope", "Không thuộc việc")]:
        print(f"  {label}: {a.get(k, '-')}")
    print(f"  Chuỗi chỉ huy: {' → '.join(reg.chain(aid))}")
    print(f"  Vùng ghi: {', '.join(a['write_scope'])}")
    print(f"  Skill: {', '.join(a['skills']) or '(chưa gán)'}")
    return 0


def cmd_companies(reg: R.Registry) -> None:
    for cid, c in reg.companies.items():
        print(f"{cid:<14} chủ quản {c['owner']:<8} {c['status']:<18} {c['name']}")
        print(f"{'':<14} {c['fact']}")


def cmd_legacy(reg: R.Registry) -> None:
    for code, u in reg.legacy.items():
        print(f"{code:<24} → {', '.join(u['maps_to']):<22} {u['status']}")
    print(f"\nTổng: {len(reg.legacy)} mã cũ")


def _print_task(t: dict) -> None:
    lease = f"{t['lease_owner']} tới {to_vn(t['lease_expiry'])}" if t["lease_owner"] else "-"
    print(f"{t['task_id']}  [{t['state']}]  chủ: {t['owner_agent_id']}  công ty: {t['company_id'] or '-'}")
    print(f"  Mục tiêu: {t['objective']}")
    print(f"  Xong khi: {t['acceptance_criteria']}")
    print(f"  Đang giữ: {lease}  token={t['fencing_token']}  thử lại={t['retry_count']}")
    if t["evidence_refs"]:
        print(f"  Bằng chứng: {'; '.join(t['evidence_refs'])}")
    if t["next_action"]:
        print(f"  Bước tiếp: {t['next_action']}")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["vua"]:  # lớp Vương Tọa → Agent Vua, in JSON cho workflow
        from . import vua
        return vua.main(argv[1:])
    p = argparse.ArgumentParser(prog="crown", description="CROWN AGENT — sổ đăng ký và sổ nhiệm vụ")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("org"); sub.add_parser("companies"); sub.add_parser("legacy"); sub.add_parser("check")
    sa = sub.add_parser("agent"); sa.add_argument("id")
    st = sub.add_parser("task"); ts = st.add_subparsers(dest="tcmd", required=True)
    n = ts.add_parser("new"); n.add_argument("owner"); n.add_argument("objective"); n.add_argument("criteria")
    n.add_argument("--company"); n.add_argument("--key"); n.add_argument("--ready", action="store_true")
    n.add_argument("--actor", required=True, help="ID người tạo trong sổ đăng ký")
    l = ts.add_parser("list"); l.add_argument("--state")
    c = ts.add_parser("claim"); c.add_argument("task"); c.add_argument("worker"); c.add_argument("--ttl", type=int, default=900)
    r = ts.add_parser("record"); r.add_argument("task"); r.add_argument("worker"); r.add_argument("token", type=int)
    r.add_argument("--artifact"); r.add_argument("--evidence"); r.add_argument("--next")
    m = ts.add_parser("move"); m.add_argument("task"); m.add_argument("state"); m.add_argument("actor")
    m.add_argument("--token", type=int); m.add_argument("--note", default=""); m.add_argument("--approval")
    s = ts.add_parser("show"); s.add_argument("task")
    args = p.parse_args(argv)

    try:
        reg = R.load()
    except R.RegistryError as e:
        print(e, file=sys.stderr)
        return 2

    if args.cmd == "check":
        print(f"OK — {len(reg.leaders)} lãnh đạo, {len(reg.agents)} Agent, {len(reg.companies)} công ty, {len(reg.legacy)} mã cũ")
        if getattr(reg, "has_tn_file", False):          # có Thiên Não → kiểm cả bảng quyền và khóa hiến pháp
            from . import policy as P
            try:
                pol = P.Policy.load(reg)
            except P.PolicyError as e:
                print(e, file=sys.stderr)
                return 3
            bad = pol.verify_lock(R.ROOT)
            if bad:
                print("Tệp quyền/hiến pháp khác bản đã khóa (registries/CONSTITUTION.lock):", file=sys.stderr)
                for x in bad:
                    print(f"  - {x['path']}: {x['problem']}", file=sys.stderr)
                return 3
            print(f"OK — bảng quyền hợp lệ; khóa hiến pháp khớp {len(pol.protected_files(R.ROOT))} tệp; "
                  f"{len(reg.tn_seats)} ghế TN, {len(reg.units)} đơn vị dưới Thiên Não")
        return 0
    if args.cmd == "org":
        cmd_org(reg); return 0
    if args.cmd == "agent":
        return cmd_agent(reg, args.id)
    if args.cmd == "companies":
        cmd_companies(reg); return 0
    if args.cmd == "legacy":
        cmd_legacy(reg); return 0

    store = TaskStore(_db_path(), registry=reg)
    try:
        if args.tcmd == "new":
            _print_task(store.create(owner_agent_id=args.owner, objective=args.objective,
                                     acceptance_criteria=args.criteria, actor=args.actor,
                                     company_id=args.company, idempotency_key=args.key, ready=args.ready))
        elif args.tcmd == "list":
            rows = store.list(args.state)
            for t in rows:
                print(f"{t['task_id']}  {t['state']:<16} {t['owner_agent_id']:<8} {t['objective']}")
            print(f"({len(rows)} nhiệm vụ)")
        elif args.tcmd == "claim":
            print(f"Đã nhận. fencing token = {store.claim(args.task, args.worker, args.ttl)}")
        elif args.tcmd == "record":
            store.record(args.task, args.worker, args.token, artifact=args.artifact, evidence=args.evidence,
                         next_action=args.next)
            print("Đã ghi.")
        elif args.tcmd == "move":
            _print_task(store.move(args.task, args.state, args.actor, token=args.token, note=args.note,
                                   approval=args.approval))
        elif args.tcmd == "show":
            _print_task(store.get(args.task))
            for e in store.events(args.task):
                print(f"    {to_vn(e['at'])}  {e['actor']:<10} {e['from_state'] or '·'} → {e['to_state']}  {e['note']}")
    except TaskError as e:
        print(f"TỪ CHỐI: {e}", file=sys.stderr)
        return 1
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
