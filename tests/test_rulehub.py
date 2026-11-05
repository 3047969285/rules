import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rulehub import history, launchd, reviewer, status  # noqa: E402
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

    def call(self, script, payload, config=None, extra_env=None):
        env = dict(os.environ, RULEHUB_CONFIG=config or "/nonexistent.json", RULEHUB_VIOLATIONS=os.devnull,
                   RULEHUB_REVIEWER_CMD="/usr/bin/false")
        env.update(extra_env or {})
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

    def test_prompt_scaffold_leaves_the_role_to_the_model(self):
        def ask(prompt):
            out = self.call("rule_router.py", {"prompt": prompt})
            return json.loads(out)["hookSpecificOutput"]["additionalContext"]
        for prompt in ("帮我写个 Java 接口", "设计一个 Dify 工作流", "你好"):
            msg = ask(prompt)
            for expect in ("#role: （你判断：随任务变化", "#task:", "#约束:", "身份随任务自己判断，不固定"):
                self.assertIn(expect, msg)
            for gone in ("Java 后端资深专家", "Dify 与模型应用开发专家", "#rule", "#输入", "#输出", "#上下文"):
                self.assertNotIn(gone, msg)
        self.assertIn("先用 Skill 工具加载 rule-hub:backend-flow", ask("帮我写个 Java 接口"))

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


class UnreadableSourceTests(Base):
    def test_permission_error_is_reported_not_hidden(self):
        locked = os.path.join(self.src, "b")
        os.chmod(locked, 0)
        self.addCleanup(os.chmod, locked, 0o755)
        code, _, err = self.sync()
        self.assertEqual(code, 2)
        self.assertIn("读取规则源失败", err)
        self.assertNotIn("没有任何规则", err)


class PreWriteGuardTests(HookRunner, unittest.TestCase):
    BAD_JAVA = "public class A {\n    public void f() {\n        Pattern p = Pattern.compile(\"a\");\n    }\n}\n"
    GOOD_JAVA = "public class A {\n    private static final Pattern P = Pattern.compile(\"a\");\n}\n"

    def pre(self, path, **tool_input):
        out = self.call("java_guard.py", {"hook_event_name": "PreToolUse", "tool_input": dict(tool_input, file_path=path)})
        return json.loads(out)["hookSpecificOutput"] if out else None

    def test_write_with_violation_is_denied_before_writing(self):
        got = self.pre("/p/A.java", content=self.BAD_JAVA)
        self.assertEqual(got["permissionDecision"], "deny")
        self.assertIn("Pattern.compile", got["permissionDecisionReason"])
        self.assertIn("3:", got["permissionDecisionReason"])

    def test_clean_content_and_non_java_pass(self):
        self.assertIsNone(self.pre("/p/A.java", content=self.GOOD_JAVA))
        self.assertIsNone(self.pre("/p/notes.md", content=self.BAD_JAVA))

    def test_edit_and_multiedit_are_checked(self):
        self.assertEqual(self.pre("/p/A.java", old_string="x", new_string="        throw new RuntimeException(\"x\");")["permissionDecision"], "deny")
        got = self.pre("/p/A.java", edits=[{"old_string": "a", "new_string": "new Thread(r).start();"}])
        self.assertEqual(got["permissionDecision"], "deny")

    def test_comments_are_ignored(self):
        self.assertIsNone(self.pre("/p/A.java", content="// throw new RuntimeException(\"x\")\n/* new Thread() */\n"))

    def test_mapper_xml_checked_but_pom_is_not(self):
        sql = "<mapper><select id=\"a\">select * from t where id = ${id}</select></mapper>"
        self.assertEqual(self.pre("/p/UserMapper.xml", content=sql)["permissionDecision"], "deny")
        self.assertIsNone(self.pre("/p/pom.xml", content="<project>${revision}</project>"))

    def test_test_file_still_asks_and_deny_wins(self):
        self.assertEqual(self.pre("/p/src/test/FooTest.java", content=self.GOOD_JAVA)["permissionDecision"], "ask")
        self.assertEqual(self.pre("/p/src/test/FooTest.java", content=self.BAD_JAVA)["permissionDecision"], "deny")


