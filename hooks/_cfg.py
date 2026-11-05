"""hook 开关 配置文件 ~/.config/rulehub/config.json 形如 {"hooks": {"java_guard": false}}"""
import json
import os


def enabled(name):
    path = os.environ.get("RULEHUB_CONFIG") or os.path.expanduser("~/.config/rulehub/config.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("hooks", {}).get(name, True) is not False
    except (OSError, ValueError, AttributeError):
        return True
