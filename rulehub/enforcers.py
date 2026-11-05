"""规则执行者登记 能用机器检查的规则必须有执行者 做不到的要登记原因"""
import json
import os

FILE = "enforcers.json"


def load(src):
    try:
        with open(os.path.join(src, FILE), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def report(rules, registry):
    """返回 (机器执行, 靠自觉, 未登记, 已知缺口, 登记了但规则已不存在)"""
    auto, manual, missing, gaps = {}, {}, [], {}
    for r in rules:
        entry = registry.get(r.id) or {}
        if entry.get("hooks"):
            auto[r.id] = entry
        elif entry.get("manual"):
            manual[r.id] = entry["manual"]
        else:
            missing.append(r.id)
        if entry.get("gap"):
            gaps[r.id] = entry["gap"]
    stale = sorted(set(registry) - {r.id for r in rules})
    return auto, manual, missing, gaps, stale