class BudgetTests(Base):
    def test_always_budget_is_a_hard_limit(self):
        self.sync()
        base = ("--source", self.src, "--home", self.home)
        self.assertEqual(run(*base, "check")[0], 0)
        write(os.path.join(self.src, "a", "core-always.mdc"), ALWAYS + "字" * 4000)
        code, out, _ = run(*base, "check")
        self.assertEqual(code, 1)
        self.assertIn("硬上限", out)
        self.assertEqual(run(*base, "doctor")[0], 1)
        code, _, err = self.sync()
        self.assertEqual(code, 0)
        self.assertIn("超过硬上限", err)


class EnforcementTests(Base):
    def reg(self, data):
        write(os.path.join(self.src, "enforcers.json"), json.dumps(data, ensure_ascii=False))

    def test_report_groups_and_strict(self):
        self.reg({"core-always": {"hooks": ["h1"], "manual": "其余靠自觉"},
                  "java-scoped": {"manual": "判断类", "gap": "可机检未做"},
                  "gone": {"manual": "已删除的规则"}})
        code, out, _ = run("--source", self.src, "enforcement")
        self.assertEqual(code, 0)
        self.assertIn("== 机器执行 1 条", out)
        self.assertIn("== 靠自觉 1 条", out)
        self.assertIn("== 未登记 1 条", out)
        self.assertIn("on-demand", out)
        self.assertIn("== 已知缺口 1 条", out)
        self.assertIn("gone 已没有对应规则", out)
        self.assertEqual(run("--source", self.src, "enforcement", "--strict")[0], 1)

    def test_strict_passes_when_complete_and_registry_is_not_a_rule(self):
        self.reg({"core-always": {"hooks": ["h"]}, "java-scoped": {"manual": "m"}, "on-demand": {"manual": "m"}})
        self.assertEqual(run("--source", self.src, "enforcement", "--strict")[0], 0)
        self.assertEqual(len(load_rules(self.src)[0]), 3)
        self.assertEqual(run("--source", self.src, "lint")[0], 0)

    def test_doctor_reports_unregistered(self):
        self.sync()
        self.assertIn("[warn] 3 条规则没登记执行者", run("--source", self.src, "--home", self.home, "doctor")[1])
        self.reg({"core-always": {"manual": "m"}, "java-scoped": {"manual": "m"}, "on-demand": {"manual": "m"}})
        self.assertIn("[ok] 每条规则都登记了", run("--source", self.src, "--home", self.home, "doctor")[1])


