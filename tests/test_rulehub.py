import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rulehub import history, launchd, status  # noqa: E402
from rulehub import targets as T  # noqa: E402
from rulehub.cli import main  # noqa: E402
from rulehub.model import duplicate_lines, expand_glob, fix_source, load_rules  # noqa: E402

ALWAYS = "---\ndescription: 常驻规则\nalwaysApply: true\n---\n\n# 常驻\n\n- 这是一条足够长的重复内容用来测试跨规则的重复检测功能\n"
SCOPED = "---\ndescription: Java 规则\nglobs: **/*.{java,xml}\nalwaysApply: false\n---\n\n# Java\n\n- 这是一条足够长的重复内容用来测试跨规则的重复检测功能\n"
ONDEMAND = "---\ndescription: 按需规则\nalwaysApply: false\nwhen: 用户提到按需时使用\n---\n\n# 按需\n"


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = os.path.join(self.tmp.name, "src")
        self.home = os.path.join(self.tmp.name, "home")
        write(os.path.join(self.src, "a", "core-always.mdc"), ALWAYS)
        write(os.path.join(self.src, "b", "java-scoped.mdc"), SCOPED)
        write(os.path.join(self.src, "c", "on-demand.mdc"), ONDEMAND)

    def sync(self, *extra):
        flags = [a for a in extra if a in ("--force", "--dry-run", "--quiet")]
        rest = [a for a in extra if a not in flags]
        return run("--source", self.src, "--home", self.home, *rest, "sync", *flags)

    def path(self, *p):
        return os.path.join(self.home, *p)


class ModelTests(Base):
    def test_classify_and_globs(self):
        rules, issues = load_rules(self.src)
        kinds = {r.id: r.kind for r in rules}
        self.assertEqual(kinds, {"core-always": "always", "java-scoped": "scoped", "on-demand": "ondemand"})
        self.assertEqual(next(r for r in rules if r.id == "java-scoped").globs, ["**/*.java", "**/*.xml"])
        self.assertEqual(issues, [])

    def test_expand_nested_braces(self):
        self.assertEqual(sorted(expand_glob("**/*.{a,b}")), ["**/*.a", "**/*.b"])

    def test_issues(self):
        write(os.path.join(self.src, "bad.mdc"), "没有 frontmatter\n")
        write(os.path.join(self.src, "bom.mdc"), "﻿" + ONDEMAND.replace("\n", "\r\n"))
        write(os.path.join(self.src, "dir2", "on-demand.mdc"), ONDEMAND)
        write(os.path.join(self.src, "ref.mdc"), "---\ndescription: d\n---\n见 `nope.mdc` 与简写 `java-sc` 以及 `java-99`\n")
        _, issues = load_rules(self.src)
        text = "\n".join(f"{lv} {f} {m}" for lv, f, m in issues)
        self.assertNotIn("java-sc", text)
        for expect in ("缺少 frontmatter", "含 BOM", "含 CRLF", "重复", "不存在的规则 nope"):
            self.assertIn(expect, text)

    def test_bad_flag_is_error(self):
        write(os.path.join(self.src, "flag.mdc"), "---\ndescription: d\nalwaysApply: yes\n---\n正文\n")
        self.assertTrue(any("alwaysApply" in m for _, _, m in load_rules(self.src)[1]))

    def test_fix_source_and_duplicates(self):
        write(os.path.join(self.src, "bom.mdc"), "﻿" + ONDEMAND.replace("\n", "\r\n"))
        self.assertEqual(fix_source(self.src), 1)
        _, issues = load_rules(self.src)
        self.assertFalse([i for i in issues if "BOM" in i[2] or "CRLF" in i[2]])
        dups = duplicate_lines(load_rules(self.src)[0])
        self.assertEqual(sorted(next(iter(dups.values()))), ["core-always", "java-scoped"])


