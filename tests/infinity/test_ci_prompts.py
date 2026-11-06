"""Nghiệm thu Prompt Studio — tiêu chí 1–6 và 12 (phần API) của CROWN INFINITY MASTER DIRECTIVE V2.0."""
from __future__ import annotations

import sqlite3
import unicodedata

from tests.infinity.helpers import EDITOR_PW, OWNER_PW, InfinityTestCase, ServerHarness, b64, seed_users  # noqa: F401

VI_TITLE = "Phân tích đơn hàng Etsy — tháng 11 🎯"
VI_TEXT = "# Mục tiêu\nPhân tích đơn hàng cho {{san_pham}}.\nRàng buộc: trả lời tiếng Việt có dấu: ắ ẳ ẵ ặ ố ồ ổ ỗ ộ đ Đ.\n"


def H(rev):
    return {"If-Match": f'"{rev}"'}


class T01Persistence(InfinityTestCase):
    def test_vietnamese_prompt_survives_server_restart(self):
        d = self.new_prompt(title=VI_TITLE, content=VI_TEXT, tags=["phân tích", "Etsy"])
        pid = d["id"]
        self.h.stop()
        h2 = ServerHarness(self.h.data_dir)  # tiến trình máy chủ mới trên cùng thư mục dữ liệu
        try:
            c = h2.client()
            self.assertEqual(c.login("loc", OWNER_PW)[0], 200)  # phiên cũ vẫn hợp lệ nhưng đăng nhập lại cho chắc
            st, got, _ = c.get(f"/api/v1/prompts/{pid}")
            self.assertEqual(st, 200)
            self.assertEqual(got["title"], VI_TITLE)
            self.assertEqual(got["draft_content"], VI_TEXT)
            self.assertEqual(sorted(got["tags"]), ["Etsy", "phân tích"])
            st, v, _ = c.get(f"/api/v1/prompts/{pid}/versions/{got['head_version']['id']}")
            self.assertEqual(v["content"], VI_TEXT)
        finally:
            h2.stop()
            self.h = ServerHarness(self.h.data_dir)  # để tearDown dừng được


class T02Versions(InfinityTestCase):
    def test_edit_creates_new_version_previous_and_approved_unchanged(self):
        d = self.new_prompt(content="Bản một")
        pid, v1 = d["id"], d["head_version"]["id"]
        st, v1_before, _ = self.owner.get(f"/api/v1/prompts/{pid}/versions/{v1}")
        self.assertEqual(self.owner.post(f"/api/v1/prompts/{pid}/versions/{v1}/review", {"status": "APPROVED"})[0], 200)
        st, d, _ = self.owner.put(f"/api/v1/prompts/{pid}/production", {"version_id": v1, "expected_version_id": None})
        self.assertEqual(st, 200, d)
        # autosave nhiều lần: không tạo phiên bản, không đụng bản APPROVED/production
        rev = d["revision"]
        for i in range(5):
            st, d, _ = self.owner.put(f"/api/v1/prompts/{pid}/draft", {"content": f"Bản hai lần sửa {i}"}, headers=H(rev))
            self.assertEqual(st, 200, d)
            rev = d["revision"]
        self.assertEqual(d["version_count"], 1)
        self.assertEqual(d["production_version"]["id"], v1)
        st, d, _ = self.owner.post(f"/api/v1/prompts/{pid}/versions", {"changelog": "sửa"}, headers=H(rev))
        self.assertEqual(st, 201, d)
        self.assertEqual(d["head_version"]["version_no"], 2)
        self.assertEqual(d["production_version"]["id"], v1, "production không tự đổi theo phiên bản mới")
        st, v1_after, _ = self.owner.get(f"/api/v1/prompts/{pid}/versions/{v1}")
        self.assertEqual(v1_after["content"], "Bản một")
        self.assertEqual(v1_after["checksum"], v1_before["checksum"])
        self.assertEqual(v1_after["review_status"], "APPROVED")

    def test_versions_immutable_at_database_level(self):
        d = self.new_prompt(content="Không được sửa")
        conn = self.h.db()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE prompt_versions SET content='hack' WHERE id=?", (d["head_version"]["id"],))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM prompt_versions WHERE id=?", (d["head_version"]["id"],))
            with self.assertRaises(sqlite3.IntegrityError):  # production chỉ trỏ tới bản APPROVED
                conn.execute("UPDATE prompts SET production_version_id=? WHERE id=?", (d["head_version"]["id"], d["id"]))
        finally:
            conn.close()

    def test_no_empty_or_unchanged_version(self):
        d = self.new_prompt(content="Giữ nguyên")
        st, body, _ = self.owner.post(f"/api/v1/prompts/{d['id']}/versions", {}, headers=H(d["revision"]))
        self.assertEqual((st, body["error"]["code"]), (409, "NO_CHANGE"))

    def test_cannot_unapprove_production_version(self):
        d = self.new_prompt(content="X")
        pid, v1 = d["id"], d["head_version"]["id"]
        self.owner.post(f"/api/v1/prompts/{pid}/versions/{v1}/review", {"status": "APPROVED"})
        self.owner.put(f"/api/v1/prompts/{pid}/production", {"version_id": v1, "expected_version_id": None})
        st, body, _ = self.owner.post(f"/api/v1/prompts/{pid}/versions/{v1}/review", {"status": "REJECTED"})
        self.assertEqual((st, body["error"]["code"]), (409, "IN_PRODUCTION"))
        # đặt production phải đúng con trỏ hiện tại (compare-and-set)
        st, body, _ = self.owner.put(f"/api/v1/prompts/{pid}/production", {"version_id": None, "expected_version_id": None})
        self.assertEqual((st, body["error"]["code"]), (409, "PRODUCTION_CONFLICT"))


