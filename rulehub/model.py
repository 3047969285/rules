"""规则源解析与分类"""
import os
import re
from dataclasses import dataclass

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.S)
GLOB_SPLIT = re.compile(r",(?![^{]*\})")
BRACE = re.compile(r"\{([^}]*)\}")


@dataclass
class Rule:
    id: str
    rel: str
    description: str
    when: str
    always: bool
    globs: list
    body: str

    @property
    def kind(self):
        return "always" if self.always else "scoped" if self.globs else "ondemand"


def normalize(text):
    lines = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).rstrip("\n") + "\n"


def expand_glob(glob):
    m = BRACE.search(glob)
    if not m:
        return [glob]
    return [g for alt in m.group(1).split(",") for g in expand_glob(glob[:m.start()] + alt + glob[m.end():])]


def parse(text):
    """返回 (meta, body) 无 frontmatter 时 meta 为 None"""
    m = FRONTMATTER.match(normalize(text))
    if not m:
        return None, text
    meta = {}
    for line in m.group(1).split("\n"):
        key, sep, val = line.partition(":")
        if sep:
            meta[key.strip()] = val.strip().strip("\"'")
    return meta, m.group(2).lstrip("\n")


def load_rules(src):
    """读取规则源 返回 (rules, issues) issues 为 (级别, 文件, 说明)"""
    rules, issues, seen = [], [], {}
    for root, _, files in sorted(os.walk(src)):
        for fn in sorted(files):
            if not fn.endswith(".mdc"):
                continue
            rel = os.path.relpath(os.path.join(root, fn), src)
            rid = fn[:-4]
            with open(os.path.join(root, fn), encoding="utf-8", newline="") as f:
                raw = f.read()
            if raw.startswith("﻿"):
                issues.append(("warn", rel, "含 BOM"))
            if "\r" in raw:
                issues.append(("warn", rel, "含 CRLF 换行"))
            meta, body = parse(raw)
            if meta is None:
                issues.append(("error", rel, "缺少 frontmatter"))
                continue
            if rid in seen:
                issues.append(("error", rel, f"id 与 {seen[rid]} 重复"))
                continue
            seen[rid] = rel
            flag = meta.get("alwaysApply", "false").lower()
            if flag not in ("true", "false"):
                issues.append(("error", rel, f"alwaysApply 非法值 {flag!r}"))
                continue
            globs = [p for g in GLOB_SPLIT.split(meta.get("globs", "")) if g.strip() for p in expand_glob(g.strip())]
            if flag == "true" and globs:
                issues.append(("warn", rel, "alwaysApply 为 true 时 globs 不生效"))
            if not meta.get("description"):
                issues.append(("error", rel, "缺少 description"))
                continue
            if not body.strip():
                issues.append(("error", rel, "正文为空"))
                continue
            rules.append(Rule(rid, rel, meta["description"], meta.get("when", ""), flag == "true", globs, body))
    issues += dangling_refs(rules)
    return rules, issues


def dangling_refs(rules):
    ids = {r.id for r in rules}
    out = []
    for r in rules:
        refs = set(re.findall(r"([A-Za-z0-9][\w-]*)\.mdc", r.body)) | set(re.findall(r"`(java-\d\d[\w-]*)`", r.body))
        for ref in sorted(x for x in refs - ids if not any(i.startswith(x + "-") for i in ids)):
            out.append(("warn", r.rel, f"引用了不存在的规则 {ref}"))
    return out


def duplicate_lines(rules, min_len=24):
    seen = {}
    for r in rules:
        for line in {l.strip().lstrip("-*0123456789. ").strip() for l in r.body.split("\n")}:
            if len(line) >= min_len and not line.startswith(("#", "|", "`", "[")):
                seen.setdefault(line, []).append(r.id)
    return {l: ids for l, ids in seen.items() if len(ids) > 1}


def fix_source(src):
    """原地规范化格式 返回改动文件数"""
    changed = 0
    for root, _, files in os.walk(src):
        for fn in files:
            if fn.endswith(".mdc"):
                path = os.path.join(root, fn)
                with open(path, encoding="utf-8", newline="") as f:
                    raw = f.read()
                norm = normalize(raw)
                if norm != raw:
                    with open(path, "w", encoding="utf-8", newline="\n") as f:
                        f.write(norm)
                    changed += 1
    return changed
