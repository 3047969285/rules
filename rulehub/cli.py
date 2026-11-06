"""命令行入口"""
import argparse
import json
import os
import sys

from . import targets as T
from . import enforcers, experiment, export, history, launchd, status, task
from .config import read_violations, reviewers, trial_rules
from .importer import ImportFailed, import_file
from .model import duplicate_lines, fix_source, load_rules
from .reviewer import render_agents

CANDIDATES = [
    "~/Library/Mobile Documents/com~apple~CloudDocs/Documents/规则体系",
    "~/Documents/规则体系",
    "~/Desktop/规则体系",
]
ALWAYS_BUDGET = 10240


class Fatal(Exception):
    pass


def resolve_source(arg):
    explicit = arg or os.environ.get("RULEHUB_SOURCE")
    candidates = [explicit] if explicit else CANDIDATES
    for cand in candidates:
        if os.path.isdir(os.path.expanduser(cand)):
            return os.path.expanduser(cand)
    raise Fatal("找不到规则源目录 已有规则保持不变")


def load(args):
    try:
        rules, issues = load_rules(resolve_source(args.source))
    except OSError as e:
        raise Fatal(f"读取规则源失败(无权限或 iCloud 文件未下载; 后台任务读 iCloud 需要完全磁盘访问权限): {e}")
    errors = [i for i in issues if i[0] == "error"]
    if errors:
        raise Fatal("规则源有错误 先修复:\n  " + "\n  ".join(f"{f}: {m}" for _, f, m in errors))
    if not rules:
        raise Fatal("规则源里没有任何规则 已有规则保持不变")
    return rules, issues


def plan(args, rules):
    """每个目标渲染成 [(根目录, 文件表)] 以及 Codex 区块"""
    home = os.path.expanduser(args.home)
    out, block = [], None
    for t in args.target or ["claude", "codex", "cursor"]:
        if t == "claude":
            out.append((os.path.join(home, ".claude"), T.render_claude(rules)))
        elif t == "codex":
            out.append((os.path.join(home, ".codex"), {**T.render_codex(rules), **render_agents("codex", reviewers())}))
            block = (os.path.join(home, ".codex", "AGENTS.md"), T.agents_block(rules))
        elif t == "cursor":
            out.append((os.path.join(home, ".cursor"), {**T.render_paste(rules), **render_agents("cursor", reviewers())}))
            if args.project:
                out.append((os.path.join(os.path.expanduser(args.project), ".cursor"), T.render_cursor(rules)))
    return out, block


def always_bytes(rules):
    return sum(len(r.body.encode("utf-8")) for r in rules if r.kind == "always")


def cmd_sync(args):
    rules, _ = load(args)
    if not args.dry_run:
        try:
            history.snapshot(resolve_source(args.source), args.home)
        except OSError:
            pass
    roots, block = plan(args, rules)
    failed = []
    for root, files in roots:
        try:
            rep = T.apply(root, files, args.dry_run, args.force)
        except OSError as e:
            failed.append(f"{root}: {e.strerror or e}")
            continue
        if not args.quiet:
            print(f"{root}: 写入 {len(rep['written'])} 删除 {len(rep['removed'])} 未变 {rep['unchanged']}")
    if block:
        try:
            changed = T.update_agents(block[0], block[1], args.dry_run, args.force)
            if not args.quiet:
                print(f"{block[0]}: {'已更新' if changed else '未变'}")
        except OSError as e:
            failed.append(f"{block[0]}: {e.strerror or e}")
    if failed:
        raise Fatal("部分目标写入失败(固态未挂载或链接悬空?):\n  " + "\n  ".join(failed))
    if always_bytes(rules) > ALWAYS_BUDGET:
        print(f"警告: 常驻规则正文 {always_bytes(rules)} 字节 超过硬上限 {ALWAYS_BUDGET} 过长会稀释遵守率 check 与 doctor 会失败", file=sys.stderr)
    if not args.dry_run:
        status.record(args.home, True)
    return 0


def cmd_check(args):
    rules, issues = load(args)
    roots, block = plan(args, rules)
    problems = []
    for root, files in roots:
        problems += [f"{root}: {m}" for m in T.drift(root, files)]
        problems += [f"{rel}: {m}" for rel, c in files.items() for m in T.validate(rel, c)]
    if block:
        problems += T.agents_drift(*block)
    problems += [f"{f}: {m}" for lv, f, m in issues if lv == "warn"]
    if always_bytes(rules) > ALWAYS_BUDGET:
        problems.append(f"常驻规则正文 {always_bytes(rules)} 字节 超过硬上限 {ALWAYS_BUDGET} 加一条先删一条")
    for p in problems:
        print(p)
    print("通过" if not problems else f"发现 {len(problems)} 个问题")
    return 1 if problems else 0