class T03DiffRestore(InfinityTestCase):
    def test_diff_and_restore_keeps_history(self):
        d = self.new_prompt(content="a\nb\nc")
        pid, v1 = d["id"], d["head_version"]["id"]
        st, d, _ = self.owner.post(f"/api/v1/prompts/{pid}/versions", {"content": "a\nB\nc\nd", "changelog": "v2"},
                                   headers=H(d["revision"]))
        v2 = d["head_version"]["id"]
        st, df, _ = self.owner.get(f"/api/v1/prompts/{pid}/diff?from={v1}&to={v2}")
        self.assertEqual(st, 200)
        self.assertEqual((df["added"], df["removed"]), (2, 1))
        ops = [(r["op"], r["text"]) for r in df["rows"]]
        self.assertEqual(ops, [("equal", "a"), ("delete", "b"), ("insert", "B"), ("equal", "c"), ("insert", "d")])
        st, d, _ = self.owner.post(f"/api/v1/prompts/{pid}/versions/{v1}/restore", {}, headers=H(d["revision"]))
        self.assertEqual(st, 201, d)
        self.assertEqual(d["head_version"]["version_no"], 3)
        self.assertEqual(d["head_version"]["restored_from"], v1)
        self.assertEqual(d["draft_content"], "a\nb\nc")
        st, vs, _ = self.owner.get(f"/api/v1/prompts/{pid}/versions")
        self.assertEqual([v["version_no"] for v in vs["items"]], [3, 2, 1])
        st, old2, _ = self.owner.get(f"/api/v1/prompts/{pid}/versions/{v2}")
        self.assertEqual(old2["content"], "a\nB\nc\nd")
        st, df, _ = self.owner.get(f"/api/v1/prompts/{pid}/diff?from={v1}&to=head")
        self.assertTrue(df["identical"])


