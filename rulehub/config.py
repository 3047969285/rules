"""配置读取 钩子与命令行共用 文件 ~/.config/rulehub/config.json"""
import json
import os
import time

DEFAULT_REVIEWERS = {"claude": "haiku", "codex": "gpt-6-luna", "cursor": "composer-2.5"}


def config():
    path = os.environ.get("RULEHUB_CONFIG") or os.path.expanduser("~/.config/rulehub/config.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def enabled(name):
    """复核员自己的子进程里所有钩子一律关闭 避免递归"""
    if os.environ.get("RULEHUB_REVIEW"):
        return False
    hooks = config().get("hooks", {})
    return not (isinstance(hooks, dict) and hooks.get(name) is False)


def in_scope(path, key="java_scope"):
    """配置了范围就只管范围内的路径 没配置则不限制"""
    roots = config().get(key)
    if not roots:
        return True
    real = os.path.realpath(path)
    return any(real == r or real.startswith(r + os.sep) for r in (os.path.realpath(os.path.expanduser(x)) for x in roots))


def reviewers():
    custom = config().get("reviewers")
    return {**DEFAULT_REVIEWERS, **(custom if isinstance(custom, dict) else {})}


def new_text(tool_input):
    """本次 Write Edit MultiEdit 将要写入的新内容"""
    if "content" in tool_input:
        return tool_input["content"]
    if "new_string" in tool_input:
        return tool_input["new_string"]
    return "\n".join(e.get("new_string", "") for e in tool_input.get("edits", []))


def violations_path():
    return os.environ.get("RULEHUB_VIOLATIONS") or os.path.expanduser("~/.local/share/rulehub/violations.jsonl")


def log_violation(hook, rule, detail=""):
    """记录一次违规或拦截 detail 必须已脱敏"""
    try:
        path = violations_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "hook": hook, "rule": rule, "detail": detail[:200], "cwd": os.getcwd()}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_violations(days=None):
    try:
        with open(violations_path(), encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
    except (OSError, ValueError):
        return []
    cutoff = time.time() - days * 86400 if days else 0
    return [r for r in rows if r.get("t", 0) >= cutoff]