class RenderTests(Base):
    def test_all_rendered_files_validate(self):
        rules, _ = load_rules(self.src)
        for files in (T.render_claude(rules), T.render_codex(rules), T.render_cursor(rules), T.render_paste(rules)):
            for rel, content in files.items():
                self.assertEqual(T.validate(rel, content), [], rel)

    def test_claude_layout(self):
        rules, _ = load_rules(self.src)
        files = T.render_claude(rules)
        self.assertEqual(sorted(files), ["rules/rs-core-always.md", "rules/rs-java-scoped.md", "skills/rs-on-demand/SKILL.md"])
        self.assertIn('paths:\n  - "**/*.java"\n  - "**/*.xml"', files["rules/rs-java-scoped.md"])
        self.assertIn("用户提到按需时使用", files["skills/rs-on-demand/SKILL.md"])
        self.assertTrue(files["rules/rs-core-always.md"].startswith("---\n"))

    def test_cursor_frontmatter(self):
        rules, _ = load_rules(self.src)
        files = T.render_cursor(rules)
        self.assertIn("alwaysApply: true", files["rules/rs-core-always.mdc"])
        self.assertIn("globs: **/*.java,**/*.xml", files["rules/rs-java-scoped.mdc"])
        self.assertNotIn("description", files["rules/rs-java-scoped.mdc"])
        self.assertIn("description: 按需规则", files["rules/rs-on-demand.mdc"])

    def test_validate_catches_problems(self):
        self.assertTrue(T.validate("skills/rs-x/SKILL.md", "---\nname: wrong\ndescription: \"d\"\n---\n"))
        self.assertTrue(T.validate("rules/rs-x.md", "没有 frontmatter"))
        self.assertTrue(T.validate("rules/rs-x.mdc", "---\ndescription: d\n---\n"))
        self.assertEqual(T.validate("rules/rs-x.mdc", "---\nglobs: *.py\nalwaysApply: false\n---\n"), [])
        self.assertTrue(T.validate("rules/rs-x.md", "﻿---\ndescription: d\n---\n"))


class SyncTests(Base):
    def test_sync_idempotent_and_manifest(self):
        self.assertEqual(self.sync()[0], 0)
        self.assertTrue(os.path.exists(self.path(".claude", "rules", "rs-core-always.md")))
        self.assertTrue(os.path.exists(self.path(".claude", ".rulehub-manifest.json")))
        self.assertTrue(os.path.exists(self.path(".codex", "skills", "rs-java-scoped", "SKILL.md")))
        code, out, _ = self.sync()
        self.assertEqual(code, 0)
        self.assertIn("写入 0", out)

    def test_removes_stale_but_keeps_user_files(self):
        self.sync()
        write(self.path(".claude", "rules", "mine.md"), "我自己的规则")
        write(self.path(".claude", "rules", "rs-mine.md"), "用户手写 没有生成标记")
        os.remove(os.path.join(self.src, "c", "on-demand.mdc"))
        self.assertEqual(self.sync("--force")[0], 0)
        self.assertFalse(os.path.exists(self.path(".claude", "skills", "rs-on-demand")))
        self.assertTrue(os.path.exists(self.path(".claude", "rules", "mine.md")))
        self.assertTrue(os.path.exists(self.path(".claude", "rules", "rs-mine.md")))

    def test_legacy_generated_files_are_adopted(self):
        write(self.path(".claude", "rules", "rs-old.md"), "---\ndescription: x\n---\n<!-- 自动生成自 旧脚本 -->\n")
        self.sync()
        self.assertFalse(os.path.exists(self.path(".claude", "rules", "rs-old.md")))

    def test_missing_source_keeps_everything(self):
        self.sync()
        code, _, err = run("--source", os.path.join(self.tmp.name, "gone"), "--home", self.home, "sync")
        self.assertEqual(code, 2)
        self.assertIn("找不到规则源", err)
        self.assertTrue(os.path.exists(self.path(".claude", "rules", "rs-core-always.md")))

    def test_empty_source_refused(self):
        self.sync()
        empty = os.path.join(self.tmp.name, "empty")
        os.makedirs(empty)
        code, _, err = run("--source", empty, "--home", self.home, "sync")
        self.assertEqual(code, 2)
        self.assertIn("没有任何规则", err)
        self.assertTrue(os.path.exists(self.path(".claude", "rules", "rs-core-always.md")))

    def test_sharp_drop_guard(self):
        self.sync()
        for name in ("a/core-always", "b/java-scoped"):
            os.remove(os.path.join(self.src, name + ".mdc"))
        code, _, err = self.sync()
        self.assertEqual(code, 2)
        self.assertIn("一半", err)
        self.assertTrue(os.path.exists(self.path(".claude", "rules", "rs-core-always.md")))
        self.assertEqual(self.sync("--force")[0], 0)

    def test_source_error_blocks_sync(self):
        write(os.path.join(self.src, "bad.mdc"), "无 frontmatter\n")
        code, _, err = self.sync()
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(self.path(".claude")))

    def test_dry_run_writes_nothing(self):
        self.assertEqual(self.sync("--dry-run")[0], 0)
        self.assertFalse(os.path.exists(self.path(".claude")))

    def test_cursor_project(self):
        proj = os.path.join(self.tmp.name, "proj")
        self.assertEqual(self.sync("--target", "cursor", "--project", proj)[0], 0)
        self.assertTrue(os.path.exists(os.path.join(proj, ".cursor", "rules", "rs-java-scoped.mdc")))
        self.assertTrue(os.path.exists(self.path(".cursor", "rulehub", "user-rules-paste.txt")))


