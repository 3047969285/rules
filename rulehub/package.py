"""Generate portable Mac/Windows packages for all supported rule consumers."""
import json
import os
import shutil
import tempfile

from . import targets as T
from .reviewer import render_agents

MARKER = ".rulehub-package.json"
PLATFORMS = ("Mac", "Windows")
TARGETS = (("codex", "codex"), ("claude", "Claude"), ("cursor", "cursor"))


class PackageError(Exception):
    pass


def _root_readme():
    return """# 规则体系安装包\n\n本目录由 rule-hub 从唯一 `.mdc` 规则源生成，按操作系统和工具拆成六个可分发目录：`Mac/`、`Windows/`，各自包含 `codex/`、`Claude/`、`cursor/`。\n\n修改规则请编辑规则源并重新运行 `rulehub package <本目录>`。不要手改本目录；同步时会重新生成受管内容。运行 `rulehub package <本目录> --check` 可检查是否与当前源一致。\n\n每个工具目录内的 `INSTALL.md` 说明目标应用的安装位置。Codex 的 `AGENTS.rulehub.md` 是需合并到现有 `AGENTS.md` 的受管区块；安装时保留区块外的个人指令。Cursor 同时包含项目规则和用户规则粘贴稿。\n\n此包只包含 rule-hub 规则源管理的规则；规则源之外的手写适配文件不会自动进入安装包。\n"""


def _platform_readme(platform):
    if platform == "Mac":
        home = "`$HOME`；Codex 可用 `$CODEX_HOME` 覆盖，默认 `~/.codex`。"
        claude = "`~/.claude/`"
        cursor = "项目目录下的 `.cursor/`；用户规则粘贴稿需手动放入 Cursor Settings → Rules → User Rules。"
    else:
        home = "`%USERPROFILE%`；Codex 可用环境变量 `CODEX_HOME` 覆盖，默认 `%USERPROFILE%\\.codex`。"
        claude = "`%USERPROFILE%\\.claude\\`"
        cursor = "项目目录下的 `.cursor\\`；用户规则粘贴稿需手动放入 Cursor Settings → Rules → User Rules。"
    return (
        f"# {platform} 安装包\n\n"
        f"此目录中的规则由同一份 `.mdc` 规则源生成。当前平台用户目录：{home}\n\n"
        f"- `codex/`：安装到 Codex 用户目录。\n"
        f"- `Claude/`：将 `rules/` 与 `skills/` 合并到 {claude}，保留其它个人文件。\n"
        f"- `cursor/`：项目规则目标为 {cursor}\n\n"
        "每个工具目录含具体步骤。此包用于分发和安装；生成它不会写入该平台的应用配置。\n"
    )


def _install_readme(platform, target):
    if platform == "Mac":
        if target == "codex":
            return (
                "# Codex 安装\n\n"
                "将 `skills/` 和 `agents/` 合并到 `$CODEX_HOME`，未设置时使用 `~/.codex/`。\n\n"
                "打开 `AGENTS.rulehub.md`，只把 `<!-- rulehub:begin -->` 至 `<!-- rulehub:end -->` 的区块合并到 `$CODEX_HOME/AGENTS.md`；保留现有文件的其它内容。\n"
            )
        if target == "claude":
            return (
                "# Claude Code 安装\n\n"
                "将 `rules/` 和 `skills/` 合并到 `~/.claude/` 对应目录。保留其它个人规则、设置和技能。\n"
            )
        return (
            "# Cursor 安装\n\n"
            "把 `rules/` 合并到项目 `.cursor/rules/`，把 `agents/` 合并到项目 `.cursor/agents/`。\n\n"
            "`rulehub/user-rules-paste.txt` 是用户级规则稿；按文件说明粘贴到 Cursor Settings → Rules → User Rules。\n"
        )
    if target == "codex":
        return (
            "# Codex 安装（Windows）\n\n"
            "将 `skills/` 和 `agents/` 合并到 `%CODEX_HOME%`；未设置该变量时使用 `%USERPROFILE%\\.codex\\`。\n\n"
            "打开 `AGENTS.rulehub.md`，只把 `<!-- rulehub:begin -->` 至 `<!-- rulehub:end -->` 的区块合并到目标 `AGENTS.md`；保留现有文件的其它内容。\n"
        )
    if target == "claude":
        return (
            "# Claude Code 安装（Windows）\n\n"
            "将 `rules/` 和 `skills/` 合并到 `%USERPROFILE%\\.claude\\` 对应目录。保留其它个人规则、设置和技能。\n"
        )
    return (
        "# Cursor 安装（Windows）\n\n"
        "把 `rules/` 合并到项目 `.cursor\\rules\\`，把 `agents/` 合并到项目 `.cursor\\agents\\`。\n\n"
        "`rulehub/user-rules-paste.txt` 是用户级规则稿；按文件说明粘贴到 Cursor Settings → Rules → User Rules。\n"
    )