class T04Conflict(InfinityTestCase):
    def test_two_sessions_same_revision_conflict_without_silent_loss(self):
        d = self.new_prompt(content="Gốc")
        pid, rev = d["id"], d["revision"]
        editor = self.h.client()
        self.assertEqual(editor.login("bientap", EDITOR_PW)[0], 200)
        st, e, _ = editor.put(f"/api/v1/prompts/{pid}/draft", {"content": "Bản của biên tập"}, headers=H(rev))
        self.assertEqual(st, 200)
        st, body, _ = self.owner.put(f"/api/v1/prompts/{pid}/draft", {"content": "Bản của chủ"}, headers=H(rev))
        self.assertEqual(st, 409)
        self.assertEqual(body["error"]["code"], "REVISION_CONFLICT")
        self.assertEqual(body["error"]["details"]["draft_content"], "Bản của biên tập")
        self.assertEqual(body["error"]["details"]["current_revision"], e["revision"])
        st, now, _ = self.owner.get(f"/api/v1/prompts/{pid}")
        self.assertEqual(now["draft_content"], "Bản của biên tập", "bản của người lưu trước không bị ghi đè âm thầm")
        # giải quyết có chủ đích: gửi lại với revision mới
        st, d, _ = self.owner.put(f"/api/v1/prompts/{pid}/draft", {"content": "Bản của chủ"}, headers=H(e["revision"]))
        self.assertEqual(st, 200)
        # thiếu If-Match bị từ chối; commit với revision cũ cũng xung đột
        st, body, _ = self.owner.put(f"/api/v1/prompts/{pid}/draft", {"content": "x"})
        self.assertEqual(st, 428)
        st, body, _ = self.owner.post(f"/api/v1/prompts/{pid}/versions", {}, headers=H(rev))
        self.assertEqual(st, 409)


class T05Search(InfinityTestCase):
    def setUp(self):
        super().setUp()
        self.a = self.new_prompt(title="Phân tích đơn hàng", content="Tổng hợp doanh thu theo tuần", tags=["báo cáo"])
        self.b = self.new_prompt(title="Viết mô tả sản phẩm", content="Mô tả áo thun cotton", tags=["etsy"])
        self.c = self.new_prompt(project_id=self.ids["p2"], title="Kế hoạch BTC", content="Chỉ đọc vị thế",
                                 tags=["btc"])

    def ids_for(self, query, client=None):
        st, r, _ = (client or self.owner).get(f"/api/v1/prompts?{query}")
        self.assertEqual(st, 200, r)
        return {i["id"] for i in r["items"]}, r

    def test_title_content_tag_project_and_no_diacritics(self):
        self.assertEqual(self.ids_for("q=Phân tích")[0], {self.a["id"]})
        self.assertEqual(self.ids_for("q=phan tich")[0], {self.a["id"]})
        self.assertEqual(self.ids_for("q=don hang")[0], {self.a["id"]}, "đ phải tìm được bằng d")
        self.assertEqual(self.ids_for("q=doanh thu")[0], {self.a["id"]}, "tìm trong nội dung")
        self.assertEqual(self.ids_for("q=cotton")[0], {self.b["id"]})
        self.assertEqual(self.ids_for("q=bao cao")[0], {self.a["id"]}, "tìm trong tag")
        self.assertEqual(self.ids_for("tag=etsy")[0], {self.b["id"]})
        self.assertEqual(self.ids_for(f"project_id={self.ids['p2']}")[0], {self.c["id"]})
        self.assertEqual(self.ids_for("q=thun")[0], {self.b["id"]})
        self.assertEqual(self.ids_for("q=khongtontai")[0], set())

    def test_stable_pagination(self):
        for i in range(7):
            self.new_prompt(title=f"Mẫu số {i}", content="giống nhau")
        seen = []
        for page in (1, 2, 3):
            ids, r = self.ids_for(f"q=giong nhau&limit=3&page={page}")
            seen += [i["id"] for i in r["items"]]
            self.assertEqual(r["total"], 7)
        self.assertEqual(len(seen), 7)
        self.assertEqual(len(set(seen)), 7)

    def test_editor_does_not_see_other_project(self):
        editor = self.h.client()
        editor.login("bientap", EDITOR_PW)
        ids, _ = self.ids_for("q=BTC", editor)
        self.assertEqual(ids, set())
        st, _, _ = editor.get(f"/api/v1/prompts/{self.c['id']}")
        self.assertEqual(st, 404)


