#!/usr/bin/env python3
"""密钥守卫 PreToolUse 写入前扫描凭证 格式确定的令牌与私钥直接拒绝 疑似口令与带密码的连接串询问"""
import json
import sys

from _cfg import enabled, log_violation, new_text, trial
from _secrets import ALLOW, CONFIG_FILE, scan


def decide(decision, reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                              "permissionDecisionReason": reason}}, ensure_ascii=False))


def kinds(hits):
    return "; ".join(h.split(": ", 1)[1].split(" (")[0] for h in hits[:3])


def main():
    if not enabled("secret_guard"):
        return
    data = json.load(sys.stdin)
    if data.get("hook_event_name") != "PreToolUse":
        return
    tool_input = data.get("tool_input") or {}
    hard, soft = scan(new_text(tool_input), bool(CONFIG_FILE.search(tool_input.get("file_path", "").replace("\\", "/"))))
    tip = f"\n规则 security-confidentiality：凭证不得明文写入文件。改用环境变量或配置占位；确属示例可在该行加注释 {ALLOW}"
    if hard:
        log_violation("secret_guard", "security-confidentiality", "deny " + kinds(hard))
        if not trial("security-confidentiality"):
            decide("deny", "检测到凭证，已拒绝写入（行号相对本次写入的内容）:\n" + "\n".join(hard[:10]) + tip)
    elif soft:
        log_violation("secret_guard", "security-confidentiality", "ask " + kinds(soft))
        if not trial("security-confidentiality"):
            decide("ask", "疑似凭证，请确认（行号相对本次写入的内容）:\n" + "\n".join(soft[:10]) + tip)


main()
