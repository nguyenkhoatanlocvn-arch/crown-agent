"""Xuất trạng thái THẬT của TOOL thành 4 tài liệu cho web Hoàng Thành (collection `tool_state`).

Không có số liệu tự bịa: mọi con số đọc từ tệp trong tool/ (kho, sổ việc, bản ghi lượt chạy, bản ghi phiên).
  tool_state/main     tổng quan, Agent & nhánh, lịch, ngân sách, skill, nguồn GitHub
  tool_state/catalog  kho công cụ + ví dụ đầu vào/đầu ra
  tool_state/jobs     sổ việc (mã việc, bước, bằng chứng)
  tool_state/runs     nhật ký lượt chạy tool + phiên AT
"""
from __future__ import annotations

import subprocess
from datetime import timedelta
from pathlib import Path

from .budget import Budget
from .catalog import CATEGORIES, Catalog
from .identity import Directory
from .jobs import JobStore
from .runner import Runner
from .scheduler import Scheduler
from .skills import Skills
from .subagents import SubAgents
from .util import REPO_ROOT, atomic_write_json, iso, parse_iso, read_json, read_jsonl, tool_root, utc_now

TEXT_EXT = {".srt", ".txt", ".md", ".json", ".csv", ".tsv", ".vtt", ".xml", ".html", ".yaml", ".yml", ".ini", ".log"}


def _preview(p: Path, limit: int = 4000) -> dict:
    if not p.is_file():
        return {"name": p.name, "text": None, "note": "không thấy tệp"}
    if p.suffix.lower() in TEXT_EXT:
        t = p.read_text(encoding="utf-8", errors="replace")
        return {"name": p.name, "text": t[:limit], "truncated": len(t) > limit, "bytes": p.stat().st_size}
    return {"name": p.name, "text": None, "bytes": p.stat().st_size, "note": "tệp nhị phân"}


