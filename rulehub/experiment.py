"""规则试运行与度量 试运行中的规则只记日志不拦截 裁决记录用来算精度"""
import json
import os
import tempfile
import time

from .config import config, config_path, log_violation, trial_rules

MIN_TRIGGERS = 10
MIN_VERDICTS = 5
PROMOTE_PRECISION = 0.8
RETIRE_PRECISION = 0.5
NOTICE_INTERVAL = 86400
OUTCOMES = {"upheld": "采纳", "rejected": "驳回"}
JUDGED_HOOKS = ("rule_review", "evidence_gate")
VERDICT_HINT = '裁决后逐条记录: rulehub experiment verdict <规则id> upheld|rejected --note "一句话理由"（属实 upheld，不属实 rejected）'


class ExperimentError(Exception):
    pass


def save_trials(trials):
    path = config_path()
    data = config()
    data["experimental"] = trials
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def start(rule, known):
    if rule not in known:
        raise ExperimentError(f"没有这条规则: {rule}")
    trials = trial_rules()
    if rule in trials:
        raise ExperimentError(f"{rule} 已在试运行")
    save_trials({**trials, rule: time.time()})


def stop(rule):
    trials = trial_rules()
    if rule not in trials:
        raise ExperimentError(f"{rule} 不在试运行")
    save_trials({k: v for k, v in trials.items() if k != rule})


def verdict(rule, outcome, note, known):
    if rule not in known:
        raise ExperimentError(f"没有这条规则: {rule}")
    if outcome not in OUTCOMES:
        raise ExperimentError("结果只能是 upheld（复核意见属实）或 rejected（不属实）")
    log_violation("verdict", rule, outcome + (": " + note if note else ""))


def advice(stat):
    """只对试运行规则给建议 裁决数不够时一律继续观察"""
    if not stat["trial"]:
        return ""
    if stat["upheld"] + stat["rejected"] < MIN_VERDICTS:
        return "继续观察"
    if stat["precision"] < RETIRE_PRECISION:
        return "建议下线或改写"
    if stat["triggers"] >= MIN_TRIGGERS and stat["precision"] >= PROMOTE_PRECISION:
        return "可转正"
    return "继续观察"


def notice(rows, trials, stamp_path, now=None):
    """会话开始时的提示：试运行规则数据够了就催你决定 每天最多一次 没有可决定的不打扰"""
    now = time.time() if now is None else now
    try:
        with open(stamp_path, encoding="utf-8") as f:
            if now - float(f.read().strip()) < NOTICE_INTERVAL:
                return ""
    except (OSError, ValueError):
        pass
    due = [(rule, s) for rule, s in sorted(report(rows, trials).items()) if s["advice"] in ("可转正", "建议下线或改写")]
    if not due:
        return ""
    try:
        os.makedirs(os.path.dirname(stamp_path), exist_ok=True)
        with open(stamp_path, "w", encoding="utf-8") as f:
            f.write(str(now))
    except OSError:
        pass
    lines = [f"{rule}：{s['advice']}（触发 {s['triggers']}，采纳 {s['upheld']}，驳回 {s['rejected']}）" for rule, s in due]
    return "\n【规则试运行】数据够了，请向用户说明并请其决定（可转正用 rulehub experiment stop，下线或改写需用户定）:\n" + "\n".join(lines)


def report(rows, trials):
    """按规则汇总触发次数和裁决 试运行规则只统计开始之后的记录"""
    stats = {}
    for r in rows:
        rule = r.get("rule")
        if rule in trials and r.get("t", 0) < trials[rule]:
            continue
        s = stats.setdefault(rule, {"triggers": 0, "upheld": 0, "rejected": 0, "asked": 0})
        if r.get("hook") == "verdict":
            kind = str(r.get("detail", "")).split(":", 1)[0]
            if kind in OUTCOMES:
                s[kind] += 1
        else:
            s["triggers"] += 1
            s["asked"] += r.get("hook") in JUDGED_HOOKS
    for rule in trials:
        stats.setdefault(rule, {"triggers": 0, "upheld": 0, "rejected": 0, "asked": 0})
    for rule, s in stats.items():
        judged = s["upheld"] + s["rejected"]
        s["pending"] = max(0, s["asked"] - judged)
        s["precision"] = s["upheld"] / judged if judged else None
        s["trial"] = rule in trials
        s["advice"] = advice(s)
    return stats
