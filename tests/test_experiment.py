import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rulehub import experiment  # noqa: E402
from test_rulehub import Base, HookRunner, run, write  # noqa: E402

BAD_JAVA = "public class A {\n    public void f() {\n        Pattern p = Pattern.compile(\"a\");\n    }\n}\n"
AWS = "AKIA" + "ABCDEFGHIJKLMNOP"


def row(rule, hook="rule_review", t=None, detail="d"):
    return {"t": t if t is not None else time.time(), "hook": hook, "rule": rule, "detail": detail}


class TrialHookTests(HookRunner, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = os.path.join(self.tmp.name, "v.jsonl")

    def cfg(self, *rules):
        path = os.path.join(self.tmp.name, "c.json")
        write(path, json.dumps({"experimental": {r: 1.0 for r in rules}}))
        return path

    def call_logged(self, script, payload, *rules):
        return self.call(script, payload, self.cfg(*rules) if rules else None, {"RULEHUB_VIOLATIONS": self.log})

    def logged(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_java_guard_denies_normally_but_only_logs_during_trial(self):
        payload = {"hook_event_name": "PreToolUse", "tool_input": {"file_path": "/p/A.java", "content": BAD_JAVA}}
        self.assertIn("deny", self.call_logged("java_guard.py", payload))
        self.assertNotIn("trial", self.logged()[0])
        self.assertEqual(self.call_logged("java_guard.py", payload, "java-05-code-style"), "")
        self.assertTrue(self.logged()[-1]["trial"])
        self.assertEqual(self.logged()[-1]["rule"], "java-05-code-style")

    def test_other_rules_in_the_same_file_still_deny_during_trial(self):
        mixed = BAD_JAVA + "public class B {\n    public void g() {\n        throw new RuntimeException(\"x\");\n    }\n}\n"
        payload = {"hook_event_name": "PreToolUse", "tool_input": {"file_path": "/p/A.java", "content": mixed}}
        out = self.call_logged("java_guard.py", payload, "java-05-code-style")
        self.assertIn("deny", out)
        self.assertIn("RuntimeException", out)
        self.assertNotIn("Pattern.compile", out)

    def test_java_guard_post_scan_stays_silent_for_trial_rules(self):
        with tempfile.TemporaryDirectory() as d:
            java = os.path.join(d, "A.java")
            write(java, BAD_JAVA)
            payload = {"hook_event_name": "PostToolUse", "tool_input": {"file_path": java}}
            self.assertIn("Pattern.compile", self.call_logged("java_guard.py", payload))
            only_style = self.call_logged("java_guard.py", payload, "java-05-code-style")
            self.assertNotIn("Pattern.compile", only_style)
            self.assertIn("JavaDoc", only_style)
            self.assertEqual(self.call_logged("java_guard.py", payload, "java-05-code-style", "java-07-javadoc"), "")

    def test_test_file_ask_is_logged_but_silent_during_trial(self):
        payload = {"hook_event_name": "PreToolUse", "tool_input": {"file_path": "/p/src/test/java/ATest.java", "content": "class ATest {}\n"}}
        self.assertIn("ask", self.call_logged("java_guard.py", payload))
        self.assertEqual(self.call_logged("java_guard.py", payload, "java-11-artifact-boundary"), "")
        self.assertEqual(self.logged()[-1]["rule"], "java-11-artifact-boundary")

    def test_secret_guard_logs_but_does_not_block_during_trial(self):
        payload = {"hook_event_name": "PreToolUse", "tool_input": {"file_path": "/p/f.txt", "content": f"key = {AWS}\n"}}
        self.assertIn("deny", self.call_logged("secret_guard.py", payload))
        self.assertEqual(self.call_logged("secret_guard.py", payload, "security-confidentiality"), "")
        self.assertTrue(self.logged()[-1]["trial"])
        self.assertNotIn(AWS, json.dumps(self.logged()))

    def test_stop_gate_logs_but_does_not_block_during_trial(self):
        with tempfile.TemporaryDirectory() as d:
            write(os.path.join(d, "pom.xml"), "<project/>")
            java = os.path.join(d, "A.java")
            write(java, "class A {}\n")
            transcript = os.path.join(d, "t.jsonl")
            write(transcript, json.dumps({"message": {"content": [{"type": "tool_use", "name": "Write", "input": {"file_path": java}}]}}) + "\n")
            stop = {"transcript_path": transcript}
            self.assertIn("block", self.call_logged("stop_gate.py", stop))
            self.assertEqual(self.call_logged("stop_gate.py", stop, "java-09-checklist"), "")
            self.assertEqual(self.logged()[-1]["rule"], "java-09-checklist")

    def test_stage_gate_logs_but_does_not_block_during_trial(self):
        from rulehub import task
        with tempfile.TemporaryDirectory() as d:
            proj = os.path.join(d, "shop")
            write(os.path.join(proj, "pom.xml"), "<project/>")
            env = {"RULEHUB_DEVDOCS": os.path.join(d, "docs"), "RULEHUB_CACHE": os.path.join(d, "cache"), "RULEHUB_VIOLATIONS": self.log}
            os.makedirs(env["RULEHUB_DEVDOCS"])
            os.environ.update(env)
            self.addCleanup(lambda: [os.environ.pop(k, None) for k in env])
            for name in ("A.java", "B.java"):
                task.gate_decision(os.path.join(proj, "src/main/java", name), "s")
            payload = lambda n: {"hook_event_name": "PreToolUse", "session_id": "s", "tool_input": {"file_path": os.path.join(proj, "src/main/java", n)}}
            self.assertIn("deny", self.call("stage_gate.py", payload("C.java"), None, env))
            self.assertEqual(self.call("stage_gate.py", payload("C.java"), self.cfg("java-12-pipeline"), env), "")
            self.assertTrue(self.logged()[-1]["trial"])


class ExperimentCommandTests(Base):
    def setUp(self):
        super().setUp()
        self.cfg = os.path.join(self.tmp.name, "config.json")
        self.log = os.path.join(self.tmp.name, "v.jsonl")
        os.environ.update(RULEHUB_CONFIG=self.cfg, RULEHUB_VIOLATIONS=self.log)
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in ("RULEHUB_CONFIG", "RULEHUB_VIOLATIONS")])

    def exp(self, *args):
        return run("--source", self.src, "experiment", *args)

    def config(self):
        with open(self.cfg, encoding="utf-8") as f:
            return json.load(f)

    def test_start_and_stop_change_only_the_trial_list_and_keep_other_config(self):
        write(self.cfg, json.dumps({"hooks": {"rule_router": False}}))
        self.assertEqual(self.exp("start", "java-scoped")[0], 0)
        data = self.config()
        self.assertIn("java-scoped", data["experimental"])
        self.assertEqual(data["hooks"], {"rule_router": False})
        self.assertEqual(self.exp("stop", "java-scoped")[0], 0)
        self.assertEqual(self.config()["experimental"], {})

    def test_start_rejects_unknown_duplicate_and_missing_rule(self):
        self.assertIn("没有这条规则", self.exp("start", "nope")[2])
        self.assertEqual(self.exp("start", "core-always")[0], 0)
        self.assertIn("已在试运行", self.exp("start", "core-always")[2])
        self.assertIn("不在试运行", self.exp("stop", "on-demand")[2])
        self.assertIn("需要指定规则", self.exp("start")[2])

    def test_start_works_when_config_file_does_not_exist_yet(self):
        self.assertFalse(os.path.exists(self.cfg))
        self.assertEqual(self.exp("start", "core-always")[0], 0)
        self.assertTrue(os.path.exists(self.cfg))

    def test_verdict_validates_and_is_recorded_without_counting_as_a_trigger(self):
        self.assertEqual(self.exp("verdict", "core-always", "rejected", "--note", "举例不是声称")[0], 0)
        self.assertIn("没有这条规则", self.exp("verdict", "nope", "upheld")[2])
        code, out, _ = self.exp("report")
        self.assertEqual(code, 0)
        self.assertIn("触发 0  采纳 0  驳回 1", out)

    def test_report_counts_precision_and_gives_advice_only_for_trial_rules(self):
        self.exp("start", "core-always")
        start = self.config()["experimental"]["core-always"]
        rows = [row("core-always", t=start - 100)] * 5 + [row("core-always", t=start + 1)] * 10
        rows += [row("core-always", "verdict", start + 2, "upheld")] * 5 + [row("core-always", "verdict", start + 2, "rejected: x")]
        rows += [row("java-scoped")] * 3 + [row("java-scoped", "verdict", detail="rejected")] * 5
        write(self.log, "".join(json.dumps(r) + "\n" for r in rows))
        code, out, _ = self.exp("report")
        self.assertEqual(code, 0)
        self.assertIn("试运行  core-always  触发 10  采纳 5  驳回 1  待裁决 4  精度 83%  → 可转正", out)
        line = next(l for l in out.splitlines() if "java-scoped" in l)
        self.assertIn("正式", line)
        self.assertNotIn("→", line)

    def test_report_with_nothing_recorded(self):
        self.assertIn("还没有记录", self.exp("report")[1])


