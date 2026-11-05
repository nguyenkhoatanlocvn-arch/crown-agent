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
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import sandbox
from .access import secret_reason
from .budget import Budget
from .identity import Directory
from .util import (ToolError, atomic_write_json, fold, iso, mutex, read_json, sha256_file, tool_root, tree_digest, utc_now)

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
CLAIM_STALE_S = 600  # dấu giành quyền phát hành cũ hơn mức này mà chưa thành bản phát hành = tiến trình đã chết giữa chừng


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
    elif any(not isinstance(i, dict) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", str(i.get("name", ""))) for i in itf["inputs"]):
        e.append("tên mỗi đầu vào phải dạng chữ thường/số/gạch dưới (vd. input)")
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
            else:
                for name in ex["expected"]:
                    if not isinstance(name, str) or name.startswith("/") or ".." in name.split("/") or "\\" in name:
                        e.append(f"tên tệp kết quả của ví dụ không hợp lệ: {name!r}")
                if tool_dir is not None:
                    for p in list(ex["inputs"].values()) + list(ex["expected"].values()):
                        sp = sandbox.safe_rel(Path(tool_dir), p)
                        if sp is None:
                            e.append(f"ví dụ tham chiếu đường dẫn ngoài thư mục tool: {p!r}")
                        elif not sp.is_file():
                            e.append(f"ví dụ tham chiếu tệp không có: {p}")
    cost = m.get("cost_usd_per_run", 0)
    if not isinstance(cost, (int, float)) or cost < 0:
        e.append("cost_usd_per_run phải là số ≥ 0")
    entry = m.get("entry", "main.py")
    if not isinstance(entry, str) or not entry.endswith(".py"):
        e.append("entry phải là tệp .py trong thư mục tool")
    elif tool_dir is not None:
        sp = sandbox.safe_rel(Path(tool_dir), entry)
        if sp is None or not sp.is_file():
            e.append(f"không thấy tệp chạy '{entry}' trong thư mục tool")
        links = sandbox.symlinks_in(Path(tool_dir))
        if links:
            e.append("thư mục tool không được có liên kết tượng trưng: " + ", ".join(links[:5]))
    if tool_dir is not None:  # kho là vùng dùng chung: tệp giống bí mật không được theo bản dựng vào kho
        for f in sorted(x for x in Path(tool_dir).rglob("*") if x.is_file() and "__pycache__" not in x.parts):
            why = secret_reason(f.relative_to(tool_dir))
            if why:
                e.append(f"bản dựng có tệp giống bí mật: {f.relative_to(tool_dir).as_posix()} ({why})")
    lim = m.get("limits", {})
    if not isinstance(lim, dict):
        e.append("limits phải là object")
    else:
        for k, cap in sandbox.LIMIT_CAPS.items():
            v = lim.get(k)
            if v is not None and (isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= cap):
                e.append(f"limits.{k} phải là số nguyên từ 1 đến {cap}")
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
        # RELEASE.json tự nó cũng được chốt băm trong hồ sơ tool: sửa mã rồi sửa luôn bảng băm vẫn bị phát hiện.
        info = next((v for v in self.tool(tool_id)["versions"] if v["version"] == version), {})
        if info.get("release_sha256") and info["release_sha256"] != sha256_file(d / "RELEASE.json"):
            raise ToolError(f"{tool_id}@{version}: RELEASE.json đã bị sửa sau khi phát hành — từ chối chạy")
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
    def _gate(self, build_dir: Path, actor: str, job_id: str | None, token: int | None) -> dict:
        """Cổng việc: phát hành luôn thuộc một việc ĐANG CHẠY do chính người phát hành giữ quyền ghi.

        Không có đường phát hành ngoài sổ việc: không việc → không có kiểm thử độc lập → không phát hành.
        """
        from .jobs import JobStore
        if not job_id or token is None:
            raise ToolError("Phát hành phải kèm mã việc và fencing token (--job, --token): tool chỉ vào kho qua sổ việc")
        jobs = JobStore(self.root, self.dir, self.clock)
        job = jobs.get(job_id)
        jobs._held(job, actor, token)  # việc đang RUNNING, đúng người giữ, đúng token, lease còn hạn
        steps = {s["id"]: s for s in job["steps"]}
        if steps["test"]["status"] != "DONE":
            raise ToolError("Chưa có bước kiểm thử độc lập đạt — không phát hành")
        if not steps["test"]["sub"] or not steps["build"]["sub"] or steps["test"]["sub"] == steps["build"]["sub"]:
            raise ToolError("Người kiểm thử trùng người viết mã (hoặc thiếu tên) — không phát hành")
        if steps["publish"]["status"] != "RUNNING" or steps["publish"]["by"] != actor:
            raise ToolError(f"Bước publish của {job_id} chưa được {actor} bắt đầu (job start {job_id} publish ...)")
        if Path(build_dir).resolve() != (jobs.job_dir(job_id) / "build").resolve():
            raise ToolError(f"Chỉ phát hành bản dựng của chính việc này: tool/workbench/{job_id}/build/")
        if steps["test"].get("build_digest") != tree_digest(Path(build_dir)):
            raise ToolError("Bản dựng đã thay đổi sau khi kiểm thử độc lập — phải kiểm thử lại trước khi phát hành")
        return job

    def _stale_claim(self, tdir: Path, version: str, claim: Path) -> bool:
        """Dấu giành quyền bị bỏ lại bởi một lần phát hành chết giữa chừng? Nếu đúng: dọn để thử lại được."""
        with mutex(tdir / "tool.json"):
            t = read_json(tdir / "tool.json", default={"versions": []})
            if any(v["version"] == version for v in t["versions"]):
                return False  # đã phát hành thật
            try:
                age = time.time() - claim.stat().st_mtime
            except OSError:
                return True
            if age < CLAIM_STALE_S:
                return False  # có thể bên kia đang phát hành ngay lúc này
            shutil.rmtree(tdir / f".stage-{version}", ignore_errors=True)
            shutil.rmtree(tdir / version, ignore_errors=True)  # thư mục phiên bản chưa kịp vào hồ sơ tool
            claim.unlink(missing_ok=True)
            return True

    def publish(self, build_dir: Path, actor: str, *, job_id: str | None = None, token: int | None = None,
                session_id: str | None = None, auto: bool = False) -> dict:
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
        cost = float(m.get("cost_usd_per_run", 0))
        Budget(self.root).require(cost, f"tool {tool_id} tính phí mỗi lượt", actor)
        job = self._gate(build_dir, actor, job_id, token)
        sched = None
        if auto or session_id:
            from .scheduler import Scheduler
            sched = Scheduler(self.root, self.dir, self.clock)
        if auto:
            if session_id is None:
                raise ToolError("Phát hành tự động phải thuộc một phiên AT")
            if cost > 0:
                raise ToolError("Tool tính phí không phát hành tự động — cần Chủ tịch có mặt")
            sched.reserve_release(session_id, actor, tool_id, version)  # đếm hạn mức phiên NGAY, dưới khóa phiên
        elif session_id:
            sched.get(session_id)
        try:
            return self._publish(build_dir, m, actor, job, session_id, auto)
        except BaseException:
            if auto:
                sched.cancel_release(session_id, tool_id, version)
            raise

    def _publish(self, build_dir: Path, m: dict, actor: str, job: dict, session_id: str | None, auto: bool) -> dict:
        tool_id, version = m["id"], m["version"]
        test = sandbox.run_cases(build_dir, m)
        if not test["ok"]:
            bad = test["lint"] + [f"ca '{c['name']}': {c['detail']}" for c in test["cases"] if not c["ok"]]
            raise ToolError("Không đạt kiểm thử trong vùng cách ly — không phát hành:\n- " + "\n- ".join(bad))

        tdir = self.base / tool_id
        tdir.mkdir(parents=True, exist_ok=True)
        claim = tdir / f".claim-{version}"
        for attempt in (1, 2):
            try:  # giành quyền phát hành phiên bản này: nguyên tử, đúng một bên thắng
                fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                os.write(fd, f"{actor} {iso(self.clock())}\n".encode())
                os.close(fd)
                break
            except FileExistsError:
                if attempt == 1 and self._stale_claim(tdir, version, claim):
                    continue
                who = claim.read_text(encoding="utf-8").strip() if claim.exists() else "?"
                raise ToolError(f"{tool_id}@{version} đã được phát hành hoặc đang được phát hành bởi: {who} "
                                f"— không phát hành trùng. Dùng số phiên bản mới.") from None
        vdir = self.version_dir(tool_id, version)
        steps = {s["id"]: s for s in job["steps"]}
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
                "published_at": iso(self.clock()), "job_id": job["job_id"], "session": session_id,
                "test": {"ok": True, "cases": [{"name": c["name"], "ok": c["ok"]} for c in test["cases"]],
                         "isolation": test["isolation"]},
                "builder": steps["build"]["sub"], "tester": steps["test"]["sub"],
                "build_digest": steps["test"].get("build_digest"),
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
                                  "published_at": release["published_at"], "auto": auto, "note": "",
                                  "release_sha256": sha256_file(vdir / "RELEASE.json")})
            if prev is None or ver_key(version) > ver_key(prev):
                t["current_version"] = version
            t["history"].append({"at": release["published_at"], "actor": actor, "action": "publish",
                                 "version": version, "from": prev, "to": t["current_version"],
                                 "note": f"việc {release['job_id']}"})
            atomic_write_json(tdir / "tool.json", t)
        return release

    # ---------- khôi phục ----------
    def rollback(self, tool_id: str, actor: str, reason: str, to_version: str | None = None,
                 version: str | None = None) -> dict:
        """Thu hồi một phiên bản (mặc định: bản hiện hành) và, nếu đó là bản hiện hành, quay về bản tốt. Không xóa tệp.

        version: bản cần thu hồi. Thu hồi một bản KHÔNG phải bản hiện hành thì bản hiện hành giữ nguyên.
        to_version: bản tốt muốn quay về (chỉ dùng khi thu hồi bản hiện hành).
        """
        self.dir.require_executive(actor, "khôi phục phiên bản tool")
        if not reason or not reason.strip():
            raise ToolError("Khôi phục phải ghi lý do")
        if not isinstance(tool_id, str) or not ID_RE.match(tool_id):
            raise ToolError(f"Kho không có tool '{str(tool_id)[:60]}'")
        for v in (version, to_version):
            if v is not None and not VER_RE.match(str(v)):
                raise ToolError(f"Phiên bản không hợp lệ: {v!r}")
        tdir = self.base / tool_id
        if not (tdir / "tool.json").is_file():
            raise ToolError(f"Kho không có tool '{tool_id}'")
        with mutex(tdir / "tool.json"):
            t = self.tool(tool_id)
            cur = t.get("current_version")
            bad = version or cur
            if bad is None:
                raise ToolError(f"{tool_id} không có phiên bản hiện hành để thu hồi")
            info = next((v for v in t["versions"] if v["version"] == bad), None)
            if info is None or info["status"] != "GOOD":
                raise ToolError(f"{tool_id}@{bad} không phải bản đang gọi được — không có gì để thu hồi")
            good = sorted((v["version"] for v in t["versions"] if v["status"] == "GOOD" and v["version"] != bad),
                          key=ver_key)
            if bad != cur:
                if to_version is not None:
                    raise ToolError("--to chỉ dùng khi thu hồi bản hiện hành")
                target = cur
            elif to_version is not None:
                if to_version not in good:
                    raise ToolError(f"{tool_id}@{to_version} không phải bản tốt để quay về (bản tốt: {', '.join(good) or 'không có'})")
                target = to_version
            else:  # bản tốt gần nhất phía dưới; không có thì bản tốt cao nhất còn lại
                older = [v for v in good if ver_key(v) < ver_key(bad)]
                target = older[-1] if older else (good[-1] if good else None)
            info.update(status="WITHDRAWN", note=reason.strip(), withdrawn_at=iso(self.clock()), withdrawn_by=actor)
            t["current_version"] = target
            t["history"].append({"at": iso(self.clock()), "actor": actor, "action": "rollback", "version": bad,
                                 "from": cur, "to": target, "note": reason.strip()})
            atomic_write_json(tdir / "tool.json", t)
            return {"tool_id": tool_id, "withdrawn": bad, "current_version": target}