class T05SearchFallback(T05Search):
    """Cùng bộ kiểm tra khi SQLite không có FTS5 (đường LIKE dự phòng)."""
    app_kw = {"force_like_search": True}

    def test_mode_is_like(self):
        _, r = self.ids_for("q=phan")
        self.assertEqual(r["search_mode"], "like")


class T06ImportExport(InfinityTestCase):
    def test_import_txt_md_then_export_preserves_unicode(self):
        nfd = unicodedata.normalize("NFD", "Tiếng Việt dạng NFD: Hoàng Thành Rồng")
        md = "# Tiêu đề từ Markdown\n\nNội dung có emoji 🐉 và " + nfd + "\n"
        txt = "Prompt TXT UTF-16: Đối soát sổ sách ✓\n"
        files = [{"filename": "C:\\Users\\Loc\\Desktop\\mau.md", "content_b64": b64(b"\xef\xbb\xbf" + md.encode())},
                 {"filename": "ghi-chu.txt", "content_b64": b64(txt.encode("utf-16"))}]
        st, pv, _ = self.owner.post("/api/v1/import/preview", {"project_id": self.ids["p1"], "files": files})
        self.assertEqual(st, 200, pv)
        self.assertTrue(all(i["ok"] for i in pv["items"]), pv)
        self.assertEqual(pv["items"][0]["filename"], "mau.md")
        self.assertEqual(pv["items"][0]["title"], "Tiêu đề từ Markdown")
        st, res, _ = self.owner.post("/api/v1/import/commit", {"project_id": self.ids["p1"], "items": [
            dict(files[0], action="create"), dict(files[1], action="create")]})
        self.assertEqual(st, 200, res)
        p_md, p_txt = res["results"][0]["prompt_id"], res["results"][1]["prompt_id"]
        st, raw, hdr = self.owner.get(f"/api/v1/prompts/{p_md}/export?format=md", raw=True)
        self.assertEqual(st, 200)
        self.assertEqual(raw.decode("utf-8"), md, "giữ nguyên từng code point (kể cả NFD), chỉ bỏ BOM")
        self.assertIn("attachment", hdr["Content-Disposition"])
        st, raw, _ = self.owner.get(f"/api/v1/prompts/{p_txt}/export?format=txt", raw=True)
        self.assertEqual(raw.decode("utf-8"), txt)
        st, js, _ = self.owner.get(f"/api/v1/prompts/{p_md}/export?format=json")
        self.assertEqual(js["versions"][0]["content"], md)

    def test_crlf_normalized_to_lf(self):
        st, pv, _ = self.owner.post("/api/v1/import/preview", {"project_id": self.ids["p1"], "files": [
            {"filename": "a.txt", "content_b64": b64("dòng 1\r\ndòng 2\r\n")}]})
        self.assertEqual(pv["items"][0]["chars"], len("dòng 1\ndòng 2\n"))

    def test_invalid_files_rejected_with_message(self):
        bad = [
            {"filename": "sai-ma.txt", "content_b64": b64(b"Xin ch\xe0o \xff\xfe\xfd")},
            {"filename": "nhi-phan.md", "content_b64": b64(b"MZ\x00\x01\x02binary")},
            {"filename": "virus.exe", "content_b64": b64("hello")},
            {"filename": "rong.txt", "content_b64": b64("   \n  ")},
            {"filename": "lon.txt", "content_b64": b64("a" * 1_000_001)},
            {"filename": "../.env", "content_b64": b64("SECRET=1")},
            {"filename": "hong.txt", "content_b64": "@@khong-phai-base64@@"},
        ]
        st, pv, _ = self.owner.post("/api/v1/import/preview", {"project_id": self.ids["p1"], "files": bad})
        self.assertEqual(st, 200)
        codes = [i.get("code") for i in pv["items"]]
        self.assertEqual(codes, ["IMPORT_BAD_ENCODING", "IMPORT_BINARY", "IMPORT_BAD_TYPE", "IMPORT_EMPTY",
                                 "IMPORT_TOO_LARGE", "IMPORT_BAD_NAME", "IMPORT_BAD_DATA"])
        self.assertTrue(all(not i["ok"] and i["error"] for i in pv["items"]))
        st, body, _ = self.owner.post("/api/v1/import/commit", {"project_id": self.ids["p1"], "items": [
            {"filename": "ok.txt", "content_b64": b64("hợp lệ"), "action": "create"}, dict(bad[0], action="create")]})
        self.assertEqual(st, 400)
        st, r, _ = self.owner.get("/api/v1/prompts?q=hop le")
        self.assertEqual(r["total"], 0, "nhập tất cả hoặc không gì cả")

    def test_duplicates_not_silently_overwritten(self):
        d = self.new_prompt(title="Trùng", content="Nội dung trùng")
        f = {"filename": "Trùng.txt", "content_b64": b64("Nội dung trùng")}
        st, pv, _ = self.owner.post("/api/v1/import/preview", {"project_id": self.ids["p1"], "files": [f]})
        it = pv["items"][0]
        self.assertEqual(it["duplicate_of"]["id"], d["id"])
        self.assertEqual(it["suggested_action"], "skip")
        self.assertEqual(it["title_conflict"]["id"], d["id"])
        # new_version với revision cũ → từ chối, không ghi
        self.owner.put(f"/api/v1/prompts/{d['id']}/draft", {"content": "đổi"}, headers=H(d["revision"]))
        st, body, _ = self.owner.post("/api/v1/import/commit", {"project_id": self.ids["p1"], "items": [
            dict(f, content_b64=b64("bản mới"), action="new_version", target_prompt_id=d["id"],
                 target_revision=d["revision"])]})
        self.assertEqual(st, 409)
        st, now, _ = self.owner.get(f"/api/v1/prompts/{d['id']}")
        self.assertEqual(now["draft_content"], "đổi")
        self.assertEqual(now["version_count"], 1)