class LoopTests(HookRunner, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stamp = os.path.join(self.tmp.name, "stamp")

    def rows_for(self, rule, upheld, rejected, triggers):
        t = time.time()
        return ([row(rule, t=t + 1)] * triggers + [row(rule, "verdict", t + 2, "upheld")] * upheld
                + [row(rule, "verdict", t + 2, "rejected")] * rejected), {rule: t}

    def test_notice_fires_only_when_data_is_decisive_and_at_most_once_a_day(self):
        rows, trials = self.rows_for("r", 5, 0, 10)
        text = experiment.notice(rows, trials, self.stamp)
        self.assertIn("r：可转正", text)
        self.assertIn("请向用户说明", text)
        self.assertEqual(experiment.notice(rows, trials, self.stamp), "")
        later = time.time() + experiment.NOTICE_INTERVAL + 1
        self.assertIn("可转正", experiment.notice(rows, trials, self.stamp, now=later))

    def test_notice_says_retire_for_low_precision_and_stays_silent_when_undecided(self):
        rows, trials = self.rows_for("r", 1, 4, 12)
        self.assertIn("r：建议下线或改写", experiment.notice(rows, trials, self.stamp))
        os.remove(self.stamp)
        rows, trials = self.rows_for("r", 1, 1, 12)
        self.assertEqual(experiment.notice(rows, trials, self.stamp), "")
        self.assertFalse(os.path.exists(self.stamp))

    def test_notice_never_reports_formal_rules_or_breaks_on_bad_stamp(self):
        rows, _ = self.rows_for("r", 5, 0, 10)
        self.assertEqual(experiment.notice(rows, {}, self.stamp), "")
        write(self.stamp, "不是数字")
        rows, trials = self.rows_for("r", 5, 0, 10)
        self.assertIn("可转正", experiment.notice(rows, trials, self.stamp))

    def test_router_adds_the_notice_for_a_decisive_trial_rule(self):
        rows, trials = self.rows_for("java-05-code-style", 5, 0, 10)
        log = os.path.join(self.tmp.name, "v.jsonl")
        write(log, "".join(json.dumps(r) + "\n" for r in rows))
        cfg = os.path.join(self.tmp.name, "c.json")
        write(cfg, json.dumps({"experimental": trials}))
        env = {"RULEHUB_VIOLATIONS": log, "RULEHUB_CACHE": self.tmp.name}
        first = self.call("rule_router.py", {"prompt": "你好"}, cfg, env)
        self.assertIn("规则试运行", first)
        self.assertIn("java-05-code-style：可转正", first)
        self.assertNotIn("规则试运行", self.call("rule_router.py", {"prompt": "你好"}, cfg, env))


class ExperimentLogicTests(unittest.TestCase):
    def stat(self, triggers, upheld, rejected, trial=True):
        s = {"triggers": triggers, "upheld": upheld, "rejected": rejected, "trial": trial}
        s["precision"] = upheld / (upheld + rejected) if upheld + rejected else None
        return s

    def test_advice_thresholds(self):
        a = experiment.advice
        self.assertEqual(a(self.stat(50, 0, 0)), "继续观察")
        self.assertEqual(a(self.stat(50, 2, 2)), "继续观察")
        self.assertEqual(a(self.stat(9, 5, 0)), "继续观察")
        self.assertEqual(a(self.stat(10, 5, 0)), "可转正")
        self.assertEqual(a(self.stat(10, 4, 1)), "可转正")
        self.assertEqual(a(self.stat(10, 3, 2)), "继续观察")
        self.assertEqual(a(self.stat(10, 2, 3)), "建议下线或改写")
        self.assertEqual(a(self.stat(50, 5, 0, trial=False)), "")

    def test_malformed_verdict_detail_is_ignored(self):
        now = time.time()
        stats = experiment.report([row("r", "verdict", now, "乱写"), row("r", "verdict", now, "")], {})
        self.assertEqual((stats["r"]["upheld"], stats["r"]["rejected"], stats["r"]["triggers"]), (0, 0, 0))

    def test_trial_rule_without_records_still_appears(self):
        stats = experiment.report([], {"r": time.time()})
        self.assertEqual(stats["r"]["advice"], "继续观察")


if __name__ == "__main__":
    unittest.main()
