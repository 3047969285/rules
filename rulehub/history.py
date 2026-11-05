"""规则源快照与回滚 历史放在 iCloud 之外 避免同步冲突"""
import hashlib
import os
import shutil
import time


def base_dir(home):
    return os.path.join(os.path.expanduser(home), ".local", "share", "rulehub", "snapshots")


def mdc_files(src):
    return sorted(
        os.path.relpath(os.path.join(root, fn), src)
        for root, _, files in os.walk(src) for fn in files if fn.endswith(".mdc")
    )


def digest(src):
    h = hashlib.sha256()
    for rel in mdc_files(src):
        h.update(rel.encode("utf-8"))
        with open(os.path.join(src, rel), "rb") as f:
            h.update(f.read())
    return h.hexdigest()


def copy_files(src_dir, dest_dir):
    for rel in mdc_files(src_dir):
        os.makedirs(os.path.dirname(os.path.join(dest_dir, rel)), exist_ok=True)
        shutil.copy2(os.path.join(src_dir, rel), os.path.join(dest_dir, rel))


def list_snapshots(home):
    base = base_dir(home)
    return sorted(os.listdir(base), reverse=True) if os.path.isdir(base) else []


def snapshot(src, home, keep=30):
    """内容有变化才生成 返回快照名 无变化返回 None"""
    d = digest(src)
    existing = list_snapshots(home)
    if existing and existing[0].endswith(d[:8]):
        return None
    now = time.time()
    name = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"{int(now * 1000) % 1000:03d}-" + d[:8]
    copy_files(src, os.path.join(base_dir(home), name))
    for old in list_snapshots(home)[keep:]:
        shutil.rmtree(os.path.join(base_dir(home), old))
    return name


def restore(src, home, name):
    """回滚到某个快照 先给当前状态留一份保险快照 返回 (恢复文件数, 保险快照名)"""
    snap = os.path.join(base_dir(home), name)
    if not os.path.isdir(snap):
        raise KeyError(name)
    safety = snapshot(src, home)
    wanted = set(mdc_files(snap))
    for rel in mdc_files(src):
        if rel not in wanted:
            os.remove(os.path.join(src, rel))
    copy_files(snap, src)
    return len(wanted), safety
