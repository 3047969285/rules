#!/usr/bin/env python3
"""Stop 证据闸门与独立复核 先确定性检查声称有没有证据 再让最便宜的模型对照规则复核 每次结束最多拦一次"""
import json
import os
import re
import sys

from _cfg import enabled, log_violation, reviewers
from _secrets import redact
from rulehub.review_run import ask_reviewer, extract_json
from rulehub.reviewer import IDS, instructions

CLAIM = re.compile(r"已实测|实测(?:通过|过|结果|确认|有效)|经实测|验证通过|已验证|测试(?:全部)?通过|全部通过|全过|都通过|编译通过|已拦截|都被拦|已生效|运行正常|已跑通|跑通了|已修复|已完成|搞定了?")
NEGATION = re.compile(r"[未没不无]")
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
CODE_EXT = (".py", ".java", ".js", ".ts", ".tsx", ".jsx", ".json", ".toml", ".yml", ".yaml", ".xml", ".sh", ".mdc",
            ".html", ".css", ".swift", ".kt", ".go", ".rs", ".c", ".cpp", ".h", ".sql", ".gradle")
TEST_CMD = re.compile(r"unittest|pytest|py\.test|\bmvnw?\b.*\b(?:test|verify|install|package)\b|\bgradlew?\b.*\btest\b|"
                      r"\bnpm (?:run )?test|\byarn test|\bpnpm test|go test|cargo test|swift test|xcodebuild.*\btest\b")
KEY_LINE = re.compile(r"Ran \d+ tests?|^OK\b|FAILED|passed|failed|Error|Traceback|✔|✘|错误|失败")
SYNTHETIC = ("Stop hook feedback", "<system-reminder>", "[Request interrupted")


def text_of(content):
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def is_real_user(entry):
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSynthetic"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return False
    text = text_of(content)
    return bool(text.strip()) and not text.startswith(SYNTHETIC)


def parse_turn(path):
    """返回 (用户原话, 本轮工具调用列表, 工具结果表, 最终回答)"""
    entries = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except ValueError:
                pass
    start = max((i for i, e in enumerate(entries) if is_real_user(e)), default=-1)
    prompt = text_of(entries[start]["message"]["content"]) if start >= 0 else ""
    uses, results, final = [], {}, ""
    for e in entries[start + 1:]:
        content = (e.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        if e.get("type") == "assistant":
            uses += [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
            text = text_of(content)
            final = text if text.strip() else final
        else:
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    results[b.get("tool_use_id")] = text_of(b.get("content"))
    return prompt, uses, results, final


def code_edits(uses):
    return [(i, u) for i, u in enumerate(uses)
            if u.get("name") in EDIT_TOOLS and str((u.get("input") or {}).get("file_path", "")).endswith(CODE_EXT)]


def project_has_tests(path):
    d, home = os.path.dirname(os.path.abspath(path)), os.path.expanduser("~")
    for _ in range(8):
        if d in (home, os.path.dirname(d)):
            return False
        if any(os.path.isdir(os.path.join(d, t)) for t in ("tests", "test", "__tests__")):
            return True
        d = os.path.dirname(d)
    return False


def find_claim(text):
    """第一个肯定的完成或验证声称 前两个字里带 未 没 不 无 的当作否定 不算"""
    return next((m for m in CLAIM.finditer(text) if not NEGATION.search(text[max(0, m.start() - 2):m.start()])), None)


def evidence_problem(uses, final):
    """返回 (规则, 说明) 或 None"""
    claim = find_claim(final)
    if not claim:
        return None
    if not uses:
        return "code-first-facts", f"你的回答声称了验证或完成（命中「{claim.group(0)}」），但本轮没有调用任何工具，没有证据支撑。请先实际运行验证，或把表述改成「未验证」。"
    edits = code_edits(uses)
    if not edits:
        return None
    last = edits[-1][0]
    after = [u for u in uses[last + 1:] if u.get("name") == "Bash"]
    path = str(edits[-1][1]["input"].get("file_path", ""))
    if not after:
        return "code-first-facts", f"你修改了代码文件（{os.path.basename(path)}）并声称「{claim.group(0)}」，但最后一次修改之后没有运行任何命令验证。请先验证，或改成「未验证」。"
    if project_has_tests(path) and not any(TEST_CMD.search(str((u.get("input") or {}).get("command", ""))) for u in after):
        return "craft-excellence", f"{os.path.basename(path)} 所在项目有测试，但最后一次修改之后没有运行测试命令。仓库已有测试必须跑全套，请先运行再声称「{claim.group(0)}」。"
    return None


def clip(text, limit):
    """超长时保留头尾各一半 中间的省略要明说"""
    return text if len(text) <= limit else text[:limit // 2] + " …（中间省略）… " + text[-(limit // 2):]


def packet(prompt, uses, results, final):
    ops = []
    for u in uses[-15:]:
        inp = u.get("input") or {}
        if u.get("name") == "Bash":
            cmd, out = str(inp.get("command", "")), results.get(u.get("id"), "")
            test = TEST_CMD.search(cmd)
            key = " | ".join(l.strip() for l in out.splitlines() if KEY_LINE.search(l))[:300]
            ops.append(f"Bash: {clip(cmd, 400)} [含测试命令: {test.group(0) if test else '无'}]\n  输出: {clip(out, 1600)}"
                       + (f"\n  关键行: {key}" if key and len(out) > 1600 else ""))
        elif u.get("name") in EDIT_TOOLS:
            ops.append(f"{u['name']} {inp.get('file_path', '')}")
        else:
            ops.append(f"{u.get('name')} {str(inp)[:120]}")
    text = f"用户原话:\n{prompt[:1500]}\n\n本轮操作:\n" + ("\n".join(ops) or "(无)") + f"\n\n准备给用户的回答:\n{final[:2500]}"
    return redact(text)[:12000]


def review(prompt, uses, results, final):
    model = reviewers()["claude"]
    data = extract_json(ask_reviewer(instructions() + "\n\n=== 待复核内容 ===\n" + packet(prompt, uses, results, final), model))
    items = (data or {}).get("violations", [])
    return model, [(v["rule"], str(v.get("evidence", ""))[:200]) for v in items
                   if isinstance(v, dict) and v.get("rule") in IDS and str(v.get("evidence", "")).strip()][:5]


def block(reason):
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


def main():
    if not enabled("evidence_gate"):
        return
    data = json.load(sys.stdin)
    if data.get("stop_hook_active") or not data.get("transcript_path"):
        return
    prompt, uses, results, final = parse_turn(data["transcript_path"])
    if not final:
        return
    problem = evidence_problem(uses, final)
    if problem:
        log_violation("evidence_gate", problem[0], problem[1][:120])
        block("证据闸门（规则 " + problem[0] + "）：" + problem[1])
        return
    if not enabled("rule_review") or not (uses or find_claim(final)):
        return
    model, findings = review(prompt, uses, results, final)
    if findings:
        for rule, evidence in findings:
            log_violation("rule_review", rule, evidence)
        block(f"独立复核（{model}，只读）对照你的规则发现疑似违规。请你作为裁决者逐条判断：属实就先改正再结束；不属实用一句话说明理由后结束。\n"
              + "\n".join(f"- [{rule}] {evidence}" for rule, evidence in findings))


main()
