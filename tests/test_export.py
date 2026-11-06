import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rulehub import export  # noqa: E402
from test_rulehub import Base, run, write  # noqa: E402

AWS = "AKIA" + "ABCDEFGHIJKLMNOP"


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


class ExportTests(Base):
    def setUp(self):
        super().setUp()
        self.out = os.path.join(self.tmp.name, "pub")

    def test_project_names_are_generalized_everywhere_and_other_text_is_kept(self):
        write(os.path.join(self.src, "d", "m.json"), '{"root": "**/PlagiarismChecking-server", "when": "黄岛文档任务"}\n')
        write(os.path.join(self.src, "d", "n.mdc"), "放 `model.enums.huangdao`，用 `huangdao.*`\r\n保持换行\r\n")
        code, out, _ = run("--source", self.src, "export", self.out)
        self.assertEqual(code, 0)
        self.assertIn("已导出 5 个文件", out)
        self.assertEqual(read(os.path.join(self.out, "d", "m.json")), '{"root": "**/example-project", "when": "项目文档任务"}\n')
        self.assertEqual(read(os.path.join(self.out, "d", "n.mdc")), "放 `model.enums.example`，用 `example.*`\r\n保持换行\r\n")
        for name in ("PlagiarismChecking", "黄岛", "huangdao"):
            for root, _, files in os.walk(self.out):
                for f in files:
                    self.assertNotIn(name, read(os.path.join(root, f)))

    def test_source_is_not_modified(self):
        write(os.path.join(self.src, "d", "m.json"), '{"x": "黄岛"}\n')
        run("--source", self.src, "export", self.out)
        self.assertIn("黄岛", read(os.path.join(self.src, "d", "m.json")))

    def test_credential_blocks_the_whole_export_without_leaking_it(self):
        write(os.path.join(self.src, "d", "bad.mdc"), f"key = {AWS}\n")
        code, _, err = run("--source", self.src, "export", self.out)
        self.assertEqual(code, 2)
        self.assertIn("拒绝导出", err)
        self.assertIn("bad.mdc 1:", err)
        self.assertNotIn(AWS, err)
        self.assertNotIn(AWS[:4] + "***", err)
        self.assertFalse(os.path.exists(self.out))

    def test_failed_export_keeps_the_previous_one(self):
        self.assertEqual(run("--source", self.src, "export", self.out)[0], 0)
        before = sorted(os.listdir(self.out))
        write(os.path.join(self.src, "d", "bad.mdc"), f"key = {AWS}\n")
        self.assertEqual(run("--source", self.src, "export", self.out)[0], 2)
        self.assertEqual(sorted(os.listdir(self.out)), before)

    def test_reexport_replaces_old_files_and_refuses_foreign_directories(self):
        self.assertEqual(run("--source", self.src, "export", self.out)[0], 0)
        os.remove(os.path.join(self.src, "c", "on-demand.mdc"))
        self.assertEqual(run("--source", self.src, "export", self.out)[0], 0)
        self.assertFalse(os.path.exists(os.path.join(self.out, "c", "on-demand.mdc")))
        foreign = os.path.join(self.tmp.name, "mine")
        write(os.path.join(foreign, "keep.txt"), "x")
        code, _, err = run("--source", self.src, "export", foreign)
        self.assertEqual(code, 2)
        self.assertIn("拒绝覆盖", err)
        self.assertEqual(read(os.path.join(foreign, "keep.txt")), "x")

    def test_extra_files_go_under_extra_with_the_same_checks(self):
        extra = os.path.join(self.tmp.name, "personal-rules.md")
        write(extra, "处理 PlagiarismChecking-server 或黄岛文档任务\n")
        code, out, _ = run("--source", self.src, "export", self.out, "--extra", extra)
        self.assertEqual(code, 0)
        self.assertIn("已导出 4 个文件", out)
        self.assertEqual(read(os.path.join(self.out, "extra", "personal-rules.md")), "处理 example-project 或项目文档任务\n")
        self.assertEqual(read(extra), "处理 PlagiarismChecking-server 或黄岛文档任务\n")

    def test_extra_with_credential_missing_unsupported_or_duplicate_name_is_rejected(self):
        bad = os.path.join(self.tmp.name, "bad.md")
        write(bad, f"key = {AWS}\n")
        code, _, err = run("--source", self.src, "export", self.out, "--extra", bad)
        self.assertEqual(code, 2)
        self.assertIn("extra/bad.md 1:", err)
        self.assertNotIn(AWS, err)
        self.assertFalse(os.path.exists(self.out))
        self.assertIn("读取附加文件失败", run("--source", self.src, "export", self.out, "--extra", "/不存在.md")[2])
        script = os.path.join(self.tmp.name, "a.py")
        write(script, "x")
        self.assertIn("只支持", run("--source", self.src, "export", self.out, "--extra", script)[2])
        txt = os.path.join(self.tmp.name, "paste.txt")
        write(txt, "黄岛\n")
        self.assertEqual(run("--source", self.src, "export", self.out, "--extra", txt)[0], 0)
        self.assertEqual(read(os.path.join(self.out, "extra", "paste.txt")), "项目\n")
        other = os.path.join(self.tmp.name, "sub", "bad.md")
        write(other, "ok\n")
        write(bad, "ok\n")
        self.assertIn("重名", run("--source", self.src, "export", self.out, "--extra", bad, "--extra", other)[2])

    def test_only_rule_files_are_exported_and_empty_source_is_rejected(self):
        write(os.path.join(self.src, "x", "note.txt"), "不导出")
        write(os.path.join(self.src, "x", "note.mdc"), "导出\n")
        write(os.path.join(self.src, "x", ".DS_Store"), "x")
        run("--source", self.src, "export", self.out)
        self.assertEqual(sorted(os.listdir(os.path.join(self.out, "x"))), ["note.mdc"])
        with tempfile.TemporaryDirectory() as empty:
            self.assertIn("没有可导出", run("--source", empty, "export", os.path.join(empty, "o"))[2])


if __name__ == "__main__":
    unittest.main()
