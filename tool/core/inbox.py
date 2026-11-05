"""Hộp nhận mẫu: video, ảnh, URL, prompt, tệp, ZIP, thư mục → inbox/<mã việc>/.

Mỗi mục được băm (sha256) và ghi vào inbox/<mã việc>/manifest.json.
- private=True (mặc định cho mẫu của Chủ tịch): tệp nằm trong inbox/<mã việc>/private/, KHÔNG vào git;
  manifest chỉ giữ tên, cỡ, băm và con trỏ (vd. mã asset trên web).
- private=False: tệp nằm trong inbox/<mã việc>/files/ và được commit (chỉ dùng cho mẫu tự tạo, không riêng tư).
Tệp giống bí mật bị bỏ qua và ghi tên vào danh sách skipped. URL chỉ được ghi lại, không tự tải.
"""
from __future__ import annotations

import fnmatch
import shutil
import zipfile
from pathlib import Path

from .access import DEFAULT_POLICY
from .util import ToolError, atomic_write_json, iso, mutex, read_json, sha256_file, sha256_text, tool_root

KINDS = {
    "video": {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".gif"},
    "image": {".png", ".jpg", ".jpeg", ".webp", ".bmp"},
    "archive": {".zip"},
}
MAX_FILES = 5000
MAX_TOTAL_MB = 500


def kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    for k, exts in KINDS.items():
        if ext in exts:
            return k
    return "file"


def _secretish(name: str) -> bool:
    return any(fnmatch.fnmatch(name.lower(), g.lower()) for g in DEFAULT_POLICY["deny_globs"])


class Inbox:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else tool_root()
        self.base = self.root / "inbox"
        self.base.mkdir(parents=True, exist_ok=True)

    def manifest(self, job_id: str) -> dict:
        return read_json(self.base / job_id / "manifest.json",
                         default={"job_id": job_id, "items": [], "skipped": [], "updated_at": None})

    def _store(self, job_id: str, private: bool) -> Path:
        d = self.base / job_id / ("private" if private else "files")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _add_file(self, src: Path, dst: Path, man: dict, private: bool, origin: str, pointer: str | None) -> None:
        if _secretish(src.name):
            man["skipped"].append({"name": src.name, "why": "tên tệp giống bí mật"})
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        man["items"].append({"kind": kind_of(src), "name": src.name, "path": dst.relative_to(self.root).as_posix(),
                             "bytes": dst.stat().st_size, "sha256": sha256_file(dst), "private": private,
                             "origin": origin, "pointer": pointer, "at": iso()})

    def add(self, job_id: str, item: str | Path, *, private: bool = True, pointer: str | None = None) -> dict:
        """Thêm một mục: đường dẫn tệp/thư mục/ZIP, hoặc chuỗi URL, hoặc văn bản prompt."""
        with mutex(self.base / job_id / "manifest.json"):
            man = self.manifest(job_id)
            text = str(item)
            p = Path(text).expanduser() if len(text) < 1024 and "\n" not in text else None
            if text.startswith(("http://", "https://")):
                man["items"].append({"kind": "url", "url": text, "sha256": sha256_text(text), "private": private,
                                     "fetched": False, "at": iso()})
            elif p is not None and p.is_file() and kind_of(p) == "archive":
                self._add_zip(p, job_id, man, private)
            elif p is not None and p.is_file():
                self._add_file(p, self._store(job_id, private) / p.name, man, private, "file", pointer)
            elif p is not None and p.is_dir():
                self._add_dir(p, job_id, man, private)
            else:
                if not text.strip():
                    raise ToolError("Mục gửi vào rỗng")
                d = self.base / job_id
                d.mkdir(parents=True, exist_ok=True)
                n = sum(1 for i in man["items"] if i["kind"] == "prompt") + 1
                f = d / f"prompt_{n:02d}.txt"
                f.write_text(text, encoding="utf-8")
                man["items"].append({"kind": "prompt", "path": f.relative_to(self.root).as_posix(),
                                     "bytes": f.stat().st_size, "sha256": sha256_file(f), "private": False, "at": iso()})
            man["updated_at"] = iso()
            atomic_write_json(self.base / job_id / "manifest.json", man)
            return man

    def _add_dir(self, src: Path, job_id: str, man: dict, private: bool) -> None:
        files = [f for f in sorted(src.rglob("*")) if f.is_file() and not f.is_symlink()]
        total = sum(f.stat().st_size for f in files)
        if len(files) > MAX_FILES or total > MAX_TOTAL_MB << 20:
            raise ToolError(f"Thư mục quá lớn ({len(files)} tệp, {total >> 20} MB; hạn mức {MAX_FILES} tệp, {MAX_TOTAL_MB} MB)")
        base = self._store(job_id, private) / src.name
        for f in files:
            self._add_file(f, base / f.relative_to(src), man, private, f"folder:{src.name}", None)

    def _add_zip(self, src: Path, job_id: str, man: dict, private: bool) -> None:
        base = self._store(job_id, private) / src.stem
        with zipfile.ZipFile(src) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            total = sum(i.file_size for i in infos)
            if len(infos) > MAX_FILES or total > MAX_TOTAL_MB << 20:
                raise ToolError(f"ZIP quá lớn sau giải nén ({len(infos)} tệp, {total >> 20} MB)")
            for i in infos:
                target = (base / i.filename).resolve()
                if base.resolve() not in target.parents:  # chống zip-slip
                    man["skipped"].append({"name": i.filename, "why": "đường dẫn trong ZIP thoát ra ngoài"})
                    continue
                if _secretish(Path(i.filename).name):
                    man["skipped"].append({"name": i.filename, "why": "tên tệp giống bí mật"})
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(i) as fsrc, open(target, "wb") as fdst:
                    shutil.copyfileobj(fsrc, fdst)
                man["items"].append({"kind": kind_of(target), "name": i.filename,
                                     "path": target.relative_to(self.root).as_posix(), "bytes": target.stat().st_size,
                                     "sha256": sha256_file(target), "private": private, "origin": f"zip:{src.name}",
                                     "pointer": None, "at": iso()})
