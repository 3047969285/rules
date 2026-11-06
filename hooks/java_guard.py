#!/usr/bin/env python3
"""Java 规则体系守卫 PreToolUse 写入前拦截违规内容与测试 yml 文件 PostToolUse 写入后做整文件扫描"""
import json
import os
import re
import sys

from _cfg import enabled, in_scope, log_violation, new_text, trial
from rulehub.task import tests_allowed

PUNCT = re.compile(r"[，。、；：！？（）《》“”‘’,.;:!?()\[\]{}\"'`*\-]")
INNER = re.compile(r"^\s{4,}(?:(?:public|private|protected|static|final|abstract)\s+)*(?:class|enum|interface|record)\s+\w+")
METHOD = re.compile(r"^ {4}(?:public|protected|private)\s+(?:static\s+)?(?:final\s+)?[\w<>\[\],?\s]+\s+\w+\s*\([^;]*$|^ {4}(?:public|protected|private)\s+\w+\s*\(")
IFACE_METHOD = re.compile(r"^ {4}(?=[\w<])(?!return |throw |new )[\w<>\[\],?\s]+\s+\w+\s*\([^)]*\)\s*;$")

JAVA_RULES = [
    (re.compile(r"^\s{8,}.*Pattern\.compile\("), "方法体内禁止 Pattern.compile，改类级 static final"),
    (re.compile(r"@(?:Select|Insert|Update|Delete)\b"), "Mapper 禁止注解 SQL，写进 XML"),
    (re.compile(r"throw new (?:RuntimeException|Exception|Throwable)\("), "禁止直接抛 RuntimeException/Exception，用业务异常"),
    (re.compile(r"new Thread\(|Executors\."), "禁止 new Thread / Executors，用 ThreadPoolExecutor"),
    (re.compile(r"log\.\w+\(\s*\"[^\"]*\"\s*\+"), "日志禁止字符串拼接，用 {} 占位符"),
    (re.compile(r"catch\s*\([^)]*\)\s*\{\s*\}"), "禁止空 catch"),
]
XML_RULES = [
    (re.compile(r"select\s+(?:\w+\.)?\*", re.I), "禁止 SELECT *，须显式列名"),
    (re.compile(r"\$\{"), "禁止 ${} 拼接，改用 #{}"),
    (re.compile(r"where\s+1\s*=\s*1", re.I), "禁止 WHERE 1=1，用 <where>"),
]


RULE_OF = [("Pattern.compile", "java-05-code-style"), ("Thread", "java-05-code-style"), ("空 catch", "java-05-code-style"),
           ("注解 SQL", "java-03-mapper-xml"), ("SELECT", "java-03-mapper-xml"), ("${}", "java-03-mapper-xml"),
           ("WHERE 1=1", "java-03-mapper-xml"), ("RuntimeException", "java-06-error-log-security"),
           ("日志禁止", "java-06-error-log-security"), ("内部类", "java-01-arch"), ("JavaDoc", "java-07-javadoc")]


def rule_of(msg):
    text = msg.split(": ", 1)[-1]
    return next((r for k, r in RULE_OF if k in text), "java-05-code-style")


def log_issues(hook, issues):
    for msg in issues[:10]:
        log_violation(hook, rule_of(msg), msg.split(": ", 1)[-1])


def project_root(path):
    d = os.path.dirname(os.path.abspath(path))
    while d != os.path.dirname(d):
        if os.path.exists(os.path.join(d, "pom.xml")):
            return d
        d = os.path.dirname(d)
    return None


def is_mapper_xml(path, text):
    return path.endswith(".xml") and ("mapper" in path.lower() or "<mapper" in text)


def line_issues(lines, rules, skip_comments):
    out, in_doc = [], False
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if skip_comments:
            if s.startswith("/*"):
                in_doc = not s.endswith("*/")
                continue
            if in_doc:
                in_doc = "*/" not in s
                continue
            if s.startswith("//"):
                continue
        out += [f"{i}: {msg}" for rx, msg in rules if rx.search(line)]
    return out


def scan_xml(lines):
    return line_issues(lines, XML_RULES, False)


def scan_java(lines):
    out = line_issues(lines, JAVA_RULES, True)
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
    return sorted(out, key=lambda x: int(x.split(":")[0]))


def decide(decision, reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                              "permissionDecisionReason": reason}}, ensure_ascii=False))


def pre(path, tool_input):
    norm = path.replace("\\", "/")
    base = os.path.basename(norm)
    text = new_text(tool_input)
    violations = []
    if path.endswith(".java"):
        violations = line_issues(text.splitlines(), JAVA_RULES, True)
    elif is_mapper_xml(path, text):
        violations = scan_xml(text.splitlines())
    if violations:
        log_issues("java_guard", violations)
        enforced = [v for v in violations if not trial(rule_of(v))]
        if enforced:
            decide("deny", "写入前检查未通过（行号相对本次写入的内容）:\n" + "\n".join(enforced[:10]) + "\n请按规则改正后重新写入")
            return
    reasons = []
    if ("/src/test/" in norm or re.search(r"(Test|Tests|IT)\.java$", base)) and not tests_allowed(path):
        log_violation("java_guard", "java-11-artifact-boundary", "写测试文件前询问")
        if not trial("java-11-artifact-boundary"):
            reasons.append("测试文件：规则 java-11 默认禁止，除非用户本轮明确要求写测试")
    if re.search(r"\.ya?ml$", base) and not os.path.exists(path) and project_root(path):
        log_violation("java_guard", "java-10-config-boundary", "新建 yml 前询问")
        if not trial("java-10-config-boundary"):
            reasons.append("新建 yml：规则 java-10 默认禁止新增 yml 文件")
    if reasons:
        decide("ask", "；".join(reasons))


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
        log_issues("java_guard_post", issues)
        issues = [i for i in issues if not trial(rule_of(i))]
    if issues:
        msg = f"Java 规则体系扫描 {os.path.basename(path)} 发现疑似违规（启发式 请核对后修复）:\n" + "\n".join(issues[:20])
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}}, ensure_ascii=False))


def main():
    if not enabled("java_guard"):
        return
    data = json.load(sys.stdin)
    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path", "")
    if not path or not in_scope(path):
        return
    if data.get("hook_event_name") == "PreToolUse":
        pre(path, tool_input)
    elif path.endswith((".java", ".xml")):
        post(path)


main()