class CodexAgentsTests(Base):
    def test_block_preserves_user_text(self):
        agents = self.path(".codex", "AGENTS.md")
        write(agents, "## 我的偏好\n- 先写文档\n")
        self.sync()
        text = T.read(agents)
        self.assertTrue(text.startswith("## 我的偏好\n- 先写文档\n"))
        self.assertEqual(text.count(T.BEGIN), 1)
        self.assertIn("### core-always", text)
        write(os.path.join(self.src, "a", "core-always.mdc"), ALWAYS.replace("常驻", "改过"))
        self.sync()
        text2 = T.read(agents)
        self.assertEqual(text2.count(T.BEGIN), 1)
        self.assertIn("## 我的偏好", text2)
        self.assertIn("改过", text2)

    def test_unpaired_markers_refused(self):
        write(self.path(".codex", "AGENTS.md"), T.BEGIN + "\n残缺\n")
        code, _, err = self.sync()
        self.assertEqual(code, 2)
        self.assertIn("不成对", err)

    def test_size_limit(self):
        write(os.path.join(self.src, "a", "core-always.mdc"), ALWAYS + "字" * 12000)
        code, _, err = self.sync()
        self.assertEqual(code, 2)
        self.assertIn("截断", err)


class CheckTests(Base):
    def test_check_pass_then_drift(self):
        self.sync()
        base = ("--source", self.src, "--home", self.home)
        self.assertEqual(run(*base, "check")[0], 0)
        write(self.path(".claude", "rules", "rs-core-always.md"), "被手改了")
        code, out, _ = run(*base, "check")
        self.assertEqual(code, 1)
        self.assertIn("不一致", out)
        os.remove(self.path(".codex", "skills", "rs-on-demand", "SKILL.md"))
        self.assertIn("缺失", run(*base, "check")[1])

    def test_lint_and_list(self):
        write(os.path.join(self.src, "bom.mdc"), "﻿" + ONDEMAND)
        self.assertEqual(run("--source", self.src, "lint", "--fix")[0], 0)
        code, out, _ = run("--source", self.src, "lint", "--dups")
        self.assertIn("[dup]", out)
        code, out, _ = run("--source", self.src, "list")
        self.assertIn("scoped\tjava-scoped\t**/*.java,**/*.xml", out)


