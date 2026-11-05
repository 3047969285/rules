import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rulehub import task  # noqa: E402
from test_rulehub import HookRunner, run, write  # noqa: E402

PASS = '{"verdict":"pass","findings":[]}'
FAIL = '{"verdict":"fail","findings":[{"rule":"1","evidence":"需求里有一条无法验收"}]}'


def prd(level):
    return "# 需求文档\n\n" + "".join(f"## {h}\n有实质内容\n\n" for h in task.PRD_SECTIONS[level])


def api(manifest="- src/main/java/com/shop/OrderController.java"):
    return ("# 接口文档\n\n## 接口清单\n路径 /orders 方法 POST 参数 userId 校验 非空 响应 订单号 状态码 200 鉴权 登录 幂等 订单号唯一\n\n"
            f"## 新增与修改文件清单\n{manifest}\n\n## 事务与并发\n下单加事务\n\n## 错误码与异常\n库存不足抛业务异常\n")


def plan(level, skip=()):
    return "# 测试计划\n\n" + "".join(
        f"## {d}\n" + ("不适用：这个接口没有该维度" if d in skip else "- 一条具体用例") + "\n\n" for d in task.REQUIRED_DIMS[level])


class PipelineBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proj = os.path.join(self.tmp.name, "shop")
        write(os.path.join(self.proj, "pom.xml"), "<project/>")
        self.docs = os.path.join(self.tmp.name, "devdocs")
        os.makedirs(self.docs)
        self.verdict_file = os.path.join(self.tmp.name, "verdict.txt")
        stub = os.path.join(self.tmp.name, "stub.sh")
        write(stub, '#!/bin/sh\ncat > /dev/null\ncat "$VERDICT_FILE"\n')
        os.chmod(stub, 0o755)
        patcher = mock.patch.dict(os.environ, {
            "RULEHUB_DEVDOCS": self.docs, "RULEHUB_CACHE": os.path.join(self.tmp.name, "cache"), "RULEHUB_REVIEWER_CMD": stub,
            "VERDICT_FILE": self.verdict_file, "RULEHUB_VIOLATIONS": os.devnull, "RULEHUB_CONFIG": "/nonexistent"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.verdict(PASS)

    def verdict(self, text):
        write(self.verdict_file, text)

    def t(self, *args):
        return run("task", *args, "--project", self.proj)

    def new(self, level="标准"):
        code, out, err = self.t("new", "--name", "下单", "--level", level, "--reason", "新增接口")
        self.assertEqual(code, 0, err)
        return task.active_task(self.proj)

    def src(self, name="OrderController.java", test=False):
        sub = "src/test/java/com/shop" if test else "src/main/java/com/shop"
        return os.path.join(self.proj, sub, name)

    def advance_ok(self, tpath, stage):
        self.assertEqual(self.t("review", stage)[0], 0, stage)
        self.assertEqual(self.t("advance", stage)[0], 0, stage)

    def fill(self, tpath, level):
        write(os.path.join(tpath, "prd.md"), prd(level))
        if level == "标准":
            write(os.path.join(tpath, "api.md"), api())
        write(os.path.join(tpath, "test-plan.md"), plan(level))

    def to_stage(self, tpath, level, last):
        """推进到 last 之后"""
        self.fill(tpath, level)
        for stage in task.STAGES[level]:
            if stage == "tests":
                self.assertEqual(self.t("red", "--cmd", "false")[0], 0)
            if stage == "impl":
                self.assertIsNone(task.gate_decision(self.src(), "s"))
                self.assertEqual(self.t("green", "--cmd", "true")[0], 0)
            self.advance_ok(tpath, stage)
            if stage == last:
                return


class TaskCommandTests(PipelineBase):
    def test_new_task_validations_and_templates(self):
        for args, expect in ((("--name", "x", "--level", "极简", "--reason", "r"), "极简"), (("--name", "x", "--level", "小", "--reason", " "), "理由")):
            code, _, err = self.t("new", *args)
            self.assertEqual(code, 2)
            self.assertIn(expect, err)
        code, _, err = run("task", "new", "--name", "x", "--level", "小", "--reason", "r", "--project", self.tmp.name)
        self.assertEqual(code, 2)
        self.assertIn("不在 Maven", err)
        big = self.new("标准")
        self.assertEqual(sorted(os.listdir(big)), ["api.md", "prd.md", "state.json", "test-plan.md"])
        self.assertIn(task.PLACEHOLDER, task.read(os.path.join(big, "prd.md")))
        self.assertEqual(self.t("new", "--name", "y", "--level", "小", "--reason", "r")[0], 2)
        self.assertIn(self.docs, big)

    def test_small_task_has_no_api_stage(self):
        small = self.new("小")
        self.assertFalse(os.path.exists(os.path.join(small, "api.md")))
        self.assertEqual(task.STAGES["小"], ["prd", "tests", "impl", "final"])

    def test_review_needs_complete_artifact_and_failing_review_blocks(self):
        tpath = self.new("小")
        code, _, err = self.t("review", "prd")
        self.assertEqual(code, 2)
        self.assertIn("还不完整", err)
        write(os.path.join(tpath, "prd.md"), prd("小"))
        self.verdict(FAIL)
        code, out, _ = self.t("review", "prd")
        self.assertEqual(code, 1)
        self.assertIn("无法验收", out)
        code, _, err = self.t("advance", "prd")
        self.assertEqual(code, 2)
        self.assertIn("评审没有通过", err)
        self.verdict("胡言乱语")
        self.assertIn("unavailable", self.t("review", "prd")[1])
        self.verdict(PASS)
        self.advance_ok(tpath, "prd")

    def test_unavailable_reviewer_reports_the_real_error(self):
        broken = os.path.join(self.tmp.name, "broken.sh")
        write(broken, "#!/bin/sh\ncat > /dev/null\necho 'network-outbound denied' >&2\nexit 1\n")
        os.chmod(broken, 0o755)
        tpath = self.new("小")
        write(os.path.join(tpath, "prd.md"), prd("小"))
        with mock.patch.dict(os.environ, {"RULEHUB_REVIEWER_CMD": broken}):
            code, out, _ = self.t("review", "prd")
        self.assertEqual(code, 1)
        self.assertIn("network-outbound denied", out)
        self.assertIn("api.anthropic.com", out)
        self.assertEqual(task.load_state(tpath)["reviews"]["prd"]["verdict"], "unavailable")

    def test_artifact_changed_after_review_needs_new_review(self):
        tpath = self.new("小")
        write(os.path.join(tpath, "prd.md"), prd("小"))
        self.assertEqual(self.t("review", "prd")[0], 0)
        write(os.path.join(tpath, "prd.md"), prd("小") + "\n多加一条\n")
        code, _, err = self.t("advance", "prd")
        self.assertEqual(code, 2)
        self.assertIn("重新评审", err)

    def test_stages_cannot_be_skipped(self):
        tpath = self.new("标准")
        write(os.path.join(tpath, "api.md"), api())
        code, _, err = self.t("review", "api")
        self.assertEqual(code, 2)
        self.assertIn("该评审的阶段是 prd", err)
        self.assertIn("下一阶段是 prd", self.t("advance", "api")[2])

    def test_api_validation(self):
        tpath = self.new("标准")
        write(os.path.join(tpath, "prd.md"), prd("标准"))
        self.advance_ok(tpath, "prd")
        write(os.path.join(tpath, "api.md"), api().replace("幂等", "").replace("- src/main/java/com/shop/OrderController.java", ""))
        code, _, err = self.t("review", "api")
        self.assertEqual(code, 2)
        self.assertIn("「幂等」", err)
        self.assertIn("至少要有一个文件", err)
        write(os.path.join(tpath, "api.md"), api())
        self.advance_ok(tpath, "api")

    def test_tests_need_all_dimensions_and_a_real_red(self):
        tpath = self.new("标准")
        self.to_stage(tpath, "标准", "api")
        write(os.path.join(tpath, "test-plan.md"), plan("标准").replace("## 性能\n- 一条具体用例", "## 性能\n待定"))
        code, _, err = self.t("review", "tests")
        self.assertEqual(code, 2)
        self.assertIn("「性能」维度", err)
        write(os.path.join(tpath, "test-plan.md"), plan("标准", skip=("性能", "并发")))
        self.assertIn("红的记录", self.t("review", "tests")[2])
        code, _, err = self.t("red", "--cmd", "true")
        self.assertEqual(code, 2)
        self.assertIn("先失败", err)
        self.assertEqual(self.t("red", "--cmd", "false")[0], 0)
        self.advance_ok(tpath, "tests")

    def test_green_must_be_after_last_source_write_and_must_pass(self):
        tpath = self.new("小")
        self.to_stage(tpath, "小", "tests")
        self.assertIsNone(task.gate_decision(self.src(), "s"))
        code, _, err = self.t("review", "impl")
        self.assertEqual(code, 2)
        self.assertIn("绿的全套测试", err)
        self.assertEqual(self.t("green", "--cmd", "false")[0], 2)
        self.assertEqual(self.t("green", "--cmd", "true")[0], 0)
        self.assertEqual(self.t("review", "impl")[0], 0)
        self.assertIsNone(task.gate_decision(self.src("Other.java"), "s"))
        code, _, err = self.t("advance", "impl")
        self.assertEqual(code, 2)
        self.assertIn("绿的全套测试", err)

    def test_full_standard_flow_reaches_done_and_next_task_is_allowed(self):
        tpath = self.new("标准")
        self.to_stage(tpath, "标准", "final")
        self.assertTrue(task.load_state(tpath)["done"])
        self.assertIn("没有进行中的任务", self.t("status")[1])
        self.assertIsNone(task.active_task(self.proj))
        self.new("小")

    def test_full_small_flow(self):
        tpath = self.new("小")
        self.to_stage(tpath, "小", "final")
        self.assertEqual(task.load_state(tpath)["stages"], ["prd", "tests", "impl", "final"])

    def test_final_prompt_lists_earlier_stage_verdicts_only_at_final(self):
        tpath = self.new("小")
        self.to_stage(tpath, "小", "impl")
        state = task.load_state(tpath)
        final = task.stage_prompt(tpath, state, "final")
        self.assertIn("=== 各阶段评审结论 ===", final)
        for stage in ("prd", "tests", "impl"):
            self.assertIn(f"{stage}: pass", final)
        self.assertNotIn("final: ", final.split("=== 各阶段评审结论 ===")[1].split("===")[0])
        self.assertNotIn("各阶段评审结论", task.stage_prompt(tpath, state, "impl"))

    def test_status_reports_next_step(self):
        self.new("小")
        out = self.t("status")[1]
        self.assertIn("下一阶段 prd", out)
        self.assertIn("需求文档缺少或未填写", out)

    def test_review_can_be_switched_off_and_counts_as_skipped(self):
        cfg = os.path.join(self.tmp.name, "c.json")
        write(cfg, json.dumps({"hooks": {"stage_review": False}}))
        with mock.patch.dict(os.environ, {"RULEHUB_CONFIG": cfg, "RULEHUB_REVIEWER_CMD": "/usr/bin/false"}):
            tpath = self.new("小")
            write(os.path.join(tpath, "prd.md"), prd("小"))
            self.assertIn("skipped", self.t("review", "prd")[1])
            self.assertEqual(self.t("advance", "prd")[0], 0)


class StageGateTests(PipelineBase):
    def test_without_a_task_second_distinct_file_is_denied(self):
        self.assertIsNone(task.gate_decision(self.src(), "s1"))
        self.assertIsNone(task.gate_decision(self.src(), "s1"))
        decision, reason = task.gate_decision(self.src("Another.java"), "s1")
        self.assertEqual(decision, "deny")
        self.assertIn("rulehub task new", reason)
        self.assertIn("rulehub task skip", reason)
        self.assertIsNone(task.gate_decision(self.src("Another.java"), "other-session"))

    def test_non_java_and_non_project_files_are_ignored(self):
        for p in (os.path.join(self.proj, "README.md"), os.path.join(self.tmp.name, "loose", "A.java")):
            for i in range(3):
                self.assertIsNone(task.gate_decision(p + str(i if p.endswith(".java") else ""), "s"))

    def test_small_task_gate_follows_stages(self):
        tpath = self.new("小")
        self.assertEqual(task.gate_decision(self.src(test=True, name="OrderTest.java"), "s")[0], "deny")
        self.assertEqual(task.gate_decision(self.src(), "s")[0], "deny")
        write(os.path.join(tpath, "prd.md"), prd("小"))
        self.advance_ok(tpath, "prd")
        self.assertIsNone(task.gate_decision(self.src(test=True, name="OrderTest.java"), "s"))
        self.assertIn("先写测试", task.gate_decision(self.src(), "s")[1])
        write(os.path.join(tpath, "test-plan.md"), plan("小"))
        self.assertEqual(self.t("red", "--cmd", "false")[0], 0)
        self.advance_ok(tpath, "tests")
        self.assertIsNone(task.gate_decision(self.src(), "s"))
        state = task.load_state(tpath)
        self.assertEqual(len(state["files_written"]), 1)
        self.assertGreater(state["last_source_write"], 0)

    def test_standard_task_needs_api_before_tests_and_enforces_manifest(self):
        tpath = self.new("标准")
        write(os.path.join(tpath, "prd.md"), prd("标准"))
        self.advance_ok(tpath, "prd")
        self.assertEqual(task.gate_decision(self.src(test=True, name="OrderTest.java"), "s")[0], "deny")
        write(os.path.join(tpath, "api.md"), api())
        self.advance_ok(tpath, "api")
        self.assertIsNone(task.gate_decision(self.src(test=True, name="OrderTest.java"), "s"))
        write(os.path.join(tpath, "test-plan.md"), plan("标准"))
        self.assertEqual(self.t("red", "--cmd", "false")[0], 0)
        self.advance_ok(tpath, "tests")
        self.assertIsNone(task.gate_decision(self.src(), "s"))
        decision, reason = task.gate_decision(self.src("Surprise.java"), "s")
        self.assertEqual(decision, "deny")
        self.assertIn("不在接口文档", reason)

    def test_skip_task_allows_everything(self):
        code, out, _ = self.t("skip", "--reason", "用户说直接做")
        self.assertEqual(code, 0)
        for name in ("A.java", "B.java", "C.java"):
            self.assertIsNone(task.gate_decision(self.src(name), "s"))
        self.assertEqual(task.load_state(task.active_task(self.proj))["level"], "跳过")
        self.assertEqual(self.t("skip", "--reason", " ")[0], 2)

    def test_task_state_files_are_protected_but_documents_are_not(self):
        tpath = self.new("小")
        for name in ("state.json", "red.json", "green.json", "reviews/prd.md"):
            self.assertEqual(task.gate_decision(os.path.join(tpath, name), "s")[0], "deny", name)
        self.assertIsNone(task.gate_decision(os.path.join(tpath, "prd.md"), "s"))

    def test_gate_fails_open_when_devdocs_is_unavailable(self):
        with mock.patch.dict(os.environ, {"RULEHUB_DEVDOCS": "/nonexistent/volume/DevDocs"}):
            for name in ("A.java", "B.java", "C.java"):
                self.assertIsNone(task.gate_decision(self.src(name), "s"))
            code, _, err = self.t("new", "--name", "x", "--level", "小", "--reason", "r")
            self.assertEqual(code, 2)
            self.assertIn("固态文档目录不可用", err)

    def test_gate_can_be_switched_off(self):
        cfg = os.path.join(self.tmp.name, "c.json")
        write(cfg, json.dumps({"hooks": {"stage_gate": False}}))
        with mock.patch.dict(os.environ, {"RULEHUB_CONFIG": cfg}):
            for name in ("A.java", "B.java", "C.java"):
                self.assertIsNone(task.gate_decision(self.src(name), "s"))


class HookIntegrationTests(PipelineBase, HookRunner):
    def hook(self, script, path, **extra):
        payload = {"hook_event_name": "PreToolUse", "session_id": "hs", "tool_input": dict({"file_path": path, "content": "class A {}\n"}, **extra)}
        env = {k: os.environ[k] for k in ("RULEHUB_DEVDOCS", "RULEHUB_CACHE")}
        out = self.call(script, payload, extra_env=env)
        return json.loads(out)["hookSpecificOutput"] if out else None

    def test_stage_gate_script_denies_second_file(self):
        self.assertIsNone(self.hook("stage_gate.py", self.src()))
        got = self.hook("stage_gate.py", self.src("Two.java"))
        self.assertEqual(got["permissionDecision"], "deny")
        self.assertIn("后端开发流水线闸门", got["permissionDecisionReason"])

    def test_java_guard_stops_asking_about_tests_once_the_task_allows_them(self):
        test_file = self.src(test=True, name="OrderTest.java")
        self.assertEqual(self.hook("java_guard.py", test_file)["permissionDecision"], "ask")
        tpath = self.new("小")
        write(os.path.join(tpath, "prd.md"), prd("小"))
        self.advance_ok(tpath, "prd")
        self.assertIsNone(self.hook("java_guard.py", test_file))


class DoctorTests(PipelineBase):
    def test_doctor_reports_devdocs(self):
        src = os.path.join(self.tmp.name, "rules")
        write(os.path.join(src, "a.mdc"), "---\ndescription: d\nalwaysApply: true\n---\n正文\n")
        out = run("--source", src, "--home", os.path.join(self.tmp.name, "home"), "doctor")[1]
        self.assertIn("[ok] 固态文档目录可用", out)
        with mock.patch.dict(os.environ, {"RULEHUB_DEVDOCS": "/nonexistent/volume/DevDocs"}):
            self.assertIn("[warn] 固态文档目录不可用", run("--source", src, "--home", os.path.join(self.tmp.name, "home"), "doctor")[1])


if __name__ == "__main__":
    unittest.main()
