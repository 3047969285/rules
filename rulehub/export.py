"""把规则源导出成可公开的副本：项目名通用化，扫到凭证就拒绝 导出目录由本工具独占"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"))
from _secrets import scan  # noqa: E402

MARKER = ".exported-by-rulehub"
EXTENSIONS = (".mdc", ".json", ".md")
RENAMES = (("PlagiarismChecking-server", "example-project"), ("黄岛", "项目"), ("huangdao", "example"))


class ExportError(Exception):
    pass


def generalize(text):
    for old, new in RENAMES:
        text = text.replace(old, new)
    return text


def collect(src):
    out = {}
    for root, _, names in os.walk(src, onerror=lambda e: (_ for _ in ()).throw(e)):
        for name in names:
            if name.endswith(EXTENSIONS):
                path = os.path.join(root, name)
                with open(path, encoding="utf-8", newline="") as f:
                    out[os.path.relpath(path, src)] = generalize(f.read())
    return out


def check_secrets(files):
    """凭证只报文件、行号和类型 不输出任何值或前缀"""
    problems = []
    for rel, text in sorted(files.items()):
        hard, soft = scan(text)
        problems += [f"{rel} {hit.split(' (')[0]}" for hit in hard + soft]
    if problems:
        raise ExportError("规则里有疑似凭证，拒绝导出:\n  " + "\n  ".join(problems[:20]))


def prepare_out(out):
    if os.path.isdir(out) and os.listdir(out) and not os.path.exists(os.path.join(out, MARKER)):
        raise ExportError(f"{out} 不是本工具导出的目录，拒绝覆盖")


def export(src, out):
    """返回导出的文件数 先全部通过检查再写入 失败不动已有目录"""
    files = collect(src)
    if not files:
        raise ExportError("规则源里没有可导出的文件")
    check_secrets(files)
    prepare_out(out)
    parent = os.path.dirname(os.path.abspath(out))
    os.makedirs(parent, exist_ok=True)
    tmp = tempfile.mkdtemp(dir=parent)
    try:
        for rel, text in files.items():
            dest = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        with open(os.path.join(tmp, MARKER), "w", encoding="utf-8") as f:
            f.write("此目录由 rulehub export 生成，请改规则源后重新导出，勿手改\n")
        if os.path.isdir(out):
            shutil.rmtree(out)
        os.replace(tmp, out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return len(files)