class ImportTests(Base):
    def doc(self, name, text):
        path = os.path.join(self.tmp.name, name)
        write(path, text)
        return path

    def test_plain_markdown_defaults_to_ondemand(self):
        code, out, _ = run("--source", self.src, "import", self.doc("My Notes.md", "# 部署手册\n\n先备份再发布\n"))
        self.assertEqual(code, 0)
        text = T.read(os.path.join(self.src, "06-导入", "my-notes.mdc"))
        self.assertIn("description: 部署手册", text)
        self.assertIn("alwaysApply: false", text)
        self.assertEqual(self.sync()[0], 0)
        self.assertTrue(os.path.exists(self.path(".claude", "skills", "rs-my-notes", "SKILL.md")))

    def test_cursorrules_and_chinese_name(self):
        self.assertEqual(run("--source", self.src, "import", self.doc(".cursorrules", "使用 4 空格缩进\n"))[0], 0)
        self.assertTrue(os.path.exists(os.path.join(self.src, "06-导入", "cursorrules.mdc")))
        self.assertEqual(run("--source", self.src, "import", self.doc("规范.md", "正文\n"))[0], 0)
        self.assertTrue([f for f in os.listdir(os.path.join(self.src, "06-导入")) if f.startswith("imported-")])

    def test_kinds(self):
        doc = self.doc("a.md", "正文\n")
        run("--source", self.src, "import", doc, "--kind", "always", "--id", "a-always")
        run("--source", self.src, "import", doc, "--globs", "**/*.py", "--id", "a-scoped")
        rules = {r.id: r for r in load_rules(self.src)[0]}
        self.assertEqual(rules["a-always"].kind, "always")
        self.assertEqual(rules["a-scoped"].globs, ["**/*.py"])

    def test_errors(self):
        doc = self.doc("a.md", "正文\n")
        self.assertEqual(run("--source", self.src, "import", doc, "--kind", "scoped")[0], 2)
        self.assertEqual(run("--source", self.src, "import", self.doc("x.docx", "二进制"))[0], 2)
        self.assertEqual(run("--source", self.src, "import", doc, "--id", "bad id")[0], 2)
        self.assertEqual(run("--source", self.src, "import", doc, "--id", "core-always")[0], 2)
        self.assertEqual(run("--source", self.src, "import", doc)[0], 0)
        self.assertEqual(run("--source", self.src, "import", doc)[0], 2)
        self.assertEqual(run("--source", self.src, "import", doc, "--force")[0], 0)

    def test_reuses_existing_frontmatter(self):
        doc = self.doc("b.mdc", SCOPED)
        self.assertEqual(run("--source", self.src, "import", doc, "--id", "b-copy")[0], 0)
        self.assertEqual({r.id: r.kind for r in load_rules(self.src)[0]}["b-copy"], "scoped")


class HookRunner:
    HOOKS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")

    def call(self, script, payload, config=None):
        env = dict(os.environ, RULEHUB_CONFIG=config or "/nonexistent.json")
        r = subprocess.run([sys.executable, os.path.join(self.HOOKS, script)], input=json.dumps(payload),
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

class HookConfigTests(HookRunner, unittest.TestCase):
    def test_router_on_and_off(self):
        self.assertIn("规则体系路由", self.call("rule_router.py", {"prompt": "写 Java mapper"}))
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "c.json")
            write(cfg, json.dumps({"hooks": {"rule_router": False}}))
            self.assertEqual(self.call("rule_router.py", {"prompt": "写 Java mapper"}, cfg), "")

    def test_guard_and_gate_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            write(os.path.join(d, "pom.xml"), "<project/>")
            java = os.path.join(d, "A.java")
            write(java, "public class A {\n    public void f() {\n        Pattern p = Pattern.compile(\"a\");\n    }\n}\n")
            payload = {"hook_event_name": "PostToolUse", "tool_input": {"file_path": java}}
            self.assertIn("方法体内禁止 Pattern.compile", self.call("java_guard.py", payload))
            cfg = os.path.join(d, "c.json")
            write(cfg, json.dumps({"hooks": {"java_guard": False, "stop_gate": False}}))
            self.assertEqual(self.call("java_guard.py", payload, cfg), "")
            transcript = os.path.join(d, "t.jsonl")
            write(transcript, json.dumps({"message": {"content": [{"type": "tool_use", "name": "Write", "input": {"file_path": java}}]}}) + "\n")
            stop = {"transcript_path": transcript}
            self.assertIn("block", self.call("stop_gate.py", stop))
            self.assertEqual(self.call("stop_gate.py", stop, cfg), "")

    def test_broken_config_keeps_hooks_on(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "c.json")
            write(cfg, "{坏的")
            self.assertIn("规则体系路由", self.call("rule_router.py", {"prompt": "x"}, cfg))