def build(root: Path | None = None) -> dict[str, dict]:
    root = Path(root) if root else tool_root()
    d = Directory(root)
    cat, jobs, subs = Catalog(root, d), JobStore(root, d), SubAgents(root, d)
    sched, skills, runner = Scheduler(root, d), Skills(root, d), Runner(root, d)
    now = utc_now()
    try:
        commit = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        commit = "unknown"

    # ----- kho -----
    tools = []
    for t in cat.tools():
        cur = t.get("current_version")
        entry = {"id": t["id"], "name": t.get("name"), "category": t.get("category"),
                 "category_name": CATEGORIES.get(t.get("category"), "?"), "summary": t.get("summary"),
                 "keywords": t.get("keywords", []), "current_version": cur, "versions": t["versions"],
                 "history": t["history"][-12:], "callable": bool(cur)}
        if cur:
            m = cat.manifest(t["id"], cur)
            vdir = cat.version_dir(t["id"], cur)
            rel = read_json(vdir / "RELEASE.json")
            entry.update(interface=m["interface"], permissions=m["permissions"], source=m["source"],
                         limits=m.get("limits", {}), cost_usd_per_run=m.get("cost_usd_per_run", 0),
                         release={k: rel.get(k) for k in ("published_by", "published_at", "auto", "job_id", "session",
                                                          "builder", "tester", "test")},
                         examples=[{"name": ex.get("name", "mẫu"), "params": ex.get("params", {}),
                                    "inputs": {k: _preview(vdir / v) for k, v in ex["inputs"].items()},
                                    "expected": {k: _preview(vdir / v) for k, v in ex["expected"].items()}}
                                   for ex in m["examples"]],
                         call=f'python -m tool run {t["id"]} --caller <ID Agent> --in '
                              + " ".join(f'{i["name"]}=<tệp>' for i in m["interface"]["inputs"]) + " --out <thư mục>")
        tools.append(entry)

    # ----- việc -----
    job_list = []
    for j in sorted(jobs.list(), key=lambda x: x["created_at"], reverse=True)[:40]:
        ev = jobs.events(j["job_id"])
        job_list.append({k: j.get(k) for k in ("job_id", "title", "prompt", "state", "requested_by", "assigned_to",
                                               "origin", "tool_id", "version", "created_at", "updated_at", "workers")}
                        | {"holder": (j.get("lease") or {}).get("holder"),
                           "lease_until": (j.get("lease") or {}).get("expires_at"),
                           "steps": [{k: s.get(k) for k in ("id", "branch", "what", "status", "by", "sub", "evidence",
                                                            "note", "attempts")} for s in j["steps"]],
                           "inputs": [{k: i.get(k) for k in ("kind", "name", "bytes", "private", "url")} for i in j.get("inputs", [])],
                           "events_tail": ev[-6:], "events_total": len(ev)})

    # ----- lượt chạy + phiên -----
    runs = [{k: r.get(k) for k in ("run_id", "tool", "version", "caller", "caller_kind", "trigger", "session", "purpose",
                                   "started_at", "finished_at", "status", "error", "duration_s", "report", "isolation", "record")}
            | {"inputs": [{"name": i["name"], "file": Path(i["path"]).name, "bytes": i["bytes"]} for i in r.get("inputs", [])],
               "outputs": [{"name": o["name"], "bytes": o["bytes"], "sha256": o["sha256"][:12]} for o in r.get("outputs", [])],
               "access": [{"mode": a["mode"], "allow": a["allow"], "rule": a["rule"], "file": Path(a["path"]).name}
                          for a in r.get("access", [])]}
            for r in runner.history(60)]
    sessions = sched.list(30)
    day_ago = now - timedelta(hours=24)

    # ----- Agent -----
    all_subs = subs.list()
    active = [s for s in all_subs if s["state"] == "ACTIVE" and parse_iso(s["expires_at"]) > now]
    execs = []
    for eid in d.exec_ids:
        e = d.reg.executives[eid]
        adir = root / "agents" / eid
        inbox = sorted((adir / "inbox").glob("*.json")) if (adir / "inbox").is_dir() else []
        holding = [j["job_id"] for j in jobs.list("RUNNING") if (j.get("lease") or {}).get("holder") == eid]
        last = next((s for s in sessions if s["agent"] == eid), None)
        execs.append({"id": eid, "name": e["name"], "short": e.get("short", eid), "role": e["role"],
                      "status": e["status"], "tier": e["tier"], "peer_of": e["peer_of"], "peer_scope": e["peer_scope"],
                      "clone_of": e.get("clone_of"), "memory": f"tool/agents/{eid}/MEMORY.md",
                      "inbox_open": sum(1 for p in inbox if read_json(p).get("state") == "OPEN"),
                      "inbox_total": len(inbox), "holding": holding,
                      "activity": "WORKING" if holding else "IDLE",
                      "last_session": {k: last.get(k) for k in ("run_id", "trigger", "result", "started_at")} if last else None})
    branches = [{"id": b["id"], "name": b["name"], "tier": b["tier"], "mission": b["mission"],
                 "active": [s["id"] for s in active if s["branch"] == b["id"]],
                 "total": sum(1 for s in all_subs if s["branch"] == b["id"])} for b in d.branches.values()]

    sch = sched.schedule()
    lock = read_json(sched.lock_path, default={"state": "FREE"})
    last_sched = next((s for s in sessions if s["trigger"] == "scheduler"), None)
    reg = skills.registry()
    sources = read_json(root / "skills" / "sources" / "SOURCE_REGISTER.json", default={"sources": []})
    watch = read_json(root / "runs" / "watch_state.json", default={"sources": {}})
    main = {
        "updated_at": iso(now), "commit": commit, "version": 1,
        "summary": {
            "tools_callable": sum(1 for t in tools if t["callable"]), "tools_total": len(tools),
            "versions_total": sum(len(t["versions"]) for t in tools),
            "jobs_open": sum(1 for j in job_list if j["state"] in ("READY", "RUNNING", "BLOCKED", "WAITING_APPROVAL")),
            "jobs_done": sum(1 for j in job_list if j["state"] == "VERIFIED_DONE"),
            "runs_24h": sum(1 for r in runs if parse_iso(r["started_at"]) >= day_ago),
            "runs_denied_24h": sum(1 for r in runs if r["status"] == "DENIED" and parse_iso(r["started_at"]) >= day_ago),
            "skills_current": sum(1 for s in reg["skills"].values() if s["current"]),
        },
        "agents": {"executives": execs, "branches": branches, "max_concurrent": subs.max_concurrent,
                   "active": [s["id"] for s in active],
                   "subagents": [{k: s.get(k) for k in ("id", "name", "branch", "branch_name", "state", "assigned_by",
                                                        "job_id", "step", "output", "tier", "limits", "worker",
                                                        "created_at", "finished_at", "result")}
                                 for s in sorted(all_subs, key=lambda x: x["created_at"], reverse=True)[:30]]},
        "schedule": {**sch, "lock": lock,
                     "last_scheduler_session": last_sched and {k: last_sched.get(k) for k in
                                                               ("run_id", "result", "started_at", "finished_at", "trigger_basis")},
                     "sessions_recorded": len(sessions),
                     "activation": {
                         "prompt": "Em là Thiên Công AT (agent-tool-at). Gắn repo nguyenkhoatanlocvn-arch/crown-agent, "
                                   "đọc tool/agents/agent-tool-at/CHARTER.md và chạy một phiên tự tích hợp theo đó.",
                         "cli": "python -m tool at begin --agent agent-tool-at --marker \"MANUAL: chạy tay\""}},
        "budget": {k: Budget(root).load().get(k) for k in ("currency", "paid_services_cap", "approved", "spent", "source")}
                  | {"remaining": Budget(root).remaining()},
        "skills": [{"name": s["name"], "current": s["current"],
                    "versions": [{k: v.get(k) for k in ("version", "granted_by", "recorded_by", "source", "status", "at",
                                                        "test", "description", "note", "path")} for v in s["versions"]],
                    "history": s.get("history", [])} for s in sorted(reg["skills"].values(), key=lambda x: x["name"])],
        "sources": [{k: s.get(k) for k in ("id", "repo", "url", "commit", "license", "function_to_take", "integration",
                                            "cost", "checked_at", "vi")} | {"watch": watch["sources"].get(s.get("id"))}
                    for s in sources.get("sources", [])],
        "sources_checked_at": sources.get("checked_at"),
        "categories": CATEGORIES,
    }
    return {"main": main, "catalog": {"updated_at": iso(now), "tools": tools},
            "jobs": {"updated_at": iso(now), "jobs": job_list},
            "runs": {"updated_at": iso(now), "runs": runs, "sessions": sessions}}


def write(out_dir: Path, root: Path | None = None) -> list[Path]:
    out_dir = Path(out_dir)
    docs = build(root)
    paths = []
    for name, doc in docs.items():
        p = out_dir / "tool_state" / f"{name}.json"
        atomic_write_json(p, doc)
        paths.append(p)
    return paths