def cmd_lint(args):
    src = resolve_source(args.source)
    if args.fix:
        print(f"已规范化 {fix_source(src)} 个文件")
    rules, issues = load_rules(src)
    for lv, f, m in issues:
        print(f"[{lv}] {f}: {m}")
    if args.dups:
        for line, ids in sorted(duplicate_lines(rules).items(), key=lambda kv: -len(kv[1])):
            print(f"[dup] {' / '.join(ids)}: {line[:60]}")
    return 1 if any(lv == "error" for lv, _, _ in issues) else 0


def cmd_list(args):
    rules, _ = load(args)
    for r in rules:
        print(f"{r.kind}\t{r.id}\t{','.join(r.globs)}")
    return 0


def cmd_import(args):
    dest = import_file(resolve_source(args.source), args.file, args.kind, args.globs, args.id,
                       args.description, args.when, args.dir, args.force)
    print(f"已导入 {dest}\n下一步: rulehub sync")
    return 0


def cmd_stats(args):
    rules, _ = load(args)
    for kind in ("always", "scoped", "ondemand"):
        group = sorted((r for r in rules if r.kind == kind), key=lambda r: -len(r.body.encode("utf-8")))
        print(f"== {kind} {len(group)} 条 共 {sum(len(r.body.encode('utf-8')) for r in group)} 字节")
        for r in group:
            print(f"  {len(r.body.encode('utf-8')):>6}  {r.id}")
    return 0


def cmd_violations(args):
    rows = read_violations(args.days)
    if not rows:
        print("还没有违规记录")
        return 0
    try:
        rules, _ = load(args)
        auto, manual, _, _, _ = enforcers.report(rules, enforcers.load(resolve_source(args.source)))
    except Fatal:
        rules, auto, manual = [], {}, {}
    by_rule, by_hook = {}, {}
    for r in rows:
        by_rule[r["rule"]] = by_rule.get(r["rule"], 0) + 1
        by_hook[r["hook"]] = by_hook.get(r["hook"], 0) + 1
    print(f"== 最近 {args.days} 天共 {len(rows)} 次拦截或违规")
    for rule, n in sorted(by_rule.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>4}  {rule}（{'有机器执行者' if rule in auto else '靠自觉'}）")
    print("== 按来源")
    for hook, n in sorted(by_hook.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>4}  {hook}")
    upgrade = [r for r in by_rule if r in manual]
    if upgrade:
        print("== 靠自觉却被闸门或复核抓到的规则（优先升级为机器检查）: " + "、".join(upgrade))
    quiet = [r.id for r in rules if r.id not in by_rule and r.id in auto]
    if quiet:
        print("== 有机器执行者但这段时间从未触发（若长期如此，结合使用频率考虑精简）: " + "、".join(quiet))
    return 0


def cmd_task(args):
    proj = task.project_root(os.path.abspath(args.project))
    if args.action in ("new", "skip") or args.action != "status":
        if not proj:
            raise Fatal(f"{os.path.abspath(args.project)} 不在 Maven 或 Gradle 工程里")
    if args.action == "new":
        if not args.name or not args.level:
            raise Fatal("task new 需要 --name 和 --level（小 或 标准）以及 --reason")
        path = task.new_task(proj, args.name, args.level, args.reason)
        print(f"已建任务 {path}\n产物模板已生成在该目录（固态，不进项目仓库）。下一步: 写 prd.md，然后 rulehub task review prd")
        return 0
    if args.action == "skip":
        print(f"已记录并跳过流水线 {task.skip_task(proj, args.reason)}")
        return 0
    current = task.active_task(proj) if proj and task.root_available() else None
    if args.action == "status":
        print("\n".join(task.status_lines(current)) if current else "没有进行中的任务")
        return 0
    if not current:
        raise Fatal("没有进行中的任务，先 rulehub task new")
    if args.action in ("review", "advance") and not args.stage:
        raise Fatal(f"task {args.action} 需要阶段名: prd、api、tests、impl、final")
    if args.action == "review":
        rec = task.review(current, args.stage)
        print(f"{args.stage} 评审: {rec['verdict']}（{rec['model']}）")
        for f in rec["findings"]:
            print(f"- [{f.get('rule', '')}] {f['evidence']}")
        if rec["verdict"] == "unavailable":
            print(f"复核员没有给出有效结果: {rec.get('error') or '无输出'}\n常见原因: 在沙箱里运行时没有放行到 api.anthropic.com 的网络；或复核命令超时。可重试，或在沙箱外运行这条命令")
        return 0 if rec["verdict"] in ("pass", "skipped") else 1
    if args.action == "advance":
        state = task.advance(current, args.stage)
        print(f"已推进到 {args.stage}" + ("，任务完成" if state["done"] else f"，下一阶段 {task.next_stage(state)}"))
        return 0
    if not args.cmd:
        raise Fatal(f"task {args.action} 需要 --cmd \"<测试命令>\"")
    code, tail = task.record_run(current, args.action, args.cmd, proj)
    print(f"已记录{'红' if args.action == 'red' else '绿'}（退出码 {code}）\n{tail[-600:]}")
    return 0


