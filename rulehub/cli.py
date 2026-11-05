"""命令行入口"""
import argparse
import json
import os
import sys

from . import targets as T
from . import history, launchd, status
from .importer import ImportFailed, import_file
from .model import duplicate_lines, fix_source, load_rules

CANDIDATES = [
    "~/Library/Mobile Documents/com~apple~CloudDocs/Documents/规则体系",
    "~/Documents/规则体系",
    "~/Desktop/规则体系",
]
ALWAYS_BUDGET = 40960


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
        raise Fatal(f"读取规则源失败(iCloud 文件未下载?): {e}")
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
    for t in args.target or ["claude", "codex"]:
        if t == "claude":
            out.append((os.path.join(home, ".claude"), T.render_claude(rules)))
        elif t == "codex":
            out.append((os.path.join(home, ".codex"), T.render_codex(rules)))
            block = (os.path.join(home, ".codex", "AGENTS.md"), T.agents_block(rules))
        elif t == "cursor":
            out.append((os.path.join(home, ".cursor"), T.render_paste(rules)))
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
        print(f"提示: 常驻规则正文已达 {always_bytes(rules)} 字节 过长会稀释遵守率", file=sys.stderr)
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
    rs = sub.add_parser("restore")
    rs.add_argument("snapshot")
    rs.add_argument("--yes", action="store_true")
    rs.set_defaults(fn=cmd_restore)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
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
    except (Fatal, T.GuardError, ImportFailed) as e:
        if args.cmd == "sync" and not args.dry_run:
            status.record(args.home, False, str(e).splitlines()[0])
        if getattr(args, "hook", False):
            print(json.dumps({"systemMessage": f"规则同步失败 已有规则保持不变: {e}"}, ensure_ascii=False))
            return 0
        print(f"错误: {e}", file=sys.stderr)
        return 2
