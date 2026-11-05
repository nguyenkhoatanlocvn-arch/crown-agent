"""Kho công cụ dùng chung: phát hành theo mã tool + phiên bản, tìm kiếm, khôi phục phiên bản.

catalog/<tool_id>/tool.json                 hồ sơ tool: phiên bản hiện hành, lịch sử phát hành/quay lui
catalog/<tool_id>/<version>/manifest.json   mô tả, giao diện vào/ra, quyền, ví dụ, nguồn
catalog/<tool_id>/<version>/RELEASE.json    người phát hành, mã việc, băm từng tệp, kết quả thử
Thư mục phiên bản bất biến sau khi phát hành: mỗi lần chạy đều đối chiếu băm.

Chống phát hành trùng: quyền phát hành một phiên bản được giành bằng một tệp đánh dấu tạo kiểu
O_CREAT|O_EXCL (nguyên tử). Hai Agent cùng phát hành một phiên bản thì đúng một bên thắng.
"""
from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import sandbox
from .budget import Budget
from .identity import Directory
from .util import (ToolError, atomic_write_json, fold, iso, mutex, read_json, sha256_file, tool_root, utc_now)

CATEGORIES = {
    "video-image": "Video & ảnh",
    "data-files": "Dữ liệu & tệp",
    "web-app": "Web & ứng dụng",
    "automation": "Tự động hóa",
    "testing": "Kiểm thử",
}
ID_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
VER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
RUNTIMES = {"python"}


def ver_key(v: str) -> tuple[int, int, int]:
    return tuple(int(x) for x in v.split("."))  # type: ignore[return-value]


def validate_manifest(m: dict, tool_dir: Path | None = None) -> list[str]:
    """Trả danh sách lỗi của manifest (rỗng = hợp lệ)."""
    e: list[str] = []
    if not isinstance(m, dict):
        return ["manifest phải là object JSON"]
    if not isinstance(m.get("id"), str) or not ID_RE.match(m.get("id", "")) or len(m["id"]) > 48:
        e.append("id phải dạng kebab-case (a-z, 0-9, gạch nối), tối đa 48 ký tự")
    if not isinstance(m.get("version"), str) or not VER_RE.match(m.get("version", "")):
        e.append("version phải dạng X.Y.Z")
    for k in ("name", "summary"):
        if not isinstance(m.get(k), str) or not m[k].strip():
            e.append(f"thiếu {k}")
    if m.get("category") not in CATEGORIES:
        e.append(f"category phải là một trong: {', '.join(CATEGORIES)}")
    if m.get("runtime") not in RUNTIMES:
        e.append(f"runtime phải là: {', '.join(sorted(RUNTIMES))}")
    if not isinstance(m.get("keywords"), list) or not m.get("keywords"):
        e.append("keywords phải là danh sách không rỗng (để Agent tìm theo chức năng)")
    itf = m.get("interface")
    if not isinstance(itf, dict) or not isinstance(itf.get("inputs"), list) or not isinstance(itf.get("outputs"), list):
        e.append("interface phải có inputs[] và outputs[]")
    perms = m.get("permissions")
    if not isinstance(perms, dict) or "network" not in perms or "subprocess" not in perms:
        e.append("permissions phải khai rõ network (true/false) và subprocess (danh sách lệnh, có thể rỗng)")
    elif not isinstance(perms.get("subprocess"), list):
        e.append("permissions.subprocess phải là danh sách")
    src = m.get("source")
    if not isinstance(src, dict) or not src.get("kind"):
        e.append("source phải ghi nguồn (kind, job_id, mẫu/đặc tả, mã bên thứ ba nếu có)")
    else:
        for tp in src.get("third_party", []) or []:
            if not all(tp.get(k) for k in ("url", "commit", "license")):
                e.append("mỗi mục third_party phải có url, commit, license")
    exs = m.get("examples")
    if not isinstance(exs, list) or not exs:
        e.append("phải có ít nhất một ví dụ đầu vào/đầu ra (examples)")
    else:
        for ex in exs:
            if not isinstance(ex.get("inputs"), dict) or not isinstance(ex.get("expected"), dict) or not ex["expected"]:
                e.append("mỗi ví dụ phải có inputs{} và expected{tệp kết quả: tệp mong đợi}")
            elif tool_dir is not None:
                for p in list(ex["inputs"].values()) + list(ex["expected"].values()):
                    if not (Path(tool_dir) / p).is_file():
                        e.append(f"ví dụ tham chiếu tệp không có: {p}")
    cost = m.get("cost_usd_per_run", 0)
    if not isinstance(cost, (int, float)) or cost < 0:
        e.append("cost_usd_per_run phải là số ≥ 0")
    if tool_dir is not None and isinstance(m.get("entry", "main.py"), str):
        if not (Path(tool_dir) / m.get("entry", "main.py")).is_file():
            e.append(f"không thấy tệp chạy '{m.get('entry', 'main.py')}'")
    return e


def _files(d: Path) -> dict[str, str]:
    return {p.relative_to(d).as_posix(): sha256_file(p) for p in sorted(d.rglob("*"))
            if p.is_file() and p.name != "RELEASE.json" and "__pycache__" not in p.parts}


