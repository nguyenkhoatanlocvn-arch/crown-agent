"""Gọi thử một tool NGAY TỪ KHO CHUNG sau khi phát hành; lỗi thì tự khôi phục bản tốt.

Khác với ca thử trong workbench: ở đây tool được gọi qua đúng đường mà mọi Agent sẽ gọi (Runner: danh bạ, quyền dữ liệu,
đối chiếu băm, vùng cách ly), bởi một Agent thường, với ví dụ trong manifest và dữ liệu KHÁC mẫu nếu có.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from .catalog import Catalog
from .identity import Directory
from .runner import Runner
from .util import ToolError, tool_root


def smoke(tool_ref: str, caller: str, *, root: Path | None = None, extra: list[dict] | None = None,
          rollback_actor: str | None = None, session: str | None = None, trigger: str = "manual") -> dict:
    """Trả {"ok", "cases": [...], "rolled_back": {...}|None}. extra: [{"inputs": {tên: tệp}, "expected": {tên kết quả: tệp}}]."""
    root = Path(root) if root else tool_root()
    d = Directory(root)
    cat, runner = Catalog(root, d), Runner(root, d)
    tool_id, version = cat.resolve(tool_ref)
    m, vdir = cat.manifest(tool_id, version), cat.version_dir(tool_id, version)
    cases = [{"name": f"ví dụ:{ex.get('name', 'mẫu')}", "inputs": {k: vdir / v for k, v in ex["inputs"].items()},
              "expected": {k: vdir / v for k, v in ex["expected"].items()}, "params": ex.get("params", {})}
             for ex in m["examples"]]
    for i, ex in enumerate(extra or []):
        cases.append({"name": ex.get("name", f"dữ liệu khác mẫu #{i + 1}"),
                      "inputs": {k: Path(v) for k, v in ex["inputs"].items()},
                      "expected": {k: Path(v) for k, v in ex["expected"].items()}, "params": ex.get("params", {})})
    out = {"tool": tool_id, "version": version, "caller": caller, "ok": True, "cases": [], "rolled_back": None}
    for c in cases:
        with tempfile.TemporaryDirectory(prefix="toolsmoke-") as td:
            rec = runner.run(f"{tool_id}@{version}", caller, c["inputs"], params=c["params"], out_dir=td,
                             purpose=f"gọi thử từ kho: {c['name']}", trigger=trigger, session=session)
            problems = []
            if rec["status"] != "OK":
                problems.append(f"{rec['status']}: {rec['error']}")
            else:
                for name, exp in c["expected"].items():
                    got = Path(td) / name
                    if not got.is_file():
                        problems.append(f"thiếu tệp kết quả '{name}'")
                    elif got.read_bytes() != Path(exp).read_bytes():
                        problems.append(f"'{name}' khác mong đợi")
            out["cases"].append({"name": c["name"], "ok": not problems, "detail": "; ".join(problems),
                                 "run_id": rec["run_id"], "record": rec["record"]})
    out["ok"] = all(c["ok"] for c in out["cases"])
    if not out["ok"] and rollback_actor:
        bad = "; ".join(f"{c['name']}: {c['detail']}" for c in out["cases"] if not c["ok"])
        out["rolled_back"] = cat.rollback(tool_id, rollback_actor, f"gọi thử từ kho chung lỗi — {bad}"[:400])
    return out
