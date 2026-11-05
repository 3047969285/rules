"""macOS launchd 监听规则源变化后自动同步 只生成和管理 不会自行安装"""
import os
import subprocess

LABEL = "com.rulehub.sync"
PYTHON = "/usr/bin/python3"
INTERVAL = 600
LOG = os.path.expanduser("~/.local/share/rulehub/launchd.log")


def plist_path(home):
    return os.path.join(os.path.expanduser(home), "Library", "LaunchAgents", LABEL + ".plist")


def watch_dirs(src):
    return [src] + sorted(os.path.join(r, d) for r, ds, _ in os.walk(src) for d in ds)


def render(launcher, watches, python=PYTHON):
    args = "".join(f"    <string>{a}</string>\n" for a in (python, launcher, "sync", "--quiet"))
    paths = "".join(f"    <string>{w}</string>\n" for w in watches)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
{args}  </array>
  <key>WatchPaths</key>
  <array>
{paths}  </array>
  <key>StartInterval</key><integer>{INTERVAL}</integer>
  <key>RunAtLoad</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardErrorPath</key><string>{LOG}</string>
  <key>StandardOutPath</key><string>{LOG}</string>
</dict>
</plist>
"""


def install(home, launcher, src):
    path = plist_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render(launcher, watch_dirs(src)))
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", path], check=True)
    return path


def uninstall(home):
    path = plist_path(home)
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], check=False)
    if os.path.exists(path):
        os.remove(path)
    return path
