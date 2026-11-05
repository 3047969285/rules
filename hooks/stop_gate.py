#!/usr/bin/env python3
"""Stop 交付闸门 改过构建工程里的 Java 却没在最后一次改动之后执行编译构建时 阻止结束"""
import json
import os
import re
import sys

from _cfg import enabled

MARKERS = ("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle")
BUILD = re.compile(r"\b(?:mvnw?|gradlew?)\b.*\b(?:compile|build|test|package|verify|install)\b")


def in_build_project(path):
    d = os.path.dirname(os.path.abspath(path))
    while d != os.path.dirname(d):
        if any(os.path.exists(os.path.join(d, m)) for m in MARKERS):
            return True
        d = os.path.dirname(d)
    return False


def main():
    if not enabled("stop_gate"):
        return
    data = json.load(sys.stdin)
    if data.get("stop_hook_active") or not data.get("transcript_path"):
        return
    last_edit, last_build, idx = -1, -1, 0
    with open(data["transcript_path"], encoding="utf-8", errors="replace") as f:
        for line in f:
            idx += 1
            try:
                content = (json.loads(line).get("message") or {}).get("content")
            except ValueError:
                continue
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                inp = block.get("input") or {}
                if block.get("name") in ("Edit", "Write", "MultiEdit"):
                    path = str(inp.get("file_path", ""))
                    if path.endswith(".java") and in_build_project(path):
                        last_edit = idx
                elif block.get("name") == "Bash" and BUILD.search(str(inp.get("command", ""))):
                    last_build = idx
    if last_edit > last_build:
        print(json.dumps({"decision": "block", "reason": "规则 java-09/java-11：改过 Maven 或 Gradle 工程里的 Java 后必须执行 mvn compile（或 gradle build）并确认 0 错误才能声称完成。请现在编译，失败就修复；本机无法编译时如实说明原因和命令"}, ensure_ascii=False))


main()