def cmd_history(args):
    names = history.list_snapshots(args.home)
    print("\n".join(names) if names else "还没有快照")
    return 0


def cmd_restore(args):
    if not args.yes:
        raise Fatal("回滚会覆盖规则源里的 .mdc 文件(会先留保险快照) 确认请加 --yes")
    try:
        count, safety = history.restore(resolve_source(args.source), args.home, args.snapshot)
    except KeyError:
        raise Fatal(f"没有快照 {args.snapshot} 用 history 查看")
    print(f"已恢复 {count} 个文件 回滚前的状态保存在快照 {safety or '(与最近快照相同)'}\n下一步: rulehub sync")
    return 0


def cmd_doctor(args):
    from . import doctor
    out = doctor.run(args)
    for level, text in out:
        print(f"[{level}] {text}")
    return 1 if any(level == "fail" for level, _ in out) else 0


def cmd_launchd(args):
    launcher = os.path.realpath(sys.argv[0])
    watch = resolve_source(args.source)
    if args.action == "print":
        print(launchd.render(launcher, launchd.watch_dirs(watch)))
    elif args.action == "install":
        print(f"已安装 {launchd.install(args.home, launcher, watch)}\n每 10 分钟及规则源目录变化时自动同步 卸载用 launchd uninstall")
    else:
        print(f"已卸载 {launchd.uninstall(args.home)}")
    return 0


def cmd_enforcement(args):
    src = resolve_source(args.source)
    rules, _ = load(args)
    auto, manual, missing, gaps, stale = enforcers.report(rules, enforcers.load(src))
    print(f"== 机器执行 {len(auto)} 条")
    for rid, e in auto.items():
        print(f"  {rid}: " + "；".join(e["hooks"]) + (f"（其余靠自觉: {e['manual']}）" if e.get("manual") else ""))
    print(f"== 靠自觉 {len(manual)} 条（已登记原因）")
    for rid, why in manual.items():
        print(f"  {rid}: {why}")
    print(f"== 未登记 {len(missing)} 条" + ("（先判断能否机检 再登记到 enforcers.json）" if missing else ""))
    for rid in missing:
        print(f"  {rid}")
    if gaps:
        print(f"== 已知缺口 {len(gaps)} 条（能机检但还没做）")
        for rid, why in gaps.items():
            print(f"  {rid}: {why}")
    for rid in stale:
        print(f"[warn] enforcers.json 里的 {rid} 已没有对应规则")
    return 1 if args.strict and (missing or stale) else 0


