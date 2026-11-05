"""调用最便宜的模型做只读复核 结束钩子与流水线阶段评审共用"""
import json
import os
import re
import subprocess


last_error = ""


def ask_reviewer(prompt, model, timeout=90):
    """返回模型原始输出 失败返回空串并把原因记在 last_error 子进程里所有钩子关闭 避免递归"""
    global last_error
    last_error = ""
    stub = os.environ.get("RULEHUB_REVIEWER_CMD")
    cmd = [stub] if stub else ["claude", "-p", prompt, "--model", model, "--tools", "", "--no-session-persistence",
                               "--disable-slash-commands", "--setting-sources", "", "--output-format", "text"]
    try:
        r = subprocess.run(cmd, input=prompt if stub else None, capture_output=True, text=True, timeout=timeout,
                           env=dict(os.environ, RULEHUB_REVIEW="1"))
        last_error = (r.stderr or "").strip()[-300:] if r.returncode else ""
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError) as e:
        last_error = f"{type(e).__name__}: {e}"[-300:]
        return ""


def extract_json(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
