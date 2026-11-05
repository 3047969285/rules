"""各工具的生成物渲染 校验与写入"""
import hashlib
import json
import os
import re

PREFIX = "rs-"
BANNER = "<!-- rulehub:generated 请改规则源 勿手改 -->"
BEGIN, END = "<!-- rulehub:begin -->", "<!-- rulehub:end -->"
MANIFEST = ".rulehub-manifest.json"
AGENTS_LIMIT = 32768
SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
LEGACY_MARK = "自动生成自"


class GuardError(Exception):
    pass


def q(text):
    return json.dumps(text, ensure_ascii=False)


def one_line(text, limit=1000):
    return re.sub(r"\s+", " ", text).strip()[:limit]


def sid(rule):
    return (PREFIX + re.sub(r"[^a-z0-9]+", "-", rule.id.lower()).strip("-"))[:64]


def desc(rule, with_scope=False, limit=1000):
    text = rule.description + (" " + rule.when if rule.when else "")
    if with_scope and rule.globs:
        text += "（适用文件 " + " ".join(rule.globs) + "）"
    return one_line(text, limit)


def doc(front, body):
    return f"---\n{front}\n---\n\n{BANNER}\n\n{body}"


def skill(rule, scoped_hint, limit=1000):
    return doc(f"name: {sid(rule)}\ndescription: {q(desc(rule, scoped_hint, limit))}", rule.body)


def render_claude(rules):
    files = {}
    for r in rules:
        if r.kind == "always":
            files[f"rules/{sid(r)}.md"] = doc(f"description: {q(desc(r))}", r.body)
        elif r.kind == "scoped":
            paths = "\n".join(f"  - {q(g)}" for g in r.globs)
            files[f"rules/{sid(r)}.md"] = doc(f"paths:\n{paths}", r.body)
        else:
            files[f"skills/{sid(r)}/SKILL.md"] = skill(r, False)
    return files


def render_codex(rules):
    return {f"skills/{sid(r)}/SKILL.md": skill(r, True, 500) for r in rules if r.kind != "always"}


def agents_block(rules):
    parts = ["## 规则体系（rule-hub 生成 请改规则源 勿手改）", ""]
    for r in rules:
        if r.kind == "always":
            parts += [f"### {r.id}", r.body.strip(), ""]
    parts.append("### 按需技能")
    parts += [f"- {sid(r)}：{desc(r, True)}" for r in rules if r.kind != "always"]
    return "\n".join(parts)


def render_cursor(rules):
    files = {}
    for r in rules:
        front = f"globs: {','.join(r.globs)}" if r.kind == "scoped" else f"description: {desc(r, limit=300)}"
        front += f"\nalwaysApply: {'true' if r.always else 'false'}"
        files[f"rules/{sid(r)}.mdc"] = doc(front, r.body)
    return files


def render_paste(rules):
    body = "\n\n".join(f"【{r.id}】\n{r.body.strip()}" for r in rules if r.kind == "always")
    return {"rulehub/user-rules-paste.txt": "# 粘贴到 Cursor Settings → Rules → User Rules\n\n" + body + "\n"}


def validate(rel, content):
    """生成物结构校验 返回问题列表"""
    out = []
    if content.startswith("﻿") or "\r" in content:
        out.append("含 BOM 或 CRLF")
    if rel.endswith(".txt"):
        return out
    m = re.match(r"\A---\n(.*?)\n---\n", content, re.S)
    if not m:
        return out + ["frontmatter 缺失或未闭合"]
    meta = {}
    for line in m.group(1).split("\n"):
        key, sep, val = line.partition(":")
        if sep:
            meta[key.strip()] = val.strip()
    if rel.endswith("SKILL.md"):
        name = meta.get("name", "")
        if name != os.path.basename(os.path.dirname(rel)) or not SKILL_NAME.match(name):
            out.append(f"技能名 {name!r} 与目录不符或不合法")
        if not meta.get("description") or len(json.loads(meta["description"])) > 1024:
            out.append("技能描述为空或超过 1024 字符")
    elif rel.endswith(".mdc"):
        if not (meta.get("description") or meta.get("globs")):
            out.append("缺少 description 或 globs")
        if meta.get("alwaysApply") not in ("true", "false"):
            out.append("alwaysApply 缺失或非法")
    elif "description" not in meta and "paths:" not in m.group(1):
        out.append("规则缺少 description 或 paths")
    return out


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def atomic_write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".rulehub-tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    os.replace(tmp, path)


