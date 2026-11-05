"""一键体检 返回 [(级别, 说明)] 级别 ok warn fail info"""
import json
import os
import shutil
import sys
import time

from . import enforcers, status, task
from . import targets as T
from .cli import ALWAYS_BUDGET, Fatal, always_bytes, plan, resolve_source
from .model import load_rules


def check_source(args):
    try:
        src = resolve_source(args.source)
    except Fatal as e:
        return None, None, [("fail", str(e))]
    out = [("ok", f"规则源 {src}")]
    placeholders = [f for _, _, fs in os.walk(src) for f in fs if f.endswith(".icloud")]
    if placeholders:
        out.append(("fail", f"{len(placeholders)} 个文件还是 iCloud 云端占位 没有下载到本机"))
    try:
        rules, issues = load_rules(src)
    except OSError as e:
        return src, None, out + [("fail", f"读取规则源失败: {e}")]
    errors = [i for i in issues if i[0] == "error"]
    out.append(("fail", f"规则源有 {len(errors)} 个错误 运行 lint 查看") if errors else ("ok", f"{len(rules)} 条规则 无错误"))
    warns = len(issues) - len(errors)
    if warns:
        out.append(("warn", f"规则源有 {warns} 个告警 运行 lint 查看"))
    return src, rules, out


def check_targets(args, rules, src):
    out = []
    roots, block = plan(args, rules)
    for root, files in roots:
        if os.path.islink(root) and not os.path.exists(root):
            out.append(("fail", f"{root} 是悬空链接 固态未挂载?"))
        elif not os.access(root if os.path.exists(root) else os.path.dirname(root), os.W_OK):
            out.append(("fail", f"{root} 不可写"))
        else:
            bad = T.drift(root, files)
            out.append(("warn", f"{root} 有 {len(bad)} 处与规则源不一致 运行 sync") if bad else ("ok", f"{root} 与规则源一致"))
    if block:
        size = os.path.getsize(block[0]) if os.path.exists(block[0]) else 0
        out.append(("warn" if size > T.AGENTS_LIMIT * 0.8 else "ok", f"Codex AGENTS.md {size} 字节 上限 {T.AGENTS_LIMIT}"))
    _, _, missing, gaps, stale = enforcers.report(rules, enforcers.load(src))
    if missing or stale:
        out.append(("warn", f"{len(missing)} 条规则没登记执行者 {len(stale)} 条登记已过期 运行 enforcement 查看"))
    else:
        out.append(("ok", "每条规则都登记了执行者或原因" + (f" 另有 {len(gaps)} 个能机检但未做的缺口" if gaps else "")))
    total = always_bytes(rules)
    out.append(("fail" if total > ALWAYS_BUDGET else "ok", f"常驻规则正文 {total} 字节 硬上限 {ALWAYS_BUDGET}"))
    return out


def check_environment(args):
    home = os.path.expanduser(args.home)
    out = [("ok" if sys.version_info >= (3, 9) else "fail", f"Python {sys.version.split()[0]}")]
    st = status.last(args.home)
    if st is None:
        out.append(("warn", "还没有同步记录"))
    elif not st["ok"]:
        out.append(("fail", f"上次同步失败: {st['message']}"))
    else:
        days = (time.time() - st["time"]) / 86400
        out.append(("warn" if days > 7 else "ok", f"上次同步成功 {days:.1f} 天前"))
    try:
        with open(os.path.join(home, ".claude", "plugins", "installed_plugins.json"), encoding="utf-8") as f:
            installed = "rule-hub@rule-hub" in json.load(f).get("plugins", {})
    except (OSError, ValueError):
        installed = False
    out.append(("ok" if installed else "warn", "Claude Code 插件已安装" if installed else "Claude Code 插件未安装 会话启动不会自动同步"))
    try:
        with open(os.path.join(home, ".claude", "settings.json"), encoding="utf-8") as f:
            if "rule_router" in json.dumps(json.load(f).get("hooks", {})):
                out.append(("warn", "settings.json 里还有旧的手写 hook 会和插件重复触发"))
    except (OSError, ValueError):
        pass
    docs = task.devdocs_root()
    out.append(("ok", f"固态文档目录可用 {docs}") if task.root_available() else ("warn", f"固态文档目录不可用 {docs} 后端开发流水线的阶段闸门会放行"))
    plist = os.path.join(home, "Library", "LaunchAgents", "com.rulehub.sync.plist")
    out.append(("ok", "自动监听已安装") if os.path.exists(plist) else ("info", "自动监听未安装 规则源变更要等会话结束时才同步(规则源在 iCloud 时后台任务读不到 不建议装)"))
    for tool in ("codex", "cursor-agent"):
        if not shutil.which(tool):
            out.append(("info", f"本机没有 {tool} 命令行 只能验证生成结构 不能验证它实际读取"))
    return out


def run(args):
    src, rules, out = check_source(args)
    if rules:
        out += check_targets(args, rules, src)
    return out + check_environment(args)