class Catalog:
    def __init__(self, root: Path | None = None, directory: Directory | None = None,
                 clock: Callable[[], datetime] = utc_now):
        self.root = Path(root) if root else tool_root()
        self.dir = directory or Directory(self.root)
        self.clock = clock
        self.base = self.root / "catalog"
        self.base.mkdir(parents=True, exist_ok=True)

    # ---------- đọc ----------
    def tool(self, tool_id: str) -> dict:
        p = self.base / tool_id / "tool.json"
        if not ID_RE.match(tool_id or "") or not p.is_file():
            raise ToolError(f"Kho không có tool '{tool_id}'")
        return read_json(p)

    def tools(self) -> list[dict]:
        return [read_json(p) for p in sorted(self.base.glob("*/tool.json"))]

    def version_dir(self, tool_id: str, version: str) -> Path:
        return self.base / tool_id / version

    def manifest(self, tool_id: str, version: str) -> dict:
        p = self.version_dir(tool_id, version) / "manifest.json"
        if not p.is_file():
            raise ToolError(f"Kho không có {tool_id}@{version}")
        return read_json(p)

    def resolve(self, ref: str) -> tuple[str, str]:
        """'id' → phiên bản hiện hành; 'id@X.Y.Z' → đúng phiên bản đó nếu còn được phép gọi."""
        tool_id, _, ver = ref.partition("@")
        t = self.tool(tool_id)
        if not ver:
            ver = t.get("current_version")
            if not ver:
                raise ToolError(f"{tool_id} hiện không có phiên bản nào được phép gọi (đã thu hồi)")
        info = next((v for v in t["versions"] if v["version"] == ver), None)
        if info is None:
            raise ToolError(f"Kho không có {tool_id}@{ver}")
        if info["status"] != "GOOD":
            raise ToolError(f"{tool_id}@{ver} đã bị thu hồi ({info.get('note', '')}) — không gọi được")
        return tool_id, ver

    def verify_integrity(self, tool_id: str, version: str) -> None:
        d = self.version_dir(tool_id, version)
        rel = read_json(d / "RELEASE.json")
        now = _files(d)
        if now != rel["files"]:
            changed = sorted(set(now.items()) ^ set(rel["files"].items()))
            raise ToolError(f"{tool_id}@{version} đã bị sửa sau khi phát hành "
                            f"({', '.join(sorted({c[0] for c in changed}))}) — từ chối chạy")

    def find(self, query: str = "", category: str | None = None) -> list[dict]:
        """Tìm theo chức năng (không phân biệt hoa thường, dấu tiếng Việt). Chỉ trả tool đang gọi được."""
        if category and category not in CATEGORIES:
            raise ToolError(f"Nhóm '{category}' không có (các nhóm: {', '.join(CATEGORIES)})")
        words = [w for w in re.split(r"[^0-9a-z]+", fold(query)) if w]
        out = []
        for t in self.tools():
            ver = t.get("current_version")
            if not ver or (category and t["category"] != category):
                continue
            m = self.manifest(t["id"], ver)
            name, kws = fold(m["name"]), [fold(k) for k in m["keywords"]]
            hay = " ".join([fold(m["id"]), name, fold(m["summary"])] + kws)
            score = 0
            for w in words:
                if w in kws or w in fold(m["id"]).split("-"):
                    score += 3
                elif w in name:
                    score += 2
                elif w in hay:
                    score += 1
            if words and score == 0:
                continue
            out.append({"id": t["id"], "version": ver, "name": m["name"], "category": t["category"],
                        "category_name": CATEGORIES[t["category"]], "summary": m["summary"], "score": score,
                        "inputs": m["interface"]["inputs"], "outputs": m["interface"]["outputs"]})
        out.sort(key=lambda x: (-x["score"], x["id"]))
        return out

    # ---------- phát hành ----------
    def publish(self, build_dir: Path, actor: str, *, job: dict | None = None, session: dict | None = None,
                auto: bool = False) -> dict:
        """Phát hành một bản dựng vào kho chung. Tự chạy lại toàn bộ ca thử, không tin báo cáo có sẵn."""
        self.dir.require_executive(actor, "phát hành tool")
        build_dir = Path(build_dir)
        mpath = build_dir / "manifest.json"
        if not mpath.is_file():
            raise ToolError(f"Không thấy manifest.json trong {build_dir}")
        m = read_json(mpath)
        errs = validate_manifest(m, build_dir)
        if errs:
            raise ToolError("Manifest không hợp lệ:\n- " + "\n- ".join(errs))
        tool_id, version = m["id"], m["version"]
        Budget(self.root).require(float(m.get("cost_usd_per_run", 0)), f"tool {tool_id} tính phí mỗi lượt", actor)
        if job is not None:
            steps = {s["id"]: s for s in job["steps"]}
            if steps["test"]["status"] != "DONE":
                raise ToolError("Chưa có bước kiểm thử độc lập đạt — không phát hành")
            if steps["test"]["sub"] == steps["build"]["sub"]:
                raise ToolError("Người kiểm thử trùng người viết mã — không phát hành")
        if auto:
            if session is None:
                raise ToolError("Phát hành tự động phải thuộc một phiên AT")
            cap = int(self.dir.limits.get("max_auto_releases_per_session", 1))
            if len(session.get("releases", [])) >= cap:
                raise ToolError(f"Phiên {session['run_id']} đã phát hành tự động {cap} bản — hết hạn mức của phiên")
        test = sandbox.run_cases(build_dir, m)
        if not test["ok"]:
            bad = test["lint"] + [f"ca '{c['name']}': {c['detail']}" for c in test["cases"] if not c["ok"]]
            raise ToolError("Không đạt kiểm thử trong vùng cách ly — không phát hành:\n- " + "\n- ".join(bad))

        tdir = self.base / tool_id
        tdir.mkdir(parents=True, exist_ok=True)
        claim = tdir / f".claim-{version}"
        try:  # giành quyền phát hành phiên bản này: nguyên tử, đúng một bên thắng
            fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, f"{actor} {iso(self.clock())}\n".encode())
            os.close(fd)
        except FileExistsError:
            who = claim.read_text(encoding="utf-8").strip() if claim.exists() else "?"
            raise ToolError(f"{tool_id}@{version} đã được phát hành hoặc đang được phát hành bởi: {who} "
                            f"— không phát hành trùng. Dùng số phiên bản mới.") from None
        vdir = self.version_dir(tool_id, version)
        try:
            if vdir.exists():
                raise ToolError(f"{tool_id}@{version} đã có trong kho")
            stage = tdir / f".stage-{version}"
            if stage.exists():
                shutil.rmtree(stage)
            shutil.copytree(build_dir, stage, symlinks=False,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "RELEASE.json", ".git"))
            release = {
                "tool_id": tool_id, "version": version, "published_by": actor, "auto": auto,
                "published_at": iso(self.clock()), "job_id": job["job_id"] if job else None,
                "session": session["run_id"] if session else None,
                "test": {"ok": True, "cases": [{"name": c["name"], "ok": c["ok"]} for c in test["cases"]],
                         "isolation": test["isolation"]},
                "builder": next((s["sub"] for s in (job or {}).get("steps", []) if s["id"] == "build"), None),
                "tester": next((s["sub"] for s in (job or {}).get("steps", []) if s["id"] == "test"), None),
                "files": _files(stage),
            }
            atomic_write_json(stage / "RELEASE.json", release)
            os.rename(stage, vdir)
        except Exception:
            claim.unlink(missing_ok=True)  # chưa phát hành được: nhả quyền để thử lại
            raise
        with mutex(tdir / "tool.json"):
            t = read_json(tdir / "tool.json", default={"id": tool_id, "versions": [], "history": [],
                                                       "current_version": None})
            prev = t.get("current_version")
            t.update(name=m["name"], category=m["category"], summary=m["summary"], keywords=m["keywords"])
            t["versions"].append({"version": version, "status": "GOOD", "published_by": actor,
                                  "published_at": release["published_at"], "auto": auto, "note": ""})
            if prev is None or ver_key(version) > ver_key(prev):
                t["current_version"] = version
            t["history"].append({"at": release["published_at"], "actor": actor, "action": "publish",
                                 "version": version, "from": prev, "to": t["current_version"],
                                 "note": f"việc {release['job_id']}" if release["job_id"] else ""})
            atomic_write_json(tdir / "tool.json", t)
        return release

    # ---------- khôi phục ----------
    def rollback(self, tool_id: str, actor: str, reason: str, to_version: str | None = None) -> dict:
        """Thu hồi phiên bản hiện hành và quay về bản tốt trước đó (hoặc bản chỉ định). Không xóa tệp."""
        self.dir.require_executive(actor, "khôi phục phiên bản tool")
        if not reason or not reason.strip():
            raise ToolError("Khôi phục phải ghi lý do")
        tdir = self.base / tool_id
        with mutex(tdir / "tool.json"):
            t = self.tool(tool_id)
            cur = t.get("current_version")
            if cur is None:
                raise ToolError(f"{tool_id} không có phiên bản hiện hành để thu hồi")
            good = sorted((v["version"] for v in t["versions"] if v["status"] == "GOOD" and v["version"] != cur),
                          key=ver_key)
            if to_version is not None:
                if to_version not in good:
                    raise ToolError(f"{tool_id}@{to_version} không phải bản tốt để quay về (bản tốt: {', '.join(good) or 'không có'})")
                target = to_version
            else:
                older = [v for v in good if ver_key(v) < ver_key(cur)]
                target = older[-1] if older else None
            for v in t["versions"]:
                if v["version"] == cur:
                    v.update(status="WITHDRAWN", note=reason.strip(), withdrawn_at=iso(self.clock()), withdrawn_by=actor)
            t["current_version"] = target
            t["history"].append({"at": iso(self.clock()), "actor": actor, "action": "rollback", "version": cur,
                                 "from": cur, "to": target, "note": reason.strip()})
            atomic_write_json(tdir / "tool.json", t)
            return {"tool_id": tool_id, "withdrawn": cur, "current_version": target}