def legacy_owned(root):
    """旧脚本生成的 rs- 文件 带有生成标记才算自己的"""
    found = set()
    for sub in ("rules", "skills"):
        base = os.path.join(root, sub)
        if not os.path.isdir(base):
            continue
        for name in os.listdir(base):
            if not name.startswith(PREFIX):
                continue
            rel = f"{sub}/{name}" if os.path.isfile(os.path.join(base, name)) else f"{sub}/{name}/SKILL.md"
            head = (read(os.path.join(root, rel)) or "")[:800]
            if LEGACY_MARK in head or "rulehub:generated" in head:
                found.add(rel)
    return found


def apply(root, files, dry=False, force=False):
    problems = [f"{rel}: {p}" for rel, c in files.items() for p in validate(rel, c)]
    if problems:
        raise GuardError("生成物结构校验失败\n  " + "\n  ".join(problems))
    mpath = os.path.join(root, MANIFEST)
    old = json.loads(read(mpath) or "{}")
    if old and len(files) < len(old) * 0.5 and not force:
        raise GuardError(f"生成文件数 {len(files)} 不到上次 {len(old)} 的一半 疑似规则源异常 加 --force 才继续")
    report = {"written": [], "removed": [], "unchanged": 0}
    for rel, content in files.items():
        if read(os.path.join(root, rel)) == content:
            report["unchanged"] += 1
            continue
        report["written"].append(rel)
        if not dry:
            atomic_write(os.path.join(root, rel), content)
    for rel in sorted((set(old) | legacy_owned(root)) - set(files)):
        path = os.path.join(root, rel)
        if os.path.exists(path):
            report["removed"].append(rel)
            if not dry:
                os.remove(path)
                parent = os.path.dirname(path)
                if os.path.basename(parent).startswith(PREFIX) and not os.listdir(parent):
                    os.rmdir(parent)
    if not dry:
        atomic_write(mpath, json.dumps({rel: sha(c) for rel, c in files.items()}, ensure_ascii=False, indent=1) + "\n")
    return report


def drift(root, files):
    """只比对 不写 返回问题列表"""
    out = []
    for rel, content in files.items():
        cur = read(os.path.join(root, rel))
        if cur is None:
            out.append(f"{rel}: 缺失")
        elif cur != content:
            out.append(f"{rel}: 与规则源不一致")
    return out


def update_agents(path, block, dry=False, force=False):
    cur = read(path) or ""
    if cur.count(BEGIN) != cur.count(END) or cur.count(BEGIN) > 1:
        raise GuardError(f"{path} 的 rulehub 区块标记不成对或重复")
    new_block = f"{BEGIN}\n{block}\n{END}"
    if BEGIN in cur:
        new = re.sub(re.escape(BEGIN) + ".*?" + re.escape(END), lambda m: new_block, cur, flags=re.S)
    else:
        new = (cur.rstrip("\n") + "\n\n" if cur.strip() else "") + new_block + "\n"
    size = len(new.encode("utf-8"))
    if size > AGENTS_LIMIT and not force:
        raise GuardError(f"{path} 将达 {size} 字节 超过 Codex 默认上限 {AGENTS_LIMIT} 会被静默截断")
    if new != cur and not dry:
        atomic_write(path, new)
    return new != cur


def agents_drift(path, block):
    cur = read(path) or ""
    expected = f"{BEGIN}\n{block}\n{END}"
    return [] if expected in cur else [f"{path}: 受管区块缺失或与规则源不一致"]
