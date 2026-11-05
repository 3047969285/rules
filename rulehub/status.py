"""最近一次同步的结果 供路由钩子和 doctor 读取"""
import json
import os
import time


def path(home):
    return os.environ.get("RULEHUB_STATE") or os.path.join(os.path.expanduser(home), ".config", "rulehub", "state.json")


def record(home, ok, message=""):
    p = path(home)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"time": time.time(), "ok": ok, "message": message}, f, ensure_ascii=False)
    except OSError:
        pass


def last(home):
    try:
        with open(path(home), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
