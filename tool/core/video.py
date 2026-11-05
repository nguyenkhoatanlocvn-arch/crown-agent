"""Lấy khung hình từ video mẫu bằng FFmpeg cục bộ (gọi lệnh đã cài, không sửa, không nhúng mã FFmpeg).

Kết quả: workbench/<mã việc>/frames/f_###_t####.##s.png (không vào git) + frames.json (mốc thời gian, băm)
+ samples/contact_sheet.jpg (một ảnh ghép nhỏ làm bằng chứng). Việc đọc khung hình để ghi "người dùng bấm gì,
màn hình đổi ra sao" do Agent nhánh Phân tích mẫu làm; tệp này chỉ lo phần cắt hình đúng mốc thời gian.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path

from .util import ToolError, atomic_write_json, iso, sha256_file


def _need(binary: str) -> str:
    p = shutil.which(binary)
    if not p:
        raise ToolError(f"Máy này chưa có {binary}. Cài FFmpeg (ffmpeg.org) rồi chạy lại; TOOL không tự cài.")
    return p


def probe(video: Path) -> dict:
    out = subprocess.run([_need("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,avg_frame_rate,nb_frames:format=duration", "-of", "json", str(video)],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise ToolError(f"ffprobe không đọc được video: {out.stderr.strip()[-300:]}")
    d = json.loads(out.stdout)
    if not d.get("streams"):
        raise ToolError("Tệp không có luồng hình")
    s = d["streams"][0]
    num, _, den = (s.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    return {"width": s["width"], "height": s["height"], "fps": round(fps, 3),
            "duration": round(float(d["format"]["duration"]), 3)}


def scene_changes(video: Path, threshold: float = 0.004) -> list[float]:
    """Mốc thời gian màn hình thay đổi (theo bộ lọc scene của FFmpeg)."""
    out = subprocess.run([_need("ffmpeg"), "-hide_banner", "-i", str(video), "-vf",
                          f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"],
                         capture_output=True, text=True, timeout=300)
    return [float(x) for x in re.findall(r"pts_time:([0-9.]+)", out.stderr)]


def plan_times(duration: float, every: float | None = None, at: list[float] | None = None,
               changes: list[float] | None = None, max_frames: int = 40, min_gap: float = 0.25,
               tail: float = 0.05) -> list[float]:
    """Chọn mốc thời gian. Mốc chỉ định (at) thì lấy đúng các mốc đó. Ngược lại: lưới cách đều
    + ngay sau mỗi lần màn hình đổi + khung đầu + khung cuối. Khi hai mốc sát nhau, ưu tiên: đầu/cuối,
    rồi mốc đổi màn hình, rồi mốc lưới. Vượt max_frames thì bỏ bớt mốc lưới trước."""
    last = max(0.0, round(duration - tail, 2))  # mốc cuối phải còn nằm trong khung hình cuối cùng
    if at:
        return sorted({round(t, 2) for t in at if 0 <= t <= duration})
    step = every or max(0.5, duration / 10)
    cand = [(0.0, 0), (last, 0)]
    cand += [(round(min(last, c + 0.12), 2), 1) for c in (changes or [])]
    cand += [(round(i * step, 2), 2) for i in range(1, int(math.floor(last / step)) + 1)]
    kept: list[tuple[float, int]] = []
    for t, pr in sorted(cand, key=lambda x: (x[1], x[0])):
        if all(abs(t - k) >= min_gap for k, _ in kept):
            kept.append((t, pr))
    while len(kept) > max_frames:  # bỏ mốc ưu tiên thấp nhất, gần mốc khác nhất
        kept.sort()
        worst = max(pr for _, pr in kept)
        if worst == 0:
            break
        idx = [i for i, (_, pr) in enumerate(kept) if pr == worst]
        gap = lambda i: min(kept[i][0] - kept[i - 1][0] if i else 1e9, kept[i + 1][0] - kept[i][0] if i + 1 < len(kept) else 1e9)
        kept.pop(min(idx, key=gap))
    return sorted(t for t, _ in kept)


def extract_frames(video: Path, job_dir: Path, *, every: float | None = None, at: list[float] | None = None,
                   scene: bool = True, max_frames: int = 40, width: int = 1280) -> dict:
    video, job_dir = Path(video), Path(job_dir)
    if not video.is_file():
        raise ToolError(f"Không thấy video: {video}")
    info = probe(video)
    changes = scene_changes(video) if (scene and not at) else None
    tail = max(0.05, 1.5 / info["fps"]) if info["fps"] else 0.2
    times = plan_times(info["duration"], every, at, changes, max_frames, tail=tail)
    fdir = job_dir / "frames"
    if fdir.exists():
        shutil.rmtree(fdir)
    fdir.mkdir(parents=True)
    frames = []
    ff = _need("ffmpeg")
    for i, t in enumerate(times):
        name = f"f_{i:03d}_t{t:07.2f}s.png"
        r = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(video),
                            "-frames:v", "1", "-vf", f"scale='min({width},iw)':-2", "-y", str(fdir / name)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not (fdir / name).is_file():
            continue  # mốc sát cuối video có thể không còn khung: bỏ qua mốc đó
        frames.append({"index": len(frames), "t": t, "file": f"frames/{name}", "sha256": sha256_file(fdir / name)})
    if not frames:
        raise ToolError("FFmpeg không lấy được khung hình nào")
    sdir = job_dir / "samples"
    sdir.mkdir(exist_ok=True)
    cols = min(4, len(frames))
    rows = math.ceil(len(frames) / cols)
    sheet = sdir / "contact_sheet.jpg"
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-pattern_type", "glob", "-framerate", "1",
                    "-i", str(fdir / "f_*.png"), "-vf", f"scale=400:-2,tile={cols}x{rows}:padding=4:color=white",
                    "-frames:v", "1", "-q:v", "5", "-y", str(sheet)], capture_output=True, text=True, timeout=180)
    meta = {"video": video.name, "video_sha256": sha256_file(video), **info,
            "method": "at" if at else ("scene+grid" if changes else "grid"),
            "scene_changes": changes, "frames": frames,
            "contact_sheet": "samples/contact_sheet.jpg" if sheet.is_file() else None, "extracted_at": iso()}
    atomic_write_json(job_dir / "frames.json", meta)
    return meta
