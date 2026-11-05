# rule-hub 接口文档

命令行接口。本工具没有网络接口。入口 `bin/rulehub`，插件内通过 `${CLAUDE_PLUGIN_ROOT}/bin/rulehub` 调用。

## 通用参数

| 参数 | 说明 |
|---|---|
| `--source DIR` | 规则源目录。默认依次取环境变量 `RULEHUB_SOURCE`、iCloud `Documents/规则体系`、`~/Documents/规则体系`、`~/Desktop/规则体系` 中第一个存在的 |
| `--home DIR` | 用户主目录，默认 `~`，测试时指向临时目录 |
| `--target T` | `claude`、`codex`、`cursor`，可重复；默认 `claude`、`codex` |
| `--project DIR` | Cursor 目标的项目目录，规则写入 `DIR/.cursor/rules/` |

## 命令

### `rulehub sync`
生成并写入目标。附加参数：`--dry-run` 只报告不写；`--force` 跳过"规则数骤降"护栏；`--quiet` 只输出错误；`--hook` 给钩子用，失败时输出 `systemMessage` 提示用户且退出码恒为 0。某个目标写不进去（例如 `~/.codex` 指向未挂载的固态）只影响该目标，其余目标照常写入，最后以退出码 2 报告。

### `rulehub check`
重新渲染并与磁盘比对，同时校验结构。无问题退出码 0。

### `rulehub lint`
检查规则源。`--dups` 追加跨规则重复行报告；`--fix` 原地规范化格式（去 BOM、统一 LF、去行尾空白）。

### `rulehub list`
输出 `类别<TAB>id<TAB>globs`。

### `rulehub import FILE`
把普通文档转成规则源 `.mdc`，默认放到规则源的 `06-导入/`，默认类别 ondemand（不占常驻上下文）。支持 `.md`、`.mdc`、`.txt`、`.markdown`、`.cursorrules`（`CLAUDE.md`、`AGENTS.md` 也走 `.md`），二进制文档不支持。已有 frontmatter 的沿用其中的 description、globs、alwaysApply、when。

| 参数 | 说明 |
|---|---|
| `--kind always\|scoped\|ondemand` | 指定类别；常驻规则会进每轮上下文，慎用 |
| `--globs G` | 适用文件，给了且未指定 kind 时视为 scoped；scoped 必须有 |
| `--id ID` | 规则 id，默认取文件名（中文名则用内容哈希） |
| `--description D`、`--when W` | 覆盖描述与触发说明 |
| `--dir D` | 规则源下的子目录，默认 `06-导入` |
| `--force` | 覆盖同名文件 |

导入后运行 `rulehub sync` 才会生成到各工具。id 与现有规则重复、结果无效时拒绝并回滚。

### `rulehub stats`
按类别列出每条规则的正文字节数，用来判断常驻规则是否过重。

### `rulehub doctor`
一键体检，每行 `[ok]`、`[warn]`、`[fail]`、`[info]`：规则源与 iCloud 占位文件、各目标是否可写（含悬空链接）、生成物是否漂移、`AGENTS.md` 体积、常驻规则体积、上次同步结果、插件是否安装、`settings.json` 是否残留重复 hook、本机有无 Codex 与 Cursor 命令行。有 `fail` 时退出码 1。

### `rulehub history` 与 `rulehub restore SNAPSHOT --yes`
每次 `sync` 前，规则源内容有变化就自动存一份快照到 `~/.local/share/rulehub/snapshots/`（在 iCloud 之外，保留最近 30 份）。`history` 列出快照（最新在前）。`restore` 把规则源的 `.mdc` 恢复到该快照，会先给当前状态再存一份保险快照，并删除快照里没有的 `.mdc`；必须带 `--yes`。恢复后运行 `sync`。

### `rulehub launchd print|install|uninstall`
后台自动 `sync`：监听规则源目录及其所有子目录的变化（WatchPaths，间隔至少 10 秒），并每 10 分钟定时跑一次作为兜底（目录监听感知不到子目录里文件的原地编辑），加载时也跑一次。日志在 `~/.local/share/rulehub/launchd.log`。`print` 只输出 plist；`install` 写入 `~/Library/LaunchAgents/com.rulehub.sync.plist` 并加载，是常驻后台配置，必须由用户在终端主动执行；`uninstall` 卸载。launcher 路径取自运行 `install` 的那份 `bin/rulehub`，建议从源码目录执行以免插件升级后路径失效。后台读取 iCloud 目录可能被 macOS 隐私权限拦截，装好后用 `doctor` 和日志确认。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | `check` 或 `lint` 发现问题 |
| 2 | 致命错误：源缺失、无规则、源有 error 级问题、护栏触发、AGENTS.md 超限 |

## Hook 开关

插件的三个 hook 默认全开。配置文件 `~/.config/rulehub/config.json`（可用环境变量 `RULEHUB_CONFIG` 指定别处）：

```
{"hooks": {"java_guard": false, "rule_router": true, "stop_gate": false}}
```

`java_guard` 是 Java 与 mapper 扫描及测试/yml 询问，`rule_router` 是每轮路由提示，`stop_gate` 是结束前编译闸门。配置缺失或损坏时一律保持开启。

## 规则源格式

`.mdc` 文件，LF 换行，开头为 frontmatter：

```
---
description: 一行描述
globs: **/*.java,**/*.{xml,yml}   # 可选，逗号分隔，支持花括号
alwaysApply: false                # true 或 false
when: 何时使用的补充说明           # 可选，只追加到技能描述，Cursor 忽略
---
正文
```

规则 id 为文件名去掉 `.mdc`，全局唯一。

## 同步状态

每次 `sync` 把结果写入 `~/.config/rulehub/state.json`。上次失败时，规则路由钩子在之后每一轮都提示一次，直到下次同步成功。

## 生效时机

Claude Code 在会话启动时读取规则，而 SessionStart 钩子跑在读取之后，所以启动时同步出的变化要到**下一个会话**才生效。插件因此在 SessionEnd 也同步一次，会话期间改了规则源，结束时就生成好，下个会话直接生效。需要立刻生效时手动 `rulehub sync` 并重开会话。

## 生成物

| 目标 | 路径 | 说明 |
|---|---|---|
| claude | `~/.claude/rules/rs-<id>.md`、`~/.claude/skills/rs-<id>/SKILL.md` | 清单 `~/.claude/.rulehub-manifest.json` |
| codex | `~/.codex/skills/rs-<id>/SKILL.md`、`~/.codex/AGENTS.md` 中 `<!-- rulehub:begin -->` 到 `<!-- rulehub:end -->` 区块 | 清单 `~/.codex/.rulehub-manifest.json` |
| cursor | `<project>/.cursor/rules/rs-<id>.mdc`（frontmatter 不加引号；带 globs 的规则不写 description；Codex 技能描述截到 500 字符）、`~/.cursor/rulehub/user-rules-paste.txt` | 粘贴稿用于 Cursor 设置里的 User Rules；清单在 `.cursor/.rulehub-manifest.json` |

每个生成文件带 `rulehub:generated` 标记，勿手改。