def cmd_experiment(args):
    if args.action == "report":
        trials = trial_rules()
        stats = experiment.report(read_violations(args.days), trials)
        if not stats:
            print("还没有记录，也没有规则在试运行")
            return 0
        print(f"== 最近 {args.days} 天（试运行规则只统计开始之后）")
        for rule, s in sorted(stats.items(), key=lambda kv: (not kv[1]["trial"], -kv[1]["triggers"])):
            prec = "-" if s["precision"] is None else f"{s['precision']:.0%}"
            state = "试运行" if s["trial"] else "正式"
            print(f"  {state}  {rule}  触发 {s['triggers']}  采纳 {s['upheld']}  驳回 {s['rejected']}  待裁决 {s['pending']}  精度 {prec}"
                  + (f"  → {s['advice']}" if s["advice"] else ""))
        print(f"== 转正条件: 触发 ≥ {experiment.MIN_TRIGGERS} 且裁决 ≥ {experiment.MIN_VERDICTS} 且精度 ≥ {experiment.PROMOTE_PRECISION:.0%}；"
              f"精度 < {experiment.RETIRE_PRECISION:.0%} 建议下线或改写")
        return 0
    if not args.rule:
        raise experiment.ExperimentError("需要指定规则 id")
    known = {r.id for r in load(args)[0]}
    if args.action == "start":
        experiment.start(args.rule, known)
        print(f"{args.rule} 开始试运行：只记日志不拦截。用 rulehub experiment report 查看效果")
    elif args.action == "stop":
        experiment.stop(args.rule)
        print(f"{args.rule} 已转正：恢复拦截")
    else:
        experiment.verdict(args.rule, args.outcome, args.note, known)
        print(f"已记录 {args.rule} 的裁决：{experiment.OUTCOMES[args.outcome]}")
    return 0


def cmd_export(args):
    count = export.export(resolve_source(args.source), os.path.expanduser(args.out))
    print(f"已导出 {count} 个文件到 {args.out}（项目名已通用化，已扫描凭证）")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="rulehub")
    p.add_argument("--source")
    p.add_argument("--home", default="~")
    p.add_argument("--target", action="append", choices=["claude", "codex", "cursor"])
    p.add_argument("--project")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--force", action="store_true")
    s.add_argument("--quiet", action="store_true")
    s.add_argument("--hook", action="store_true", help="失败时以 systemMessage 形式提示 退出码恒为 0")
    s.set_defaults(fn=cmd_sync)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    lt = sub.add_parser("lint")
    lt.add_argument("--dups", action="store_true")
    lt.add_argument("--fix", action="store_true")
    lt.set_defaults(fn=cmd_lint)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    sub.add_parser("history").set_defaults(fn=cmd_history)
    vi = sub.add_parser("violations")
    vi.add_argument("--days", type=int, default=30)
    vi.set_defaults(fn=cmd_violations)
    rs = sub.add_parser("restore")
    rs.add_argument("snapshot")
    rs.add_argument("--yes", action="store_true")
    rs.set_defaults(fn=cmd_restore)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    tk = sub.add_parser("task")
    tk.add_argument("action", choices=["new", "skip", "status", "review", "advance", "red", "green"])
    tk.add_argument("stage", nargs="?")
    tk.add_argument("--project", default=".")
    tk.add_argument("--name")
    tk.add_argument("--level")
    tk.add_argument("--reason", default="")
    tk.add_argument("--cmd")
    tk.set_defaults(fn=cmd_task)
    en = sub.add_parser("enforcement")
    en.add_argument("--strict", action="store_true")
    en.set_defaults(fn=cmd_enforcement)
    ex = sub.add_parser("experiment")
    ex.add_argument("action", choices=["start", "stop", "verdict", "report"])
    ex.add_argument("rule", nargs="?")
    ex.add_argument("outcome", nargs="?", choices=list(experiment.OUTCOMES))
    ex.add_argument("--note", default="")
    ex.add_argument("--days", type=int, default=30)
    ex.set_defaults(fn=cmd_experiment)
    xp = sub.add_parser("export")
    xp.add_argument("out")
    xp.set_defaults(fn=cmd_export)
    ld = sub.add_parser("launchd")
    ld.add_argument("action", choices=["print", "install", "uninstall"])
    ld.set_defaults(fn=cmd_launchd)
    im = sub.add_parser("import")
    im.add_argument("file")
    im.add_argument("--kind", choices=["always", "scoped", "ondemand"])
    im.add_argument("--globs")
    im.add_argument("--id")
    im.add_argument("--description")
    im.add_argument("--when")
    im.add_argument("--dir", default="06-导入")
    im.add_argument("--force", action="store_true")
    im.set_defaults(fn=cmd_import)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except (Fatal, T.GuardError, ImportFailed, task.TaskError, experiment.ExperimentError, export.ExportError) as e:
        if args.cmd == "sync" and not args.dry_run:
            status.record(args.home, False, str(e).splitlines()[0])
        if getattr(args, "hook", False):
            print(json.dumps({"systemMessage": f"规则同步失败 已有规则保持不变: {e}"}, ensure_ascii=False))
            return 0
        print(f"错误: {e}", file=sys.stderr)
        return 2
