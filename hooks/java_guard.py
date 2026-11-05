#!/usr/bin/env python3
"""Java 规则体系守卫 PreToolUse 拦测试与 yml 写入 PostToolUse 扫描违规"""
import json
import os
import re
import sys

from _cfg import enabled

PUNCT = re.compile(r"[，。、；：！？（）《》“”‘’,.;:!?()\[\]{}\"'`*\-]")
INNER = re.compile(r"^\s{4,}(?:(?:public|private|protected|static|final|abstract)\s+)*(?:class|enum|interface|record)\s+\w+")
METHOD = re.compile(r"^ {4}(?:public|protected|private)\s+(?:static\s+)?(?:final\s+)?[\w<>\[\],?\s]+\s+\w+\s*\([^;]*$|^ {4}(?:public|protected|private)\s+\w+\s*\(")
IFACE_METHOD = re.compile(r"^ {4}(?=[\w<])(?!return |throw |new )[\w<>\[\],?\s]+\s+\w+\s*\([^)]*\)\s*;$")


def project_root(path):
    d = os.path.dirname(os.path.abspath(path))
    while d != os.path.dirname(d):
        if os.path.exists(os.path.join(d, "pom.xml")):
            return d
        d = os.path.dirname(d)
    return None


def pre(path):
    norm = path.replace("\\", "/")
    base = os.path.basename(norm)
    reasons = []
    if "/src/test/" in norm or re.search(r"(Test|Tests|IT)\.java$", base):
        reasons.append("测试文件：规则 java-11 默认禁止，除非用户本轮明确要求写测试")
    if re.search(r"\.ya?ml$", base) and not os.path.exists(path) and project_root(path):
        reasons.append("新建 yml：规则 java-10 默认禁止新增 yml 文件")
    if reasons:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": "；".join(reasons)}}, ensure_ascii=False))


def scan_xml(lines):
    out = []
    for i, line in enumerate(lines, 1):
        if re.search(r"select\s+(?:\w+\.)?\*", line, re.I):
            out.append(f"{i}: 禁止 SELECT *，须显式列名")
        if "${" in line:
            out.append(f"{i}: 禁止 ${{}} 拼接，改用 #{{}}")
        if re.search(r"where\s+1\s*=\s*1", line, re.I):
            out.append(f"{i}: 禁止 WHERE 1=1，用 <where>")
    return out


def scan_java(lines):
    out = []
    in_doc = False
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if s.startswith("/*"):
            in_doc = not s.endswith("*/")
            continue
        if in_doc:
            in_doc = "*/" not in s
            continue
        if s.startswith("//"):
            continue
        if INNER.match(line):
            out.append(f"{i}: 禁止内部类/枚举/接口/record，须独立顶层文件")
        if re.search(r"^\s{8,}.*Pattern\.compile\(", line):
            out.append(f"{i}: 方法体内禁止 Pattern.compile，改类级 static final")
        if re.search(r"@(?:Select|Insert|Update|Delete)\b", line):
            out.append(f"{i}: Mapper 禁止注解 SQL，写进 XML")
        if re.search(r"throw new (?:RuntimeException|Exception|Throwable)\(", line):
            out.append(f"{i}: 禁止直接抛 RuntimeException/Exception，用业务异常")
        if re.search(r"new Thread\(|Executors\.", line):
            out.append(f"{i}: 禁止 new Thread / Executors，用 ThreadPoolExecutor")
        if re.search(r"log\.\w+\(\s*\"[^\"]*\"\s*\+", line):
            out.append(f"{i}: 日志禁止字符串拼接，用 {{}} 占位符")
        if re.search(r"catch\s*\([^)]*\)\s*\{\s*\}", line):
            out.append(f"{i}: 禁止空 catch")
        if METHOD.match(line) or IFACE_METHOD.match(line):
            j = i - 2
            while j >= 0 and lines[j].strip().startswith("@"):
                j -= 1
            if j < 0 or not lines[j].strip().endswith("*/"):
                out.append(f"{i}: 方法缺 JavaDoc")
            else:
                k = j
                while k >= 0 and not lines[k].strip().startswith("/**"):
                    k -= 1
                first = next((l.strip().lstrip("*").strip() for l in lines[k + 1:j + 1]
                              if l.strip().lstrip("*").strip() and not l.strip().lstrip("*").strip().startswith("@")), "")
                if k >= 0 and PUNCT.search(first):
                    out.append(f"{k + 1}: JavaDoc 首行含标点或符号，须纯中文无标点")
    return out


def post(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    lines = text.splitlines()
    if path.endswith(".java"):
        issues = scan_java(lines)
    else:
        issues = scan_xml(lines) if "<mapper" in text else []
    if issues:
        msg = f"Java 规则体系扫描 {os.path.basename(path)} 发现疑似违规（启发式 请核对后修复）:\n" + "\n".join(issues[:20])
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}}, ensure_ascii=False))


def main():
    if not enabled("java_guard"):
        return
    data = json.load(sys.stdin)
    path = (data.get("tool_input") or {}).get("file_path", "")
    if not path:
        return
    if data.get("hook_event_name") == "PreToolUse":
        pre(path)
    elif path.endswith((".java", ".xml")):
        post(path)


main()
