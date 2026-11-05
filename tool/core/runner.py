"""Gọi một tool đã phát hành từ kho chung, thay mặt một Agent trong danh bạ.

Mỗi lần chạy: xác định người gọi → kiểm quyền truy cập từng tệp đầu vào/nơi ghi kết quả →
đối chiếu băm phiên bản → chạy trong vùng cách ly → ghi bản ghi lượt chạy vào runs/.
Bản ghi chỉ lưu đường dẫn + băm + kết quả, không lưu nội dung dữ liệu của người gọi.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import sandbox
from .access import Access, AccessDenied
from .budget import Budget
from .catalog import Catalog
from .identity import Directory
from .util import ToolError, atomic_write_json, iso, new_id, read_json, sha256_file, stamp, tool_root, utc_now

TRIGGERS = {"manual", "scheduler", "web", "test"}


class Runner:
    def __init__(self, root: Path | None = None, directory: Directory | None = None,
                 clock: Callable[[], datetime] = utc_now, repo_root: Path | None = None):
        self.root = Path(root) if root else tool_root()
        self.dir = directory or Directory(self.root)
        self.clock = clock
        self.catalog = Catalog(self.root, self.dir, clock)
        self.access = Access(self.root, self.dir, repo_root)
        self.runs = self.root / "runs"

    def _record(self, rec: dict) -> dict:
        d = self.runs / stamp(self.clock(), "%Y-%m-%d") / rec["run_id"]
        d.mkdir(parents=True, exist_ok=True)
        rec["record"] = (d / "run.json").relative_to(self.root).as_posix()
        atomic_write_json(d / "run.json", rec)
        return rec

    def run(self, tool_ref: str, caller: str, inputs: dict[str, str | Path], *, params: dict | None = None,
            out_dir: str | Path | None = None, purpose: str = "", trigger: str = "manual",
            session: str | None = None) -> dict:
        if trigger not in TRIGGERS:
            raise ToolError(f"trigger phải là một trong: {', '.join(sorted(TRIGGERS))}")
        run_id = new_id("R", self.clock(), "%Y%m%d-%H%M%S")
        rec = {"run_id": run_id, "tool": tool_ref, "version": None, "caller": caller, "caller_kind": None,
               "caller_auth": None, "trigger": trigger, "session": session, "purpose": purpose,
               "started_at": iso(self.clock()), "finished_at": None, "status": "ERROR", "error": None,
               "inputs": [], "outputs": [], "access": [], "report": None, "isolation": None,
               "exit_code": None, "duration_s": None}
        run_dir = self.runs / stamp(self.clock(), "%Y-%m-%d") / run_id
        work = None
        try:
            who = self.dir.resolve(caller)  # người gọi phải có trong danh bạ và không bị tắt
            rec.update(caller_kind=who["kind"], caller_auth=who["auth"])
            tool_id, version = self.catalog.resolve(tool_ref)
            rec.update(tool=tool_id, version=version)
            m = self.catalog.manifest(tool_id, version)
            self.catalog.verify_integrity(tool_id, version)
            Budget(self.root).require(float(m.get("cost_usd_per_run", 0)), f"chạy {tool_id}@{version}", caller)
            declared = {i["name"]: i for i in m["interface"]["inputs"]}
            for name in inputs:
                if name not in declared:
                    raise ToolError(f"Tool không có đầu vào '{name}' (có: {', '.join(declared)})")
            for name, spec in declared.items():
                if spec.get("required") and name not in inputs:
                    raise ToolError(f"Thiếu đầu vào bắt buộc '{name}'")
            resolved: dict[str, Path] = {}
            for name, p in inputs.items():
                dec = self.access.decide(caller, p, "read")
                rec["access"].append(dec)
                if not dec["allow"]:
                    raise AccessDenied(f"TỪ CHỐI: {caller} không được đọc {dec['path']} — {dec['rule']}")
                src = Path(dec["path"])
                if not src.is_file():
                    raise ToolError(f"Đầu vào '{name}' không tồn tại: {src}")
                exts = declared[name].get("ext")
                if exts and src.suffix.lower() not in [x.lower() for x in exts]:
                    raise ToolError(f"Đầu vào '{name}' phải có đuôi {', '.join(exts)}")
                rec["inputs"].append({"name": name, "path": dec["path"], "bytes": src.stat().st_size,
                                      "sha256": sha256_file(src)})
                resolved[name] = src
            dest = None
            if out_dir is not None:
                dec = self.access.decide(caller, out_dir, "write", run_dir=run_dir)
                rec["access"].append(dec)
                if not dec["allow"]:
                    raise AccessDenied(f"TỪ CHỐI: {caller} không được ghi vào {dec['path']} — {dec['rule']}")
                dest = Path(dec["path"])
            r = sandbox.execute(self.catalog.version_dir(tool_id, version), m, resolved, params)
            work = r["work"]
            rec.update(exit_code=r["exit_code"], duration_s=r["duration_s"], report=r["report"],
                       isolation=r["isolation"])
            if r["timed_out"]:
                rec.update(status="TIMEOUT", error=f"quá {r['isolation']['timeout_s']} giây")
            elif r["exit_code"] != 0:
                rec.update(status="ERROR", error=(r["stderr_tail"].strip() or f"mã thoát {r['exit_code']}")[-600:])
            else:
                if dest is not None:  # xét quyền với TỪNG tệp kết quả (tên tệp do tool đặt) trước khi ghi bất kỳ tệp nào
                    for relname in r["outputs"]:
                        dec = self.access.decide(caller, dest / relname, "write", run_dir=run_dir)
                        if not dec["allow"]:
                            rec["access"].append(dec)
                            raise AccessDenied(f"TỪ CHỐI: {caller} không được ghi {dec['path']} — {dec['rule']}")
                keep = run_dir / "out"
                keep.mkdir(parents=True, exist_ok=True)
                for relname, src in r["outputs"].items():
                    for base in [keep] + ([dest] if dest else []):
                        tgt = base / relname
                        tgt.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(src, tgt)
                    rec["outputs"].append({"name": relname, "bytes": src.stat().st_size, "sha256": sha256_file(src),
                                           "path": ((dest or keep) / relname).as_posix()})
                rec["status"] = "OK"
        except AccessDenied as e:
            rec.update(status="DENIED", error=str(e))
        except ToolError as e:
            rec.update(status="ERROR", error=str(e))
        finally:
            if work is not None:
                sandbox.cleanup(work)
            rec["finished_at"] = iso(self.clock())
            self._record(rec)
        return rec

    def history(self, limit: int = 50) -> list[dict]:
        recs = [read_json(p) for p in self.runs.glob("*/*/run.json")]
        recs.sort(key=lambda r: r["started_at"], reverse=True)
        return recs[:limit]