class SecretGuardTests(HookRunner, unittest.TestCase):
    AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
    GH = "ghp_" + "a1B2c3D4" * 5
    PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"
    SK = "sk-ant-" + "api03" + "q" * 30
    PW = "Tr0ub4dor" + "&3"

    def pre(self, **tool_input):
        out = self.call("secret_guard.py", {"hook_event_name": "PreToolUse", "tool_input": dict(tool_input, file_path="/p/f.txt")})
        return json.loads(out)["hookSpecificOutput"] if out else None

    def test_hard_formats_are_denied_and_masked(self):
        for secret in (self.AWS, self.GH, self.PEM, self.SK):
            got = self.pre(content=f"line1\nkey = {secret}\n")
            self.assertEqual(got["permissionDecision"], "deny", secret)
            self.assertIn("2:", got["permissionDecisionReason"])
            self.assertNotIn(secret, got["permissionDecisionReason"])

    def test_soft_cases_ask(self):
        for text in (f'password = "{self.PW}"', f"url: postgres://admin:{self.PW}@db.corp.example/app",
                     f"jdbc:mysql://db/app?user=a&password={self.PW}"):
            got = self.pre(content=text)
            self.assertEqual(got["permissionDecision"], "ask", text)
            self.assertNotIn(self.PW, got["permissionDecisionReason"])

    def test_placeholders_local_hosts_and_allow_marker_pass(self):
        for text in ('password = "${DB_PASSWORD}"', 'api_key = "<your-api-key>"', 'token = os.environ["API_TOKEN_X1"]',
                     f"postgres://root:{self.PW}@localhost/app", f"key = {self.AWS}  # rulehub:allow-secret",
                     'password = "changeme-please"', "没有任何凭证的普通文本", "url = f\"postgres://admin:{pw}@db.corp/app\""):
            self.assertIsNone(self.pre(content=text), text)

    def test_application_config_files_may_hold_passwords_but_not_tokens(self):
        def at(path, text):
            out = self.call("secret_guard.py", {"hook_event_name": "PreToolUse", "tool_input": {"file_path": path, "content": text}})
            return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out else None
        pw = f'api-secret: "{self.PW}"'
        self.assertIsNone(at("/p/src/main/resources/application.yml", pw))
        self.assertIsNone(at("/p/application-prod.properties", f"db.password={self.PW}\nspring.datasource.url=jdbc:mysql://h/a?password={self.PW}"))
        self.assertEqual(at("/p/src/Config.java", pw), "ask")
        self.assertEqual(at("/p/other.yml", pw), "ask")
        self.assertEqual(at("/p/application.yml", f"key: {self.AWS}"), "deny")

    def test_edit_multiedit_and_switch(self):
        self.assertEqual(self.pre(old_string="a", new_string=self.AWS)["permissionDecision"], "deny")
        self.assertEqual(self.pre(edits=[{"old_string": "a", "new_string": self.GH}])["permissionDecision"], "deny")
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "c.json")
            write(cfg, json.dumps({"hooks": {"secret_guard": False}}))
            payload = {"hook_event_name": "PreToolUse", "tool_input": {"file_path": "/p/f", "content": self.AWS}}
            self.assertEqual(self.call("secret_guard.py", payload, cfg), "")


class JavaScopeTests(HookRunner, unittest.TestCase):
    BAD = "public class A {\n    public void f() {\n        Pattern p = Pattern.compile(\"a\");\n    }\n}\n"

    def test_guard_and_gate_only_apply_inside_scope(self):
        with tempfile.TemporaryDirectory() as d:
            mine, other = os.path.join(d, "mine"), os.path.join(d, "other")
            cfg = os.path.join(d, "c.json")
            write(cfg, json.dumps({"java_scope": [mine]}))
            for root in (mine, other):
                write(os.path.join(root, "pom.xml"), "<project/>")
                write(os.path.join(root, "A.java"), self.BAD)
            def guard(root, event):
                return self.call("java_guard.py", {"hook_event_name": event, "tool_input": {"file_path": os.path.join(root, "A.java"), "content": self.BAD}}, cfg)
            for event in ("PreToolUse", "PostToolUse"):
                self.assertNotEqual(guard(mine, event), "", event)
                self.assertEqual(guard(other, event), "", event)
            def gate(root):
                t = os.path.join(d, "t.jsonl")
                write(t, json.dumps({"message": {"content": [{"type": "tool_use", "name": "Write", "input": {"file_path": os.path.join(root, "A.java")}}]}}) + "\n")
                return self.call("stop_gate.py", {"transcript_path": t}, cfg)
            self.assertIn("block", gate(mine))
            self.assertEqual(gate(other), "")

    def test_no_scope_means_everywhere_and_prefix_is_not_a_match(self):
        with tempfile.TemporaryDirectory() as d:
            mine = os.path.join(d, "proj")
            sibling = os.path.join(d, "proj-other")
            cfg = os.path.join(d, "c.json")
            payload = lambda root: {"hook_event_name": "PreToolUse", "tool_input": {"file_path": os.path.join(root, "A.java"), "content": self.BAD}}
            self.assertNotEqual(self.call("java_guard.py", payload(sibling)), "")
            write(cfg, json.dumps({"java_scope": [mine]}))
            self.assertEqual(self.call("java_guard.py", payload(sibling), cfg), "")