class RobustnessTests(Base):
    def test_codex_description_limit(self):
        write(os.path.join(self.src, "long.mdc"), "---\ndescription: " + "长" * 900 + "\nalwaysApply: false\n---\n正文\n")
        rules, _ = load_rules(self.src)
        codex = T.render_codex(rules)["skills/rs-long/SKILL.md"]
        self.assertLessEqual(len(json.loads(codex.split("description: ")[1].split("\n")[0])), 500)

    def test_dangling_target_does_not_block_others(self):
        os.makedirs(self.home)
        os.symlink("/nonexistent/dot-codex", self.path(".codex"))
        code, _, err = self.sync()
        self.assertEqual(code, 2)
        self.assertIn("部分目标写入失败", err)
        self.assertTrue(os.path.exists(self.path(".claude", "rules", "rs-core-always.md")))

    def test_hook_mode_reports_without_failing(self):
        code, out, _ = run("--source", os.path.join(self.tmp.name, "gone"), "--home", self.home, "sync", "--hook")
        self.assertEqual(code, 0)
        self.assertIn("systemMessage", out)
        self.assertEqual(self.sync("--quiet")[0], 0)
        self.assertEqual(run("--source", self.src, "--home", self.home, "sync", "--quiet", "--hook")[1], "")


class RouterAndGateTests(HookRunner, unittest.TestCase):
    def route(self, prompt):
        return self.call("rule_router.py", {"prompt": prompt})

    def test_router_precision(self):
        for harmless in ("后端服务器重启了吗", "JavaScript 怎么写循环", "我想看看 javadoc 是什么", "把这个 docx 文件删掉"):
            self.assertNotIn("命中按任务技能", self.route(harmless), harmless)
        for hit in ("帮我写个 Java 接口", "改一下 UserMapper.xml 里的 mapper", "处理黄岛的 xlsx 模板"):
            self.assertIn("命中按任务技能", self.route(hit), hit)

    def test_gate_ignores_non_build_projects_and_accepts_real_builds(self):
        with tempfile.TemporaryDirectory() as d:
            def gate(java, command=None):
                lines = [{"message": {"content": [{"type": "tool_use", "name": "Write", "input": {"file_path": java}}]}}]
                if command:
                    lines.append({"message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": command}}]}})
                t = os.path.join(d, "t.jsonl")
                write(t, "\n".join(json.dumps(x) for x in lines) + "\n")
                return self.call("stop_gate.py", {"transcript_path": t})
            loose = os.path.join(d, "loose", "A.java")
            write(loose, "class A {}\n")
            self.assertEqual(gate(loose), "")
            proj = os.path.join(d, "proj")
            write(os.path.join(proj, "pom.xml"), "<project/>")
            java = os.path.join(proj, "src", "B.java")
            write(java, "class B {}\n")
            self.assertIn("block", gate(java))
            self.assertIn("block", gate(java, "echo mvn"))
            self.assertEqual(gate(java, "cd proj && mvn -q compile"), "")
            self.assertEqual(gate(java, "./gradlew build"), "")


