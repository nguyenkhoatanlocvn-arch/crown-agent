"""Vùng thử cách ly cho mã tool: soát quyền bằng AST rồi chạy trong tiến trình con bị giới hạn.

Hợp đồng chạy tool (runtime python):
    python <entry> request.json      (thư mục làm việc = vùng thử)
    request.json = {"inputs": {tên: "in/<tệp>"}, "out_dir": "out", "params": {...}}
    Tool ghi kết quả vào out/, in một dòng JSON báo cáo ở cuối stdout, thoát 0 khi thành công.

Cách ly: thư mục tạm riêng (bản sao mã + bản sao đầu vào), môi trường đã gỡ biến (không token, không proxy),
giới hạn CPU/bộ nhớ/cỡ tệp (POSIX), hết giờ thì diệt cả nhóm tiến trình, và tách mạng bằng `unshare -n`
khi hệ điều hành cho phép. Nơi không tách được mạng (vd. Windows) kết quả ghi rõ netns=false.
Đây là hàng rào cho mã do bộ phận tự viết và đã soát; KHÔNG phải nhà tù cho mã độc.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from functools import lru_cache
from pathlib import Path

from .util import ToolError

NETWORK_MODULES = {"socket", "ssl", "http", "urllib", "requests", "ftplib", "smtplib", "poplib", "imaplib", "nntplib",
                   "telnetlib", "xmlrpc", "websocket", "websockets", "aiohttp", "httpx", "asyncio", "socketserver",
                   "selectors", "webbrowser", "asyncore", "asynchat", "smtpd"}
PROCESS_MODULES = {"subprocess", "multiprocessing", "pty", "concurrent"}
NEVER_MODULES = {"ctypes", "importlib", "runpy", "code", "codeop", "pickle", "marshal", "shelve", "builtins", "gc", "inspect"}
PROCESS_OS_CALLS = {"system", "popen", "fork", "forkpty", "kill", "killpg", "startfile", "posix_spawn", "posix_spawnp"}
NEVER_BUILTINS = {"eval", "exec", "compile", "__import__", "breakpoint", "globals", "vars"}
LIMIT_CAPS = {"timeout_s": 600, "memory_mb": 4096, "max_output_mb": 500}  # manifest không tự nới quá các mức này


def _os_forbidden(attr: str) -> bool:
    return attr in PROCESS_OS_CALLS or attr.startswith(("exec", "spawn"))


def safe_rel(tool_dir: Path, rel: str) -> Path | None:
    """Đường dẫn tương đối nằm TRONG thư mục tool, hoặc None nếu thoát ra ngoài (tuyệt đối, '..', liên kết tượng trưng)."""
    if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or "\\" in rel or ":" in rel:
        return None
    parts = rel.split("/")
    if any(x in ("", ".", "..") for x in parts):
        return None
    base = Path(tool_dir).resolve()
    p = base.joinpath(*parts)
    cur = base
    for x in parts:  # không thành phần nào được là liên kết tượng trưng
        cur = cur / x
        if cur.is_symlink():
            return None
    try:
        p.resolve().relative_to(base)
    except ValueError:
        return None
    return p


def symlinks_in(tool_dir: Path) -> list[str]:
    return [p.relative_to(tool_dir).as_posix() for p in Path(tool_dir).rglob("*") if p.is_symlink()]


def lint_permissions(tool_dir: Path, manifest: dict) -> list[str]:
    """Soát mã nguồn so với quyền khai trong manifest. Trả danh sách vi phạm (rỗng = đạt).

    Soát theo TÊN ĐÃ PHÂN GIẢI chứ không theo chữ viết: `import os as o`, `from os import system`,
    `getattr(os, ...)`, `sys.modules`, `os.posix_spawn` đều bị bắt. Mô-đun nội bộ của CPython (tên bắt đầu
    bằng gạch dưới, vd. _socket, _posixsubprocess) bị cấm vì chúng là cửa sau của các mô-đun đã chặn.
    Đây vẫn là bộ soát cho mã do bộ phận tự viết, không phải bằng chứng mã vô hại.
    """
    tool_dir = Path(tool_dir)
    perms = manifest.get("permissions", {})
    allow_net = perms.get("network") is True
    allow_proc = bool(perms.get("subprocess"))
    local = {p.stem for p in tool_dir.rglob("*.py")} | {p.name for p in tool_dir.iterdir() if p.is_dir()}
    stdlib = set(sys.stdlib_module_names)
    out: list[str] = []
    for py in sorted(tool_dir.rglob("*.py")):
        relp = py.relative_to(tool_dir).as_posix()
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=relp)
        except (SyntaxError, UnicodeDecodeError, ValueError) as e:
            out.append(f"{relp}: không đọc được mã nguồn ({type(e).__name__})")
            continue
        os_names, sys_names = set(), set()  # mọi tên trong tệp đang trỏ tới mô-đun os / sys
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    top = a.name.split(".")[0]
                    if top in ("os", "posix", "nt"):
                        os_names.add(a.asname or top)
                    if top == "sys":
                        sys_names.add(a.asname or "sys")
        for node in ast.walk(tree):
            where = f"{relp}:{getattr(node, 'lineno', 0)}"
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [] if node.level else [(node.module or "").split(".")[0]]
                if not node.level and (node.module or "").split(".")[0] in ("os", "posix", "nt"):
                    for a in node.names:
                        if a.name == "*":
                            out.append(f"{where}: cấm 'from os import *' (không soát được tên nào được dùng)")
                        elif _os_forbidden(a.name) and not allow_proc:
                            out.append(f"{where}: nhập os.{a.name} nhưng manifest không khai permissions.subprocess")
                if not node.level and (node.module or "").split(".")[0] == "sys" and any(a.name == "modules" for a in node.names):
                    out.append(f"{where}: cấm dùng sys.modules")
            for m in mods:
                if m in NEVER_MODULES:
                    out.append(f"{where}: cấm dùng mô-đun '{m}'")
                elif m.startswith("_") and m != "__future__" and m not in local:
                    out.append(f"{where}: cấm dùng mô-đun nội bộ '{m}'")
                elif m in NETWORK_MODULES and not allow_net:
                    out.append(f"{where}: dùng mạng ('{m}') nhưng manifest không khai permissions.network")
                elif m in PROCESS_MODULES and not allow_proc:
                    out.append(f"{where}: gọi tiến trình ('{m}') nhưng manifest không khai permissions.subprocess")
                elif m not in stdlib and m not in local:
                    out.append(f"{where}: mô-đun ngoài thư viện chuẩn '{m}' (chưa được thẩm định)")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                f = node.func
                if f.id in NEVER_BUILTINS:
                    out.append(f"{where}: cấm gọi {f.id}()")
                if f.id in ("getattr", "setattr", "delattr") and node.args and isinstance(node.args[0], ast.Name) \
                        and node.args[0].id in os_names | sys_names:
                    out.append(f"{where}: cấm {f.id}() trên mô-đun {node.args[0].id} (che tên hàm được gọi)")
            # Dùng thuộc tính của os dưới bất kỳ tên nào, dù có gọi ngay hay gán sang biến khác.
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id in os_names and _os_forbidden(node.attr) and not allow_proc:
                    out.append(f"{where}: dùng os.{node.attr} nhưng manifest không khai permissions.subprocess")
                if node.value.id in sys_names and node.attr == "modules":
                    out.append(f"{where}: cấm dùng sys.modules")
            if isinstance(node, ast.Name) and node.id == "__builtins__":
                out.append(f"{where}: cấm dùng __builtins__")
    return out


@lru_cache(maxsize=1)
def netns_available() -> bool:
    if os.name != "posix" or not shutil.which("unshare"):
        return False
    try:
        return subprocess.run(["unshare", "-n", "true"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


def _limits(timeout_s: int, max_output_mb: int, memory_mb: int):
    def apply():  # chạy trong tiến trình con trước khi exec
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (timeout_s + 5, timeout_s + 5))
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_output_mb << 20, max_output_mb << 20))
        resource.setrlimit(resource.RLIMIT_AS, (memory_mb << 20, memory_mb << 20))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    return apply


def _bin_dir(work: Path, commands: list) -> Path:
    """Thư mục PATH của lượt chạy: chỉ chứa đúng các lệnh ngoài mà manifest đã khai (permissions.subprocess)."""
    b = work / "bin"
    b.mkdir(exist_ok=True)
    for c in commands or []:
        if isinstance(c, str) and c and "/" not in c and "\\" not in c and not c.startswith("."):
            real = shutil.which(c)
            if real and not (b / c).exists():
                try:
                    os.symlink(real, b / c)
                except OSError:
                    pass
    return b


def clean_env(work: Path, commands: list | None = None) -> dict:
    path = str(_bin_dir(work, commands or [])) if os.name == "posix" else os.environ.get("PATH", os.defpath)
    env = {"PATH": path, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
           "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
           "HOME": str(work), "TMPDIR": str(work / "tmp"), "TEMP": str(work / "tmp"), "TMP": str(work / "tmp"),
           "TOOL_SANDBOX": "1"}
    for k in ("SYSTEMROOT", "COMSPEC", "PATHEXT", "WINDIR"):  # Windows cần các biến này để chạy python
        if k in os.environ:
            env[k] = os.environ[k]
    return env


def execute(tool_dir: Path, manifest: dict, inputs: dict[str, Path], params: dict | None = None,
            timeout_s: int | None = None) -> dict:
    """Chạy một lượt trong vùng thử. Người gọi phải dọn `result['work']` bằng cleanup()."""
    tool_dir = Path(tool_dir)
    lim = manifest.get("limits", {})
    timeout_s = max(1, min(int(timeout_s or lim.get("timeout_s", 30)), LIMIT_CAPS["timeout_s"]))
    max_out = max(1, min(int(lim.get("max_output_mb", 50)), LIMIT_CAPS["max_output_mb"]))
    mem = max(64, min(int(lim.get("memory_mb", 2048)), LIMIT_CAPS["memory_mb"]))
    entry = manifest.get("entry", "main.py")
    if safe_rel(tool_dir, entry) is None or not safe_rel(tool_dir, entry).is_file():
        raise ToolError(f"Tệp chạy '{entry}' không nằm trong thư mục tool")
    if symlinks_in(tool_dir):
        raise ToolError("Thư mục tool có liên kết tượng trưng — từ chối chạy")
    work = Path(tempfile.mkdtemp(prefix="toolrun-"))
    code = work / "tool"
    shutil.copytree(tool_dir, code, symlinks=False, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
    for d in ("in", "out", "tmp"):
        (work / d).mkdir()
    req_inputs = {}
    for name, src in inputs.items():
        src = Path(src)
        if not src.is_file():
            cleanup(work)
            raise ToolError(f"Đầu vào '{name}' không phải tệp: {src}")
        dst = work / "in" / src.name
        if dst.exists():
            dst = work / "in" / f"{name}__{src.name}"
        shutil.copyfile(src, dst)
        req_inputs[name] = dst.relative_to(work).as_posix()
    (work / "request.json").write_text(json.dumps(
        {"inputs": req_inputs, "out_dir": "out", "params": params or {}}, ensure_ascii=False), encoding="utf-8")
    cmd = [sys.executable, "-E", "-s", "-B", f"tool/{entry}", "request.json"]
    use_netns = manifest.get("permissions", {}).get("network") is not True and netns_available()
    if use_netns:
        cmd = [shutil.which("unshare") or "unshare", "-n", "--"] + cmd
    posix = os.name == "posix"
    t0 = time.monotonic()
    timed_out = False
    proc = subprocess.Popen(cmd, cwd=work, env=clean_env(work, manifest.get("permissions", {}).get("subprocess")),
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            preexec_fn=_limits(timeout_s, max_out, mem) if posix else None,
                            start_new_session=posix)
    try:
        so, se = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            if posix:
                os.killpg(proc.pid, 9)
            else:
                proc.kill()
        except OSError:
            pass
        so, se = proc.communicate()
    dur = time.monotonic() - t0
    stdout, stderr = so.decode("utf-8", "replace"), se.decode("utf-8", "replace")
    report = None
    for line in reversed([x for x in stdout.splitlines() if x.strip()]):
        try:
            v = json.loads(line)
            if isinstance(v, dict):
                report = v
        except ValueError:
            pass
        break
    outputs, total = {}, 0
    for p in sorted((work / "out").rglob("*")):
        if p.is_symlink():
            cleanup(work)
            raise ToolError(f"Tool tạo liên kết tượng trưng trong out/ ({p.name}) — từ chối")
        if p.is_file():
            outputs[p.relative_to(work / "out").as_posix()] = p
            total += p.stat().st_size
    if total > max_out << 20:
        cleanup(work)
        raise ToolError(f"Kết quả {total >> 20} MB vượt hạn mức {max_out} MB")
    return {"exit_code": proc.returncode, "timed_out": timed_out, "duration_s": round(dur, 3),
            "stdout_tail": stdout[-2000:], "stderr_tail": stderr[-2000:], "report": report,
            "outputs": outputs, "work": work,
            "isolation": {"tempdir": True, "env_stripped": True, "rlimits": posix, "netns": use_netns,
                          "timeout_s": timeout_s}}


def cleanup(work: Path) -> None:
    shutil.rmtree(work, ignore_errors=True)


def run_cases(tool_dir: Path, manifest: dict) -> dict:
    """Chạy toàn bộ ca thử của tool (tests/cases.json + ví dụ trong manifest). Trả báo cáo, không ném lỗi ca thử."""
    tool_dir = Path(tool_dir)
    report = {"ok": False, "lint": lint_permissions(tool_dir, manifest), "cases": [], "isolation": None}
    cases = []
    cpath = tool_dir / "tests" / "cases.json"
    if cpath.is_file():
        cases += json.loads(cpath.read_text(encoding="utf-8")).get("cases", [])
    for ex in manifest.get("examples", []):
        cases.append({"name": f"vi-du:{ex.get('name', 'mau')}", "inputs": ex["inputs"], "params": ex.get("params", {}),
                      "expect": {"exit_code": 0, "files": ex.get("expected", {})}})
    if not cases:
        report["lint"].append("Không có ca thử nào (tests/cases.json hoặc examples trong manifest)")
    for c in cases:
        res = {"name": c.get("name", "?"), "ok": False, "detail": "", "duration_s": None}
        refs = list(c.get("inputs", {}).values()) + list((c.get("expect", {}).get("files") or {}).values())
        outside = [v for v in refs if safe_rel(tool_dir, v) is None]
        if outside:
            res["detail"] = "đường dẫn ca thử thoát ra ngoài thư mục tool: " + ", ".join(map(str, outside))
            report["cases"].append(res)
            continue
        try:
            r = execute(tool_dir, manifest, {k: tool_dir / v for k, v in c.get("inputs", {}).items()}, c.get("params"))
        except ToolError as e:
            res["detail"] = str(e)
            report["cases"].append(res)
            continue
        try:
            report["isolation"] = r["isolation"]
            res["duration_s"] = r["duration_s"]
            exp = c.get("expect", {})
            problems = []
            if r["timed_out"]:
                problems.append(f"quá thời gian {r['isolation']['timeout_s']} giây")
            want_code = exp.get("exit_code", 0)
            if r["exit_code"] != want_code:
                problems.append(f"mã thoát {r['exit_code']} ≠ {want_code}; stderr: {r['stderr_tail'][-300:].strip()}")
            for out_name, exp_path in (exp.get("files") or {}).items():
                got = r["outputs"].get(out_name)
                if got is None:
                    problems.append(f"thiếu tệp kết quả '{out_name}' (có: {', '.join(r['outputs']) or 'không có'})")
                    continue
                a, b = got.read_bytes(), (tool_dir / exp_path).read_bytes()
                if c.get("compare") == "text":
                    a, b = a.replace(b"\r\n", b"\n"), b.replace(b"\r\n", b"\n")
                if a != b:
                    problems.append(f"'{out_name}' khác mong đợi ({len(a)} byte so với {len(b)} byte)")
            for k, v in (exp.get("report") or {}).items():
                if (r["report"] or {}).get(k) != v:
                    problems.append(f"báo cáo {k}={(r['report'] or {}).get(k)!r} ≠ {v!r}")
            if exp.get("stderr_contains") and exp["stderr_contains"] not in r["stderr_tail"]:
                problems.append(f"stderr thiếu '{exp['stderr_contains']}'")
            if exp.get("no_output") and r["outputs"]:
                problems.append(f"không được tạo tệp kết quả, nhưng có: {', '.join(r['outputs'])}")
            res["ok"], res["detail"] = not problems, "; ".join(problems)
        finally:
            cleanup(r["work"])
        report["cases"].append(res)
    report["ok"] = not report["lint"] and bool(cases) and all(c["ok"] for c in report["cases"])
    return report