class TRenderAnalysis(InfinityTestCase):
    def test_missing_variables_reported_not_guessed(self):
        block = self.new_prompt(title="Khối chữ ký", content="Trân trọng, {{ten}}")
        d = self.new_prompt(content=f"Chào {{{{khach}}}}.\n{{{{> {block['id']}}}}}")
        st, body, _ = self.owner.post(f"/api/v1/prompts/{d['id']}/render", {"variables": {"khach": "An"}})
        self.assertEqual(st, 400)
        self.assertEqual(body["error"]["details"]["missing_variables"], ["ten"])
        st, body, _ = self.owner.post(f"/api/v1/prompts/{d['id']}/render",
                                      {"variables": {"khach": "{{ten}}", "ten": "Lộc"}})
        self.assertEqual(st, 200, body)
        self.assertEqual(body["text"], "Chào {{ten}}.\nTrân trọng, Lộc", "giá trị không bị mở rộng lần hai")

    def test_block_cycle_detected(self):
        a = self.new_prompt(title="A", content="A")
        b = self.new_prompt(title="B", content=f"{{{{> {a['id']}}}}}")
        st, a2, _ = self.owner.post(f"/api/v1/prompts/{a['id']}/versions", {"content": f"{{{{> {b['id']}}}}}"},
                                    headers=H(a["revision"]))
        st, body, _ = self.owner.post(f"/api/v1/prompts/{a['id']}/render", {"variables": {}})
        self.assertEqual(st, 400)
        self.assertEqual(body["error"]["details"]["block_errors"][0]["code"], "BLOCK_CYCLE")

    def test_static_analysis_rules(self):
        line = "Hãy luôn trả lời ngắn gọn và chính xác."
        d = self.new_prompt(content=f"{line}\n{line}\n{{{{ 9sai }}}} và {{{{chua_dong")
        st, a, _ = self.owner.get(f"/api/v1/prompts/{d['id']}/analysis")
        rules = {f["rule"] for f in a["findings"]}
        self.assertTrue({"DUPLICATE_LINE", "VAR_SYNTAX", "VAR_UNCLOSED", "OUTPUT_FORMAT"} <= rules, rules)
        self.assertEqual(a["kind"], "STATIC_RULES")
