"""Xuất trạng thái THẬT của các Agent từ sổ đăng ký + sổ nhiệm vụ cho phòng 3D.

Không đoán: hoạt động của mỗi Agent suy ra từ nhiệm vụ trong runtime/tasks.db.
  WORKING  = đang giữ lease một nhiệm vụ RUNNING còn hạn
  REVIEW   = nhiệm vụ của mình đang chờ duyệt (kết quả hoặc quyết định Chủ tịch)
  BLOCKED  = nhiệm vụ của mình bị chặn / hết hạn mức / gián đoạn
  WAITING  = có nhiệm vụ DRAFT/READY chưa ai nhận
  IDLE     = ACTIVE nhưng không có việc
  OFF      = mới đăng ký, chưa kích hoạt
In ra JSON: {"meta": {...}, "members": [...]}.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crown import registry as R  # noqa: E402
from crown.tasks import TaskStore  # noqa: E402

reg = R.load()
db = Path(os.environ.get("CROWN_DB", ROOT / "runtime" / "tasks.db"))
store = TaskStore(db, registry=reg)
tasks = store.list()
now = datetime.now(timezone.utc)

PRIORITY = ["WORKING", "REVIEW", "BLOCKED", "WAITING", "IDLE", "OFF"]


def activity_of(mid: str, status: str):
    found = []
    for t in tasks:
        lease_ok = t["lease_expiry"] and datetime.fromisoformat(t["lease_expiry"]) > now
        if t["state"] == "RUNNING" and t["lease_owner"] == mid and lease_ok:
            found.append(("WORKING", t))
        elif t["owner_agent_id"] == mid:
            if t["state"] in ("REVIEW", "WAITING_APPROVAL"):   # chờ duyệt → lồng đèn vàng
                found.append(("REVIEW", t))
            elif t["state"] in ("BLOCKED", "PAUSED_QUOTA", "INTERRUPTED"):
                found.append(("BLOCKED", t))
            elif t["state"] in ("DRAFT", "READY"):
                found.append(("WAITING", t))
    if not found:
        return ("IDLE" if status == "ACTIVE" else "OFF"), None, 0
    found.sort(key=lambda x: PRIORITY.index(x[0]))
    act, t = found[0]
    last = store.events(t["task_id"])[-1]
    return act, {"id": t["task_id"], "objective": t["objective"], "state": t["state"],
                 "since": last["at"], "note": last["note"] or ""}, len(found)


members = []
for lid, l in reg.leaders.items():
    act, task, n = activity_of(lid, l["status"])
    members.append({"id": lid, "name": l["name"], "kind": "leader", "boss": l.get("supervisor"),
                    "cluster": "Lãnh đạo", "status": l["status"], "tier": l["tier"],
                    "activity": act, "task": task, "open_tasks": n})
for aid, a in reg.agents.items():
    act, task, n = activity_of(aid, a["status"])
    members.append({"id": aid, "name": a["name"], "kind": "agent", "boss": a["leader"],
                    "cluster": a["cluster"], "status": a["status"], "tier": a["tier"],
                    "activity": act, "task": task, "open_tasks": n})
for sid, a in reg.subagents.items():   # Agent con (A03.1...)
    act, task, n = activity_of(sid, a["status"])
    members.append({"id": sid, "name": a["name"], "kind": "agent", "boss": a["parent"],
                    "cluster": reg.agents[a["parent"]]["cluster"], "status": a["status"], "tier": a["tier"],
                    "activity": act, "task": task, "open_tasks": n})

try:
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()
except Exception:
    commit = "unknown"

counts = {s: sum(m["activity"] == s for m in members) for s in PRIORITY}
meta = {"synced_at": now.isoformat(), "source": "crown-agent runtime/tasks.db (sandbox cloud)",
        "commit": commit, "tasks_total": len(tasks), "counts": counts,
        "tasks": [{"id": t["task_id"], "owner": t["owner_agent_id"], "state": t["state"],
                   "objective": t["objective"]} for t in tasks]}
store.close()
print(json.dumps({"meta": meta, "members": members}, ensure_ascii=False))
