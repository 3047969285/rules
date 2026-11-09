import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_rulehub import Base, run, write  # noqa: E402


class PackageTests(Base):
    def setUp(self):
        super().setUp()
        self.out = os.path.join(self.tmp.name, "install-packages")

    def package(self, *flags):
        return run("--source", self.src, "package", self.out, *flags)

    def test_generates_six_tool_packages_and_checks_them(self):
        code, out, err = self.package()
        self.assertEqual(code, 0, err)
        self.assertIn("已生成", out)
        expected = {
            "Mac/codex/AGENTS.rulehub.md",
            "Mac/codex/skills/rs-java-scoped/SKILL.md",
            "Mac/codex/agents/rule-reviewer.toml",
            "Mac/Claude/rules/rs-core-always.md",
            "Mac/Claude/skills/rs-on-demand/SKILL.md",
            "Mac/cursor/rules/rs-java-scoped.mdc",
            "Mac/cursor/rulehub/user-rules-paste.txt",
            "Mac/cursor/agents/rule-reviewer.md",
            "Windows/codex/AGENTS.rulehub.md",
            "Windows/Claude/rules/rs-core-always.md",
            "Windows/cursor/rules/rs-java-scoped.mdc",
        }
        for rel in expected:
            self.assertTrue(os.path.isfile(os.path.join(self.out, rel)), rel)
        self.assertIn("%USERPROFILE%", self.read("Windows/codex/INSTALL.md"))
        self.assertIn("~/.codex/", self.read("Mac/codex/INSTALL.md"))
        self.assertEqual(self.package("--check")[0], 0)

    def test_packages_are_idempotent_and_source_is_unchanged(self):
        original = self.read_source("a/core-always.mdc")
        self.assertEqual(self.package()[0], 0)
        before = self.read("Mac/codex/skills/rs-java-scoped/SKILL.md")
        code, out, err = self.package()
        self.assertEqual(code, 0, err)
        self.assertIn("0 个文件", out)
        self.assertEqual(self.read("Mac/codex/skills/rs-java-scoped/SKILL.md"), before)
        self.assertEqual(self.read_source("a/core-always.mdc"), original)

    def test_dry_run_and_check_do_not_write(self):
        code, out, err = self.package("--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("将生成", out)
        self.assertFalse(os.path.exists(self.out))
        code, out, _ = self.package("--check")
        self.assertEqual(code, 1)
        self.assertIn("缺失", out)
        self.assertFalse(os.path.exists(self.out))

    def test_foreign_output_is_preserved_and_rejected(self):
        write(os.path.join(self.out, "keep.txt"), "user data\n")
        code, _, err = self.package()
        self.assertEqual(code, 2)
        self.assertIn("拒绝覆盖", err)
        self.assertEqual(self.read("keep.txt"), "user data\n")

    def test_rebuild_removes_stale_generated_files(self):
        self.assertEqual(self.package()[0], 0)
        stale = os.path.join(self.out, "Mac", "Claude", "skills", "rs-on-demand", "SKILL.md")
        self.assertTrue(os.path.isfile(stale))
        os.remove(os.path.join(self.src, "c", "on-demand.mdc"))
        self.assertEqual(self.package()[0], 0)
        self.assertFalse(os.path.exists(stale))
        self.assertEqual(self.package("--check")[0], 0)

    def test_source_errors_leave_existing_package_untouched(self):
        self.assertEqual(self.package()[0], 0)
        before = self.read("Windows/Claude/rules/rs-core-always.md")
        write(os.path.join(self.src, "bad.mdc"), "missing frontmatter\n")
        code, _, _ = self.package()
        self.assertEqual(code, 2)
        self.assertEqual(self.read("Windows/Claude/rules/rs-core-always.md"), before)

    def test_package_cannot_be_nested_in_or_replace_the_source(self):
        code, _, err = run("--source", self.src, "package", self.src)
        self.assertEqual(code, 2)
        self.assertIn("规则源目录之外", err)
        nested = os.path.join(self.src, "packages")
        code, _, err = run("--source", self.src, "package", nested)
        self.assertEqual(code, 2)
        self.assertIn("规则源目录之外", err)

    def read(self, *parts):
        with open(os.path.join(self.out, *parts), encoding="utf-8") as f:
            return f.read()

    def read_source(self, *parts):
        with open(os.path.join(self.src, *parts), encoding="utf-8") as f:
            return f.read()


if __name__ == "__main__":
    unittest.main()