def U(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def USE(uid, name, **inp):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": uid, "name": name, "input": inp}]}}


def RES(uid, text="ok"):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": uid, "content": text}]}}


def SAY(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


class EvidenceGateTests(HookRunner, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(os.path.join(self.proj, "tests"))
        os.makedirs(os.path.join(self.proj, "src"))
        self.code = os.path.join(self.proj, "src", "a.py")

    def gate(self, entries, config=None, env=None, active=False):
        t = os.path.join(self.tmp.name, "t.jsonl")
        write(t, "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n")
        cfg = None
        if config is not None:
            cfg = os.path.join(self.tmp.name, "c.json")
            write(cfg, json.dumps(config))
        out = self.call("evidence_gate.py", {"transcript_path": t, "stop_hook_active": active}, cfg, env)
        return json.loads(out) if out else None

    def stub(self, reply, record=None):
        path = os.path.join(self.tmp.name, "stub.sh")
        write(path, "#!/bin/sh\n" + (f"cat > {record}\n" if record else "cat > /dev/null\n") + f"cat <<'EOF'\n{reply}\nEOF\n")
        os.chmod(path, 0o755)
        return {"RULEHUB_REVIEWER_CMD": path}

    def test_claim_without_any_tool_is_blocked(self):
        got = self.gate([U("验证过了吗"), SAY("已验证通过")])
        self.assertEqual(got["decision"], "block")
        self.assertIn("没有调用任何工具", got["reason"])
        self.assertIsNone(self.gate([U("你好"), SAY("你好呀")]))

    def test_plans_and_negations_are_not_claims_but_real_claims_are(self):
        for ok in ("接下来我会用新会话实测一下", "还没有实测", "未验证通过，需要再看", "没有全部通过", "我准备跑通它"):
            self.assertIsNone(self.gate([U("x"), SAY(ok)]), ok)
        for bad in ("已实测通过", "实测结果正常", "验证通过了", "测试全部通过", "已完成"):
            self.assertEqual(self.gate([U("x"), SAY(bad)])["decision"], "block", bad)

    def test_edit_then_claim_needs_a_command_after_the_edit(self):
        edit = [U("改一下"), USE("1", "Write", file_path=self.code), RES("1")]
        got = self.gate(edit + [SAY("改好了，测试通过")])
        self.assertIn("没有运行任何命令", got["reason"])
        self.assertIsNone(self.gate(edit + [SAY("改好了")]))

    def test_project_with_tests_needs_a_test_command(self):
        base = [U("改"), USE("1", "Edit", file_path=self.code), RES("1"), USE("2", "Bash", command="ls"), RES("2")]
        got = self.gate(base + [SAY("全部通过")])
        self.assertIn("有测试", got["reason"])
        ok = [U("改"), USE("1", "Edit", file_path=self.code), RES("1"),
              USE("2", "Bash", command="python3 -m unittest discover"), RES("2"), SAY("全部通过")]
        self.assertIsNone(self.gate(ok))

    def test_non_code_edits_and_stop_hook_active_pass(self):
        memo = os.path.join(self.tmp.name, "memory", "x.md")
        self.assertIsNone(self.gate([U("记一下"), USE("1", "Write", file_path=memo), RES("1"), SAY("已完成")]))
        self.assertIsNone(self.gate([U("x"), SAY("已验证通过")], active=True))

    def test_switches(self):
        entries = [U("x"), SAY("已验证通过")]
        self.assertIsNone(self.gate(entries, config={"hooks": {"evidence_gate": False}}))
        self.assertIsNone(self.gate(entries, env={"RULEHUB_REVIEW": "1"}))

    def test_reviewer_findings_block_and_are_advisory_text(self):
        turn = [U("看看"), USE("1", "Bash", command="ls"), RES("1", "a b"), SAY("目录里有两个文件")]
        reply = '{"violations":[{"rule":"code-first-facts","evidence":"没有读文件就下了结论"},{"rule":"不存在的规则","evidence":"x"}]}'
        got = self.gate(turn, env=self.stub(reply))
        self.assertEqual(got["decision"], "block")
        self.assertIn("[code-first-facts] 没有读文件就下了结论", got["reason"])
        self.assertIn("裁决者", got["reason"])
        self.assertNotIn("不存在的规则", got["reason"])

    def test_reviewer_clean_broken_or_disabled_never_blocks(self):
        turn = [U("看看"), USE("1", "Bash", command="ls"), RES("1"), SAY("两个文件")]
        for reply in ('{"violations":[]}', "胡言乱语", '{"violations":[{"rule":"code-first-facts","evidence":""}]}'):
            self.assertIsNone(self.gate(turn, env=self.stub(reply)), reply)
        bad = '{"violations":[{"rule":"code-first-facts","evidence":"x"}]}'
        self.assertIsNone(self.gate(turn, config={"hooks": {"rule_review": False}}, env=self.stub(bad)))
        self.assertIsNone(self.gate(turn))

    def test_reviewer_input_is_redacted_and_contains_the_turn(self):
        record = os.path.join(self.tmp.name, "seen.txt")
        key = "AKIA" + "ABCDEFGHIJKLMNOP"
        turn = [U("看看"), USE("1", "Bash", command=f"echo {key}"), RES("1", "输出"), SAY("好了")]
        self.gate(turn, env=self.stub('{"violations":[]}', record))
        seen = T.read(record)
        self.assertNotIn(key, seen)
        self.assertIn("[已脱敏]", seen)
        self.assertIn("用户原话:\n看看", seen)
        self.assertIn("code-first-facts", seen)

    def test_reviewer_sees_test_command_and_result_even_when_buried(self):
        record = os.path.join(self.tmp.name, "seen2.txt")
        cmd = "python3 - <<'X'\n" + "print('填充')\n" * 80 + "X\npython3 -m unittest discover -s tests\necho 收尾"
        out = "Ran 76 tests in 2.3s\n\nOK\n" + "后续无关输出\n" * 60
        turn = [U("跑测试"), USE("1", "Bash", command=cmd), RES("1", out), SAY("测试全过")]
        self.gate(turn, env=self.stub('{"violations":[]}', record))
        seen = T.read(record)
        self.assertIn("含测试命令: unittest", seen)
        self.assertIn("Ran 76 tests in 2.3s", seen)
        self.assertIn("OK", seen)

    def test_reviewer_sees_facts_from_the_middle_of_a_moderate_output(self):
        record = os.path.join(self.tmp.name, "seen3.txt")
        out = "头部\n" + "填充\n" * 60 + "/proj/黄岛 -> 是 git 仓库\n" + "尾部填充\n" * 60
        self.gate([U("查仓库"), USE("1", "Bash", command="git check"), RES("1", out), SAY("黄岛是 git 仓库")],
                  env=self.stub('{"violations":[]}', record))
        self.assertIn("/proj/黄岛 -> 是 git 仓库", T.read(record))

    def test_very_long_output_keeps_head_and_tail_and_says_so(self):
        record = os.path.join(self.tmp.name, "seen4.txt")
        out = "开头标记\n" + "填" * 5000 + "\n结尾标记"
        self.gate([U("x"), USE("1", "Bash", command="y"), RES("1", out), SAY("好")], env=self.stub('{"violations":[]}', record))
        seen = T.read(record)
        self.assertIn("开头标记", seen)
        self.assertIn("结尾标记", seen)
        self.assertIn("中间省略", seen)

    def test_blocks_are_logged(self):
        log = os.path.join(self.tmp.name, "v.jsonl")
        self.gate([U("x"), SAY("已验证通过")], env={"RULEHUB_VIOLATIONS": log})
        row = json.loads(T.read(log).splitlines()[0])
        self.assertEqual((row["hook"], row["rule"]), ("evidence_gate", "code-first-facts"))


class ViolationsAndAgentsTests(Base):
    def test_guards_log_rule_ids(self):
        log = os.path.join(self.tmp.name, "v.jsonl")
        runner = HookRunner()
        runner.assertEqual = self.assertEqual
        bad = 'public class A {\n    public void f() {\n        Pattern p = Pattern.compile("a");\n    }\n}\n'
        runner.call("java_guard.py", {"hook_event_name": "PreToolUse", "tool_input": {"file_path": "/p/A.java", "content": bad}},
                    extra_env={"RULEHUB_VIOLATIONS": log})
        row = json.loads(T.read(log).splitlines()[0])
        self.assertEqual((row["hook"], row["rule"]), ("java_guard", "java-05-code-style"))
        self.assertNotIn("content", row)

    def test_violations_command(self):
        log = os.path.join(self.tmp.name, "v.jsonl")
        write(log, "".join(json.dumps({"t": __import__("time").time(), "hook": h, "rule": r, "detail": "d", "cwd": "/"}) + "\n"
                           for h, r in (("evidence_gate", "code-first-facts"), ("evidence_gate", "code-first-facts"), ("secret_guard", "security-confidentiality"))))
        write(os.path.join(self.src, "enforcers.json"), json.dumps(
            {"core-always": {"manual": "靠自觉"}, "java-scoped": {"hooks": ["h"]}, "on-demand": {"manual": "m"}}))
        os.environ["RULEHUB_VIOLATIONS"] = log
        self.addCleanup(os.environ.pop, "RULEHUB_VIOLATIONS", None)
        code, out, _ = run("--source", self.src, "violations")
        self.assertEqual(code, 0)
        self.assertIn("共 3 次", out)
        self.assertIn("2  code-first-facts", out)
        self.assertIn("evidence_gate", out)
        os.remove(log)
        self.assertIn("还没有违规记录", run("--source", self.src, "violations")[1])

    def test_reviewer_definitions_for_each_tool(self):
        models = {"claude": "haiku", "codex": "gpt-6-luna", "cursor": "composer-2.5"}
        codex = reviewer.render_agents("codex", models)["agents/rule-reviewer.toml"]
        for expect in ('model = "gpt-6-luna"', 'sandbox_mode = "read-only"', "developer_instructions = '''"):
            self.assertIn(expect, codex)
        cursor = reviewer.render_agents("cursor", models)["agents/rule-reviewer.md"]
        self.assertIn("model: composer-2.5", cursor)
        self.assertIn("readonly: true", cursor)
        claude = reviewer.render_claude_agent("haiku")
        self.assertIn("model: haiku", claude)
        self.assertIn("tools: Read, Grep, Glob", claude)
        plugin_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents", "rule-reviewer.md")
        self.assertEqual(T.read(plugin_file), claude)

    def test_sync_writes_reviewers_and_honours_config_model(self):
        cfg = os.path.join(self.tmp.name, "c.json")
        write(cfg, json.dumps({"reviewers": {"codex": "gpt-9-nano"}}))
        os.environ["RULEHUB_CONFIG"] = cfg
        self.addCleanup(os.environ.pop, "RULEHUB_CONFIG", None)
        code, _, err = self.sync("--target", "codex", "--target", "cursor")
        self.assertEqual(code, 0, err)
        self.assertIn('model = "gpt-9-nano"', T.read(self.path(".codex", "agents", "rule-reviewer.toml")))
        self.assertIn("model: composer-2.5", T.read(self.path(".cursor", "agents", "rule-reviewer.md")))
        self.assertEqual(run("--source", self.src, "--home", self.home, "--target", "codex", "--target", "cursor", "check")[0], 0)


if __name__ == "__main__":
    unittest.main()
