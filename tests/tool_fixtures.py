"""Đồ dùng chung cho test TOOL: gốc TOOL tạm + một tool mẫu nhỏ (viết hoa văn bản)."""
import json
import shutil
import tempfile
from pathlib import Path

MAIN_PY = '''import json, sys
from pathlib import Path

req = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
src = Path(req["inputs"]["input"])
text = src.read_text(encoding="utf-8")
if not text.strip():
    print("tep rong", file=sys.stderr)
    sys.exit(2)
out = Path(req["out_dir"]) / src.name
out.write_text(text.upper()__SUFFIX__, encoding="utf-8")
print(json.dumps({"ok": True, "chars": len(text)}))
'''


def make_root() -> Path:
    """Gốc TOOL tạm đặt trong một 'repo' tạm: <tmp>/repo/tool."""
    tmp = Path(tempfile.mkdtemp(prefix="tooltest-"))
    root = tmp / "repo" / "tool"
    for d in ("inbox", "workbench", "catalog", "skills", "agents/sub", "runs/sessions", "runs/locks", "config"):
        (root / d).mkdir(parents=True)
    return root


def make_build(base: Path, tool_id: str = "upper-text", version: str = "1.0.0", *, broken: bool = False,
               extra_import: str = "", permissions: dict | None = None, cost: float = 0, dest: Path | None = None) -> Path:
    """Bản dựng tool hợp lệ. broken=True: kết quả sai so với ví dụ (để thử cổng kiểm thử/quay lui)."""
    b = Path(dest) if dest else Path(base) / f"build-{tool_id}-{version}"
    if b.exists():
        shutil.rmtree(b)
    (b / "examples").mkdir(parents=True)
    (b / "tests" / "data").mkdir(parents=True)
    code = MAIN_PY.replace("__SUFFIX__", ' + "!"' if broken else "")
    (b / "main.py").write_text((extra_import + "\n" if extra_import else "") + code, encoding="utf-8")
    (b / "examples" / "mau.txt").write_text("xin chào\n", encoding="utf-8")
    (b / "examples" / "mau.expected.txt").write_text("XIN CHÀO\n", encoding="utf-8")
    (b / "tests" / "data" / "rong.txt").write_text("", encoding="utf-8")
    (b / "tests" / "cases.json").write_text(json.dumps({"cases": [
        {"name": "tep-rong", "inputs": {"input": "tests/data/rong.txt"},
         "expect": {"exit_code": 2, "stderr_contains": "tep rong", "no_output": True}}]}), encoding="utf-8")
    manifest = {
        "id": tool_id, "version": version, "name": "Viết hoa văn bản", "category": "data-files",
        "summary": "Đổi toàn bộ chữ trong tệp văn bản thành chữ hoa", "keywords": ["viết hoa", "văn bản", "txt"],
        "runtime": "python", "entry": "main.py",
        "interface": {"inputs": [{"name": "input", "type": "file", "ext": [".txt"], "required": True}],
                      "outputs": [{"name": "output", "type": "file"}]},
        "permissions": permissions or {"network": False, "subprocess": []},
        "examples": [{"name": "mau", "inputs": {"input": "examples/mau.txt"},
                      "expected": {"mau.txt": "examples/mau.expected.txt"}}],
        "source": {"kind": "test-fixture"}, "limits": {"timeout_s": 20}, "cost_usd_per_run": cost,
    }
    (b / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return b


T, AT = "agent-tool", "agent-tool-at"


def job_at_publish(root: Path, actor: str = T, *, clock=None, mutate=None, **build_kw):
    """Đưa một việc đi ĐÚNG đường thật tới bước publish: Agent con viết ≠ Agent con kiểm, bản dựng đã chốt băm.

    Trả (mã việc, fencing token, thư mục bản dựng). mutate(build_dir): sửa bản dựng trước khi "kiểm thử".
    """
    from tool.core.identity import Directory
    from tool.core.jobs import JobStore
    from tool.core.subagents import SubAgents
    from tool.core.util import utc_now
    clock = clock or utc_now
    d = Directory(root)
    jobs, subs = JobStore(root, d, clock), SubAgents(root, d, clock)
    jid = jobs.create(title="Tool thử", prompt="viết tool thử", requested_by="CHAIRMAN", assigned_to=actor)["job_id"]
    tok = jobs.claim(jid, actor)
    for st in ("intake", "research", "analyze", "spec"):
        jobs.step_start(jid, st, actor, tok)
        jobs.step_done(jid, st, actor, tok, ["bằng chứng thử"])
    lt = subs.spawn(branch="lt", assigned_by=actor, job_id=jid, step="build", output="build/")["id"]
    jobs.step_start(jid, "build", actor, tok, sub=lt)
    build = make_build(root, dest=jobs.job_dir(jid) / "build", **build_kw)
    if mutate:
        mutate(build)
    jobs.step_done(jid, "build", actor, tok, ["build/manifest.json"])
    subs.finish(lt, actor, True, "viết xong")
    kt = subs.spawn(branch="kt", assigned_by=actor, job_id=jid, step="test", output="TEST_REPORT.json")["id"]
    jobs.step_start(jid, "test", actor, tok, sub=kt)
    jobs.step_done(jid, "test", actor, tok, ["TEST_REPORT.json"])
    subs.finish(kt, actor, True, "kiểm xong")
    jobs.step_start(jid, "publish", actor, tok)
    return jid, tok, build


def publish_via_job(root: Path, actor: str = T, *, clock=None, session_id: str | None = None, auto: bool = False,
                    mutate=None, **build_kw) -> dict:
    """Phát hành qua sổ việc (đường duy nhất vào kho). Trả bản ghi phát hành."""
    from tool.core.catalog import Catalog
    from tool.core.identity import Directory
    from tool.core.util import utc_now
    jid, tok, build = job_at_publish(root, actor, clock=clock, mutate=mutate, **build_kw)
    return Catalog(root, Directory(root), clock or utc_now).publish(build, actor, job_id=jid, token=tok,
                                                                    session_id=session_id, auto=auto)
