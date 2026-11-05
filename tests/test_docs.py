"""Tài liệu sinh từ dữ liệu không được lệch khỏi dữ liệu."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DocsTest(unittest.TestCase):
    def test_permission_matrix_document_matches_the_registries(self):
        m = load("tn_docs")
        self.assertTrue(m.OUT.exists(), "chưa sinh D03 — chạy: python scripts/tn_docs.py")
        self.assertEqual(m.OUT.read_text(encoding="utf-8"), m.build(), "D03 lệch dữ liệu — chạy lại: python scripts/tn_docs.py")

    def test_matrix_document_reports_zero_agents_with_unapproved_sensitive_rights(self):
        self.assertIn("không có phê duyệt = **0**", load("tn_docs").build())


if __name__ == "__main__":
    unittest.main()