class HistoryTests(Base):
    def test_snapshot_only_on_change_and_restore(self):
        self.assertEqual(self.sync()[0], 0)
        self.assertEqual(len(history.list_snapshots(self.home)), 1)
        self.sync()
        self.assertEqual(len(history.list_snapshots(self.home)), 1)
        write(os.path.join(self.src, "a", "core-always.mdc"), ALWAYS.replace("常驻", "改坏了"))
        write(os.path.join(self.src, "extra.mdc"), ONDEMAND)
        self.sync()
        first = history.list_snapshots(self.home)[-1]
        self.assertEqual(len(history.list_snapshots(self.home)), 2)
        write(os.path.join(self.src, "unsaved.mdc"), ONDEMAND)
        code, _, err = run("--source", self.src, "--home", self.home, "restore", first)
        self.assertEqual(code, 2)
        self.assertIn("--yes", err)
        code, out, _ = run("--source", self.src, "--home", self.home, "restore", first, "--yes")
        self.assertEqual(code, 0)
        self.assertNotIn("改坏了", T.read(os.path.join(self.src, "a", "core-always.mdc")))
        self.assertFalse(os.path.exists(os.path.join(self.src, "extra.mdc")))
        self.assertFalse(os.path.exists(os.path.join(self.src, "unsaved.mdc")))
        self.assertIn("unsaved.mdc", " ".join(history.mdc_files(os.path.join(history.base_dir(self.home), history.list_snapshots(self.home)[0]))))
        self.assertEqual(len(history.list_snapshots(self.home)), 3)

    def test_prune_and_unknown_snapshot(self):
        for i in range(5):
            write(os.path.join(self.src, "a", "core-always.mdc"), ALWAYS + f"第{i}版\n")
            history.snapshot(self.src, self.home, keep=3)
        self.assertEqual(len(history.list_snapshots(self.home)), 3)
        self.assertEqual(run("--source", self.src, "--home", self.home, "restore", "nope", "--yes")[0], 2)

    def test_dry_run_makes_no_snapshot(self):
        self.sync("--dry-run")
        self.assertEqual(history.list_snapshots(self.home), [])


class StatusAndDoctorTests(Base):
    def setUp(self):
        super().setUp()
        os.environ.pop("RULEHUB_STATE", None)

    def test_state_records_success_and_failure(self):
        self.sync()
        self.assertTrue(status.last(self.home)["ok"])
        run("--source", os.path.join(self.tmp.name, "gone"), "--home", self.home, "sync")
        st = status.last(self.home)
        self.assertFalse(st["ok"])
        self.assertIn("找不到规则源", st["message"])

    def test_router_warns_after_failed_sync(self):
        run("--source", os.path.join(self.tmp.name, "gone"), "--home", self.home, "sync")
        env = dict(os.environ, RULEHUB_CONFIG="/nonexistent", RULEHUB_STATE=status.path(self.home))
        hooks = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "rule_router.py")
        r = subprocess.run([sys.executable, hooks], input=json.dumps({"prompt": "你好"}), capture_output=True, text=True, env=env)
        self.assertIn("上次规则同步失败", r.stdout)

    def test_doctor_levels(self):
        self.sync()
        code, out, _ = run("--source", self.src, "--home", self.home, "doctor")
        self.assertEqual(code, 0)
        self.assertIn("[ok] 3 条规则", out)
        os.remove(self.path(".claude", "rules", "rs-core-always.md"))
        self.assertIn("[warn]", run("--source", self.src, "--home", self.home, "doctor")[1])
        write(os.path.join(self.src, ".x.mdc.icloud"), "")
        code, out, _ = run("--source", self.src, "--home", self.home, "doctor")
        self.assertEqual(code, 1)
        self.assertIn("iCloud 云端占位", out)

    def test_doctor_detects_dangling_target(self):
        os.makedirs(self.home)
        os.symlink("/nonexistent/x", self.path(".codex"))
        code, out, _ = run("--source", self.src, "--home", self.home, "doctor")
        self.assertEqual(code, 1)
        self.assertIn("悬空链接", out)

    def test_stats_and_launchd_print(self):
        code, out, _ = run("--source", self.src, "stats")
        self.assertIn("== always 1 条", out)
        code, out, _ = run("--source", self.src, "launchd", "print")
        self.assertIn("WatchPaths", out)
        self.assertIn(self.src, out)
        self.assertIn(os.path.join(self.src, "a"), out)
        self.assertIn("StartInterval", out)
        self.assertIn("/usr/bin/python3", out)
        self.assertFalse(os.path.exists(launchd.plist_path(self.home)))


if __name__ == "__main__":
    unittest.main()
