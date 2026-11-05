"""把普通文档导入为规则源 .mdc"""
import hashlib
import os
import re

from .model import GLOB_SPLIT, load_rules, normalize, parse
from .targets import one_line

SUFFIXES = (".md", ".mdc", ".txt", ".markdown")
ID_OK = re.compile(r"^[A-Za-z0-9][\w.-]*$")


class ImportFailed(Exception):
    pass


def make_id(name, text):
    base = re.sub(r"[^a-z0-9]+", "-", os.path.splitext(name)[0].lstrip(".").lower()).strip("-")
    return base or "imported-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def first_title(body):
    for line in body.split("\n"):
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:80]
    return ""


def import_file(src, path, kind=None, globs=None, rid=None, description=None, when=None, subdir="06-导入", force=False):
    """导入一个文档到规则源 返回目标路径"""
    name = os.path.basename(path)
    if not (name.endswith(SUFFIXES) or name == ".cursorrules"):
        raise ImportFailed("只支持 .md .mdc .txt .markdown 和 .cursorrules 二进制文档请先转成文本")
    with open(path, encoding="utf-8") as f:
        meta, body = parse(f.read())
    meta = meta or {}
    body = normalize(body).strip("\n") + "\n"
    rid = rid or make_id(name, body)
    if not ID_OK.match(rid):
        raise ImportFailed(f"id {rid!r} 不合法 只能用字母数字和 - _ .")
    globs = globs if globs is not None else meta.get("globs", "")
    kind = kind or ("always" if meta.get("alwaysApply") == "true" else "scoped" if globs else "ondemand")
    if kind == "scoped" and not [g for g in GLOB_SPLIT.split(globs) if g.strip()]:
        raise ImportFailed("scoped 必须用 --globs 指定适用文件")
    desc = one_line(description or meta.get("description") or first_title(body) or rid, 200)
    front = [f"description: {desc}"]
    if kind == "scoped":
        front.append(f"globs: {globs}")
    front.append(f"alwaysApply: {'true' if kind == 'always' else 'false'}")
    if when or meta.get("when"):
        front.append(f"when: {one_line(when or meta['when'], 200)}")
    dest = os.path.join(src, subdir, rid + ".mdc")
    if os.path.exists(dest) and not force:
        raise ImportFailed(f"{dest} 已存在 加 --force 覆盖")
    if any(r.id == rid for r in load_rules(src)[0]) and not os.path.exists(dest):
        raise ImportFailed(f"规则 id {rid} 已存在于别的目录")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write("---\n" + "\n".join(front) + "\n---\n\n" + body)
    bad = [m for lv, rel, m in load_rules(src)[1] if lv == "error" and rel.endswith(rid + ".mdc")]
    if bad:
        os.remove(dest)
        raise ImportFailed("导入结果无效: " + "; ".join(bad))
    return dest
