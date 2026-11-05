#!/usr/bin/env python3
"""PreToolUse 后端开发流水线阶段闸门 没建任务或阶段没过评审时 拒绝写源码与测试 并保护任务状态文件"""
import json
import sys

from _cfg import enabled, log_violation
from rulehub.task import gate_decision


def main():
    if not enabled("stage_gate"):
        return
    data = json.load(sys.stdin)
    if data.get("hook_event_name") != "PreToolUse":
        return
    path = (data.get("tool_input") or {}).get("file_path", "")
    result = gate_decision(path, data.get("session_id", "")) if path else None
    if result:
        log_violation("stage_gate", "java-12-pipeline", result[1].splitlines()[0][:120])
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": result[0],
                                                  "permissionDecisionReason": "后端开发流水线闸门：" + result[1]}}, ensure_ascii=False))


main()
