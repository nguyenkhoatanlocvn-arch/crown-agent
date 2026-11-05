"""Nhận mẫu (tệp, thư mục, ZIP, URL, prompt) và lấy khung hình video bằng FFmpeg cục bộ."""
import shutil
import subprocess
import unittest
import zipfile

from tool.core import video
from tool.core.inbox import Inbox
from tool.core.util import ToolError, read_json

from .tool_fixtures import make_root


class InboxTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.tmp = self.root.parent.parent
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ib = Inbox(self.root)

    def test_prompt_url_file_folder_zip(self):
        src = self.tmp / "mau"
        (src / "sub").mkdir(parents=True)
        (src / "a.srt").write_text("1\n", encoding="utf-8")
        (src / "sub" / "b.png").write_bytes(b"\x89PNG")
        (src / ".env").write_text("KEY=1", encoding="utf-8")
        z = self.tmp / "goi.zip"
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("doc/readme.md", "xin chào")
            f.writestr("../../thoat.txt", "zip-slip")
            f.writestr("auth_token.json", "{}")
        self.ib.add("TJ-1", "Viết tool đánh lại số phụ đề từ 1", private=False)
        self.ib.add("TJ-1", "https://example.com/huong-dan")
        self.ib.add("TJ-1", src / "a.srt", private=False)
        self.ib.add("TJ-1", src)                     # mặc định riêng tư
        man = self.ib.add("TJ-1", z)
        kinds = sorted(i["kind"] for i in man["items"])
        self.assertEqual(kinds, ["file", "file", "file", "image", "prompt", "url"])
        self.assertTrue((self.root / "inbox/TJ-1/files/a.srt").is_file())               # mẫu công khai → vào git
        self.assertTrue((self.root / "inbox/TJ-1/private/mau/sub/b.png").is_file())     # mẫu riêng → ngoài git
        self.assertTrue((self.root / "inbox/TJ-1/private/goi/doc/readme.md").is_file())
        skipped = sorted(s["name"] for s in man["skipped"])
        self.assertEqual(skipped, sorted([".env", "../../thoat.txt", "auth_token.json"]))       # bí mật + zip-slip bị bỏ
        self.assertFalse((self.tmp / "thoat.txt").exists())
        self.assertFalse(any(i.get("fetched") for i in man["items"] if i["kind"] == "url"))
        self.assertTrue(all(len(i["sha256"]) == 64 for i in man["items"]))
        self.assertEqual(read_json(self.root / "inbox/TJ-1/manifest.json")["job_id"], "TJ-1")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "máy không có FFmpeg")
class FramesTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.tmp = self.root.parent.parent
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.video = self.tmp / "v.mp4"
        # 6 giây: 2 giây đỏ, 2 giây lục, 2 giây lam — hai lần "màn hình đổi" tại giây 2 và 4
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
        for color in ("red", "green", "blue"):
            cmd += ["-f", "lavfi", "-i", f"color=c={color}:s=320x240:r=10:d=2"]
        cmd += ["-filter_complex", "[0][1][2]concat=n=3:v=1:a=0", "-pix_fmt", "yuv420p", "-y", str(self.video)]
        subprocess.run(cmd, check=True)
        self.job = self.root / "workbench" / "TJ-v"
        self.job.mkdir(parents=True)

    def test_frames_at_requested_timestamps(self):
        meta = video.extract_frames(self.video, self.job, at=[0.5, 2.5, 4.5])
        self.assertEqual([f["t"] for f in meta["frames"]], [0.5, 2.5, 4.5])
        self.assertEqual((meta["method"], meta["width"], meta["height"], meta["duration"]), ("at", 320, 240, 6.0))
        for f in meta["frames"]:
            self.assertTrue((self.job / f["file"]).is_file())
        px = [subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(self.job / f["file"]), "-vf", "scale=1:1",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
              for f in meta["frames"]]
        self.assertEqual([max(range(3), key=lambda i: p[i]) for p in px], [0, 1, 2])   # đỏ, lục, lam đúng mốc
        self.assertTrue((self.job / "samples" / "contact_sheet.jpg").is_file())
        self.assertEqual(read_json(self.job / "frames.json")["video_sha256"], meta["video_sha256"])

    def test_scene_detection_captures_each_screen_change(self):
        v2 = self.tmp / "bw.mp4"       # đen → trắng → xám: hai lần đổi màn hình ở giây 2 và 4
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
        for color in ("black", "white", "gray"):
            cmd += ["-f", "lavfi", "-i", f"color=c={color}:s=320x240:r=10:d=2"]
        cmd += ["-filter_complex", "[0][1][2]concat=n=3:v=1:a=0", "-pix_fmt", "yuv420p", "-y", str(v2)]
        subprocess.run(cmd, check=True)
        meta = video.extract_frames(v2, self.job)
        self.assertEqual(meta["method"], "scene+grid")
        self.assertEqual([round(c) for c in meta["scene_changes"]], [2, 4])
        ts = [f["t"] for f in meta["frames"]]
        self.assertEqual(ts[0], 0.0)
        self.assertIn(2.12, ts)                                                          # ngay sau lần đổi thứ nhất
        self.assertIn(4.12, ts)                                                          # ngay sau lần đổi thứ hai
        self.assertGreater(ts[-1], 5.5)                                                  # khung cuối: kết quả sau cùng
        self.assertEqual(ts, sorted(ts))

    def test_every_n_seconds_and_cap(self):
        self.assertEqual(video.plan_times(10.0, every=2.0), [0.0, 2.0, 4.0, 6.0, 8.0, 9.95])
        capped = video.plan_times(100.0, every=0.5, changes=[33.3], max_frames=12)
        self.assertEqual((len(capped), capped[0], capped[-1]), (12, 0.0, 99.95))
        self.assertIn(33.42, capped)                                                     # mốc đổi màn hình không bị bỏ
        self.assertEqual(video.extract_frames(self.video, self.job, every=2.0, scene=False)["method"], "grid")

    def test_not_a_video(self):
        bad = self.tmp / "x.mp4"
        bad.write_text("không phải video")
        with self.assertRaises(ToolError):
            video.extract_frames(bad, self.job)


if __name__ == "__main__":
    unittest.main()