def _render_target(target, rules, models):
    if target == "codex":
        files = T.render_codex(rules)
        files["AGENTS.rulehub.md"] = f"{T.BEGIN}\n{T.agents_block(rules)}\n{T.END}\n"
        files.update(render_agents("codex", models))
        return files
    if target == "claude":
        return T.render_claude(rules)
    files = T.render_cursor(rules)
    files.update(T.render_paste(rules))
    files.update(render_agents("cursor", models))
    return files


def build_files(rules, models):
    """Return package-relative paths and contents for all six platform/tool bundles."""
    files = {"README.md": _root_readme()}
    for platform in PLATFORMS:
        files[f"{platform}/README.md"] = _platform_readme(platform)
        for target, folder in TARGETS:
            prefix = f"{platform}/{folder}"
            files[f"{prefix}/INSTALL.md"] = _install_readme(platform, target)
            rendered = _render_target(target, rules, models)
            for rel, content in rendered.items():
                if rel != "AGENTS.rulehub.md":
                    problems = T.validate(rel, content)
                    if problems:
                        raise PackageError(f"生成结构校验失败 {platform}/{folder}/{rel}: {'、'.join(problems)}")
                files[f"{prefix}/{rel}"] = content
    return files


def _manifest(files):
    return json.dumps(
        {
            "generated_by": "rulehub-package",
            "schema_version": 1,
            "files": {rel: T.sha(content) for rel, content in sorted(files.items())},
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def _read_manifest(out):
    path = os.path.join(out, MARKER)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if data.get("generated_by") != "rulehub-package" or data.get("schema_version") != 1:
        return None
    return data


def _prepare(out):
    if os.path.lexists(out):
        if os.path.islink(out) or not os.path.isdir(out):
            raise PackageError(f"安装包目标不是普通目录，拒绝覆盖: {out}")
        if os.listdir(out) and _read_manifest(out) is None:
            raise PackageError(f"目标目录不是 rule-hub 安装包，拒绝覆盖: {out}")


def _differences(out, files):
    if not os.path.isdir(out) or _read_manifest(out) is None:
        return ["安装包缺失或所有权标记无效"]
    expected = dict(files)
    expected[MARKER] = _manifest(files)
    actual = {}
    for root, dirs, names in os.walk(out, followlinks=False):
        for name in list(dirs):
            path = os.path.join(root, name)
            if os.path.islink(path):
                rel = os.path.relpath(path, out)
                actual[rel] = None
                dirs.remove(name)
        for name in names:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, out)
            if os.path.islink(path):
                actual[rel] = None
                continue
            try:
                with open(path, encoding="utf-8", newline="") as f:
                    actual[rel] = f.read()
            except (OSError, UnicodeError):
                actual[rel] = None
    problems = []
    for rel in sorted(set(expected) | set(actual)):
        if rel not in actual:
            problems.append(f"缺失: {rel}")
        elif rel not in expected:
            problems.append(f"多余: {rel}")
        elif actual[rel] != expected[rel]:
            problems.append(f"不一致: {rel}")
    return problems


def check(out, files):
    return _differences(out, files)


def generate(src, out, files, dry_run=False):
    """Write a complete owned package tree; reject source nesting and foreign output."""
    src_real = os.path.realpath(src)
    out_real = os.path.realpath(out)
    common = os.path.commonpath((src_real, out_real))
    if common in (src_real, out_real):
        raise PackageError("安装包必须放在规则源目录之外，避免生成文件混入规则源")
    _prepare(out)
    if os.path.isdir(out) and not _differences(out, files):
        return {"written": 0, "unchanged": len(files) + 1}
    if dry_run:
        return {"written": len(files) + 1, "unchanged": 0}
    parent = os.path.dirname(os.path.abspath(out))
    os.makedirs(parent, exist_ok=True)
    stage = tempfile.mkdtemp(prefix=".rulehub-package-", dir=parent)
    backup = None
    try:
        for rel, content in files.items():
            dest = os.path.join(stage, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
        with open(os.path.join(stage, MARKER), "w", encoding="utf-8", newline="\n") as f:
            f.write(_manifest(files))
        if os.path.isdir(out) and os.listdir(out):
            backup = tempfile.mkdtemp(prefix=".rulehub-package-backup-", dir=parent)
            os.rmdir(backup)
            os.replace(out, backup)
        elif os.path.isdir(out):
            os.rmdir(out)
        try:
            os.replace(stage, out)
        except BaseException:
            if backup and not os.path.exists(out):
                os.replace(backup, out)
                backup = None
            raise
        if backup:
            shutil.rmtree(backup, ignore_errors=True)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return {"written": len(files) + 1, "unchanged": 0}
