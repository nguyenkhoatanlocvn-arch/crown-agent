"""Bản đồ mã (Graphify) cho CROWN INFINITY.

Nguyên tắc (skill graphify-eval + kiểm tra 2026-11-06):
  * Graphify là CÔNG CỤ NGOÀI, tùy chọn (gói PyPI `graphifyy`, đã kiểm 0.9.103). Lõi INFINITY không phụ thuộc nó.
  * Chỉ chạy chế độ code (`extract --code-only`), GRAPHIFY_BACKEND=none, không biến API key, tắt nhật ký truy vấn.
    Trên Linux có `unshare -n` thì cắt mạng hẳn.
  * Đầu vào là `git archive <commit> <phạm vi>`: chỉ tệp ĐÃ commit → không có dữ liệu chạy, backup, .env.
  * Mỗi bản đồ là snapshot bất biến gắn commit nguồn + thời điểm tạo + băm từng tệp; mở nguồn bằng `git show`.
  * Mỗi cạnh giữ nhãn EXTRACTED (đọc trực tiếp từ mã) hoặc INFERRED (Graphify suy luận).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from .auth import Actor, audit, require_owner
from .db import tx
from .httpcore import ApiError, bad_request
from .util import fold, new_id, now_iso

GRAPHIFY_TESTED_VERSION = "0.9.103"
SENSITIVE = re.compile(r"(^|/)(\.env[^/]*|.*\.key|.*\.pem|auth[^/]*\.json|[^/]*secret[^/]*|.*\.sqlite|.*\.db|backups?)(/|$)",
                       re.I)
SCOPE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,120}$")
MAX_ITEMS = 300_000


def run_git(repo: Path, *args: str, timeout: int = 30) -> tuple[bool, bytes]:
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return False, b""
    return p.returncode == 0, p.stdout


def find_graphify(explicit: str | None = None) -> str | None:
    cand = explicit or os.environ.get("CROWN_INFINITY_GRAPHIFY") or shutil.which("graphify")
    if cand and Path(cand).exists():
        return str(cand)
    return shutil.which(cand) if cand else None


def _safe_rel(path: str) -> str:
    """Đường dẫn tương đối, không tuyệt đối, không '..'. Rỗng nếu không hợp lệ."""
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
        return ""
    p = PurePosixPath(path)
    if p.is_absolute() or any(part in ("..", "") for part in p.parts):
        return ""
    return str(p)


def norm_name(s: str) -> str:
    """'._create_backup()' / 'create_backup()' → so khớp theo tên gọn, không dấu, không phân biệt hoa thường."""
    return fold(re.sub(r"\(\)$", "", str(s).strip()).lstrip("."))


def _line(loc) -> int | None:
    m = re.match(r"^L(\d+)", str(loc or ""))
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- dựng bản đồ

def _extract_archive(repo: Path, commit: str, scope: str, dest: Path) -> list[dict]:
    ok, data = run_git(repo, "archive", "--format=tar", commit, scope, timeout=120)
    if not ok or not data:
        raise ApiError(409, "GIT_ARCHIVE_FAILED", f"Không lấy được mã '{scope}' ở commit {commit[:12]} (git archive)")
    files = []
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        for m in tf.getmembers():
            rel = _safe_rel(m.name.rstrip("/"))
            if not rel or not (rel == scope or rel.startswith(scope.rstrip("/") + "/")):
                continue
            if m.isdir():
                continue
            if not m.isfile():
                continue  # bỏ symlink, thiết bị…
            sub = rel[len(scope.rstrip("/")) + 1:]
            if SENSITIVE.search(sub) or "graphify-out" in sub.split("/"):
                continue
            blob = tf.extractfile(m).read()
            out = dest / scope / sub
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(blob)
            files.append({"path": sub, "sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob)})
    if not files:
        raise ApiError(409, "EMPTY_SCOPE", f"Phạm vi '{scope}' không có tệp nào ở commit {commit[:12]}")
    return sorted(files, key=lambda f: f["path"])


def _sandbox_env(home: Path) -> dict:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "USERPROFILE": str(home), "LANG": "C.UTF-8",
           "PYTHONIOENCODING": "utf-8", "GRAPHIFY_BACKEND": "none", "GRAPHIFY_QUERY_LOG_DISABLE": "1"}
    for k in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC"):  # Windows cần để chạy tiến trình con
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env  # cố ý KHÔNG chép biến *_API_KEY, AWS_*, OLLAMA_*… → Graphify không có backend LLM nào


def build_map(conn, actor: Actor, repo_root: Path, project_id: str, scope: str = "crown_infinity",
              graphify_bin: str | None = None, commit: str = "HEAD") -> dict:
    require_owner(actor)
    if not SCOPE_RE.match(scope) or ".." in scope:
        raise bad_request("Phạm vi không hợp lệ")
    exe = find_graphify(graphify_bin)
    if not exe:
        raise ApiError(409, "GRAPHIFY_NOT_INSTALLED",
                       "Chưa có Graphify trên máy chủ. Cài gói 'graphifyy' (hai chữ y) phiên bản 0.9.103 rồi đặt "
                       "CROWN_INFINITY_GRAPHIFY=<đường dẫn graphify>; xem docs/infinity/OPS_BRAIN.md")
    ok, out = run_git(repo_root, "rev-parse", "--verify", f"{commit}^{{commit}}")
    if not ok:
        raise ApiError(409, "GIT_UNAVAILABLE", "Không đọc được commit (máy chủ chưa có git hoặc không phải repo)")
    sha = out.decode().strip()
    with tempfile.TemporaryDirectory(prefix="ci-graph-") as tmp:
        tmp = Path(tmp)
        files = _extract_archive(repo_root, sha, scope, tmp / "src")
        cmd = [exe, "extract", str(tmp / "src" / scope), "--code-only"]
        isolated = False
        if sys.platform.startswith("linux") and shutil.which("unshare"):
            if subprocess.run(["unshare", "-n", "true"], capture_output=True).returncode == 0:
                cmd, isolated = ["unshare", "-n", *cmd], True
        (tmp / "home").mkdir()
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=900, cwd=tmp, env=_sandbox_env(tmp / "home"))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ApiError(502, "GRAPHIFY_FAILED", f"Không chạy được Graphify: {type(exc).__name__}") from exc
        log = (p.stdout + p.stderr).decode("utf-8", "replace")
        gpath = tmp / "src" / scope / "graphify-out" / "graph.json"
        if p.returncode != 0 or not gpath.is_file():
            raise ApiError(502, "GRAPHIFY_FAILED", "Graphify chạy lỗi", {"exit": p.returncode, "log_tail": log[-1500:]})
        ver = subprocess.run([exe, "--version"], capture_output=True, env=_sandbox_env(tmp / "home"), timeout=60)
        tool_version = ver.stdout.decode().strip().split()[-1] if ver.returncode == 0 and ver.stdout.strip() else "?"
        notes = (f"graphify extract --code-only; GRAPHIFY_BACKEND=none; không có biến API key; "
                 f"{'đã cắt mạng (unshare -n)' if isolated else 'KHÔNG cắt được mạng trên hệ điều hành này'}; "
                 f"{len(files)} tệp từ git archive")
        warn = [ln.strip() for ln in log.splitlines() if "warning" in ln.lower() or "skipped" in ln.lower()]
        if warn:
            notes += " · cảnh báo: " + " | ".join(warn)[:600]
        return import_graph(conn, actor, project_id, gpath.read_bytes(), sha, scope, tool_version, files, notes)


def import_graph(conn, actor: Actor, project_id: str, raw: bytes, source_commit: str, scope: str,
                 tool_version: str, files: list[dict], notes: str = "") -> dict:
    require_owner(actor)
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit or ""):
        raise bad_request("source_commit phải là SHA-1 đầy đủ 40 ký tự")
    try:
        g = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise bad_request("graph.json hỏng") from exc
    nodes, links = g.get("nodes"), g.get("links") if "links" in g else g.get("edges")
    if not isinstance(nodes, list) or not isinstance(links, list) or len(nodes) + len(links) > MAX_ITEMS:
        raise bad_request("graph.json không đúng định dạng Graphify (nodes/links)")
    gv = (g.get("graph") or {}).get("graphify_version") or tool_version
    file_set = {f["path"] for f in files}
    mid, ts = new_id("GM"), now_iso()
    with tx(conn):
        if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            raise ApiError(404, "NOT_FOUND", "Không tìm thấy dự án")
        ext = inf = 0
        node_rows, edge_rows = [], []
        for n in nodes:
            if not isinstance(n, dict) or not isinstance(n.get("id"), str):
                continue
            label = str(n.get("label") or n["id"])[:300]
            ntype = str(n.get("type") or ("callable" if n.get("_callable") else n.get("file_type") or ""))[:40]
            node_rows.append((mid, n["id"][:300], label, norm_name(label), ntype, _safe_rel(n.get("source_file") or ""),
                              _line(n.get("source_location"))))
        for e in links:
            if not isinstance(e, dict) or not isinstance(e.get("source"), str) or not isinstance(e.get("target"), str):
                continue
            conf = str(e.get("confidence") or "").upper()
            if conf not in ("EXTRACTED", "INFERRED"):
                conf = "AMBIGUOUS"
            ext += conf == "EXTRACTED"
            inf += conf == "INFERRED"
            score = e.get("confidence_score")
            edge_rows.append((mid, e["source"][:300], e["target"][:300], str(e.get("relation") or "related")[:60], conf,
                              float(score) if isinstance(score, (int, float)) else None,
                              _safe_rel(e.get("source_file") or ""), _line(e.get("source_location"))))
        conn.execute("INSERT INTO graph_maps(id,project_id,tool,tool_version,source_commit,scope_prefix,files_json,"
                     "node_count,edge_count,extracted_count,inferred_count,graph_sha256,notes,created_by,created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (mid, project_id, "graphify", str(gv), source_commit, scope.rstrip("/"),
                      json.dumps(files, ensure_ascii=False), len(node_rows), len(edge_rows), ext, inf,
                      hashlib.sha256(raw).hexdigest(), notes[:2000], actor.id, ts))
        conn.executemany("INSERT OR IGNORE INTO graph_nodes(map_id,node_id,label,norm_label,node_type,source_file,"
                         "source_line) VALUES (?,?,?,?,?,?,?)", node_rows)
        conn.executemany("INSERT INTO graph_edges(map_id,source,target,relation,confidence,confidence_score,"
                         "source_file,source_line) VALUES (?,?,?,?,?,?,?,?)", edge_rows)
        audit(conn, actor, "graph.import", "graph_map", mid,
              detail={"project_id": project_id, "commit": source_commit, "nodes": len(node_rows),
                      "edges": len(edge_rows), "files": len(file_set)})
    return map_info(conn, mid)


# ---------------------------------------------------------------- đọc (người gọi tự kiểm quyền dự án)

def map_row(conn, map_id: str):
    r = conn.execute("SELECT * FROM graph_maps WHERE id=?", (map_id,)).fetchone()
    if not r:
        raise ApiError(404, "NOT_FOUND", "Không tìm thấy bản đồ")
    return r


def map_info(conn, map_id: str) -> dict:
    r = map_row(conn, map_id)
    d = {k: r[k] for k in r.keys() if k != "files_json"}
    d["file_count"] = len(json.loads(r["files_json"]))
    return d


def latest_map(conn, project_id: str) -> dict | None:
    r = conn.execute("SELECT id FROM graph_maps WHERE project_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                     (project_id,)).fetchone()
    return map_info(conn, r["id"]) if r else None


def list_maps(conn, project_id: str) -> list[dict]:
    return [map_info(conn, r["id"]) for r in conn.execute(
        "SELECT id FROM graph_maps WHERE project_id=? ORDER BY created_at DESC, id DESC LIMIT 20", (project_id,))]


def _node(conn, map_id, node_id):
    return conn.execute("SELECT * FROM graph_nodes WHERE map_id=? AND node_id=?", (map_id, node_id)).fetchone()


def query(conn, map_id: str, name: str, relation: str | None = None, direction: str = "both", limit: int = 60) -> dict:
    """Tìm nút theo id/tên rồi trả các quan hệ kèm nhãn EXTRACTED/INFERRED và vị trí nguồn."""
    m = map_row(conn, map_id)
    if not isinstance(name, str) or not name.strip():
        raise bad_request("Cần tên nút (node)")
    name = name.strip()
    if direction not in ("out", "in", "both"):
        raise bad_request("direction phải là out, in hoặc both")
    limit = max(1, min(200, int(limit)))
    hit = _node(conn, map_id, name)
    cands = [] if hit else conn.execute(
        "SELECT * FROM graph_nodes WHERE map_id=? AND norm_label=? ORDER BY node_id LIMIT 20",
        (map_id, norm_name(name))).fetchall()
    if not hit and len(cands) != 1:
        like = cands or conn.execute("SELECT * FROM graph_nodes WHERE map_id=? AND norm_label LIKE ? ORDER BY "
                                     "length(label), node_id LIMIT 20", (map_id, f"%{norm_name(name)}%")).fetchall()
        return {"map_id": map_id, "source_commit": m["source_commit"], "node": None,
                "candidates": [_node_view(r) for r in like], "relations": [],
                "note": "Tên khớp nhiều nút hoặc không khớp chính xác; chọn một node_id" if like else "Không có nút nào"}
    node = hit or cands[0]
    rels = []
    rel_cond, rel_args = ("AND relation=?", [relation]) if relation else ("", [])
    if direction in ("out", "both"):
        for e in conn.execute(f"SELECT * FROM graph_edges WHERE map_id=? AND source=? {rel_cond} ORDER BY confidence, "
                              f"relation, target LIMIT ?", (map_id, node["node_id"], *rel_args, limit)):
            rels.append(_edge_view(conn, map_id, e, "out", e["target"]))
    if direction in ("in", "both"):
        for e in conn.execute(f"SELECT * FROM graph_edges WHERE map_id=? AND target=? {rel_cond} ORDER BY confidence, "
                              f"relation, source LIMIT ?", (map_id, node["node_id"], *rel_args, limit)):
            rels.append(_edge_view(conn, map_id, e, "in", e["source"]))
    return {"map_id": map_id, "source_commit": m["source_commit"], "scope_prefix": m["scope_prefix"],
            "node": _node_view(node), "candidates": [], "relations": rels,
            "legend": {"EXTRACTED": "đọc trực tiếp từ mã nguồn", "INFERRED": "Graphify suy luận khi phân giải",
                       "AMBIGUOUS": "không rõ nguồn gốc"}}


def _node_view(r) -> dict:
    return {"node_id": r["node_id"], "label": r["label"], "type": r["node_type"], "source_file": r["source_file"],
            "source_line": r["source_line"]}


def _edge_view(conn, map_id, e, direction, other_id) -> dict:
    o = _node(conn, map_id, other_id)
    return {"direction": direction, "relation": e["relation"], "confidence": e["confidence"],
            "confidence_score": e["confidence_score"],
            "other": _node_view(o) if o else {"node_id": other_id, "label": other_id, "type": "", "source_file": "",
                                               "source_line": None},
            "evidence": {"source_file": e["source_file"], "source_line": e["source_line"]}}


def read_source(conn, repo_root: Path, map_id: str, path: str, line: int | None = None, context: int = 12) -> dict:
    """Mở đúng tệp ở đúng commit của bản đồ để đối chiếu. Chỉ tệp thuộc bản đồ."""
    m = map_row(conn, map_id)
    rel = _safe_rel(path)
    files = {f["path"]: f for f in json.loads(m["files_json"])}
    if not rel or rel not in files:
        raise ApiError(404, "NOT_IN_MAP", "Tệp không thuộc bản đồ này")
    full = f"{m['scope_prefix']}/{rel}"
    ok, blob = run_git(repo_root, "show", f"{m['source_commit']}:{full}")
    origin = f"git show {m['source_commit'][:12]}:{full}"
    if not ok:
        wt = Path(repo_root) / full
        blob = wt.read_bytes() if wt.is_file() else b""
        if hashlib.sha256(blob).hexdigest() != files[rel]["sha256"]:
            raise ApiError(409, "SOURCE_UNAVAILABLE", "Không đọc được tệp ở commit của bản đồ và bản trên đĩa đã khác")
        origin = "thư mục làm việc (băm khớp bản đồ)"
    if hashlib.sha256(blob).hexdigest() != files[rel]["sha256"]:
        raise ApiError(409, "SOURCE_MISMATCH", "Nội dung tệp không khớp băm ghi trong bản đồ")
    text = blob.decode("utf-8", "replace").split("\n")
    try:
        line = int(line) if line not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise bad_request("line phải là số") from exc
    if line is None:
        start, end = 1, min(len(text), 80)
    else:
        start, end = max(1, line - context), min(len(text), line + context)
    return {"map_id": map_id, "source_commit": m["source_commit"], "path": rel, "repo_path": full, "origin": origin,
            "line": line, "start": start, "end": end, "total_lines": len(text),
            "lines": [{"n": i, "text": text[i - 1]} for i in range(start, end + 1)]}
