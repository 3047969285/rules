# rule-hub 接口文档

命令行接口。本工具没有网络接口。入口 `bin/rulehub`，插件内通过 `${CLAUDE_PLUGIN_ROOT}/bin/rulehub` 调用。

## 通用参数

| 参数 | 说明 |
|---|---|
| `--source DIR` | 规则源目录。默认依次取环境变量 `RULEHUB_SOURCE`、iCloud `Documents/规则体系`、`~/Documents/规则体系`、`~/Desktop/规则体系` 中第一个存在的 |
| `--home DIR` | 用户主目录，默认 `~`，测试时指向临时目录 |
| `--target T` | `claude`、`codex`、`cursor`，可重复；默认三个都做（cursor 在用户目录写规则粘贴稿与复核员，项目规则需加 `--project`） |
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

### `rulehub enforcement`
读取规则源里的 `enforcers.json`（`{规则id: {"hooks": [...], "manual": "原因", "gap": "能机检但未做"}}`），把规则分为机器执行、靠自觉（已登记原因）、未登记三类，并列出已知缺口和已过期的登记。`--strict` 在有未登记或过期项时退出码 1。原则：能用机器判断的规则必须有执行者，做不到的要写明原因。

### `rulehub violations [--days N]`
汇总违规与拦截记录（`~/.local/share/rulehub/violations.jsonl`，默认最近 30 天）：按规则、按来源统计，并指出两类候选——「靠自觉却被闸门或复核抓到」的规则应升级为机器检查，「有机器执行者却长期从未触发」的规则可考虑精简。记录里不含凭证和文件内容，只有拦截类型的简短说明。

### `rulehub export OUT [--extra FILE ...]`
把规则源导出成可公开的副本：只导出 `.mdc`、`.json`、`.md`；`PlagiarismChecking-server`、`黄岛`、`huangdao` 替换成通用名；用与密钥守卫相同的规则扫描，任何文件扫到凭证就拒绝，只报文件、行号和类型，不输出任何值。先全部通过检查再写入，失败不动已有目录。OUT 由本工具独占，目录非空且没有 `.exported-by-rulehub` 标记时拒绝覆盖。`--extra` 可重复，带上规则源之外的文件（`.mdc`、`.json`、`.md`、`.txt`），放在 `OUT/extra/` 下，同样通用化和扫描，重名或不支持的类型报错。本机规则源和附加文件原文都不会被修改。

### `rulehub experiment start|stop|verdict|report`：规则试运行
- `start RULE`：规则开始试运行，写入 `~/.config/rulehub/config.json` 的 `experimental`（保留其他配置）。试运行的规则所有钩子只记日志（带 `trial` 标记），不拦截、不提醒；同一文件里其他规则不受影响。规则 id 不存在或已在试运行时报错。
- `stop RULE`：转正，恢复拦截。
- `verdict RULE upheld|rejected [--note 理由]`：记录你对复核或闸门意见的裁决，属实记 `upheld`，不属实记 `rejected`。裁决也写入违规日志（来源 `verdict`），不算触发次数。
- `report [--days N]`：按规则列出触发、采纳、驳回、待裁决、精度。待裁决 = 复核员与证据闸门提出的意见数减去已记录的裁决数，用来发现没人裁决的意见。试运行规则只统计开始之后的记录，并给建议：触发 ≥ 10 且裁决 ≥ 5 且精度 ≥ 80% 为「可转正」，裁决 ≥ 5 且精度 < 50% 为「建议下线或改写」，其余「继续观察」。正式规则只列数字不给建议。

闭环三处：复核与证据闸门的拦截文本末尾附记录裁决的命令；报告显示待裁决数；会话开始时（路由钩子）若有试运行规则可转正或建议下线，提示一次，24 小时内不重复，时间戳存 `~/.cache/rulehub/experiment_notice`，没有可决定的不打扰。

涉及密钥的 `security-confidentiality` 也可以试运行，但试运行期间该规则的拦截全部失效，仅在用户明确要求时使用。

### `rulehub task <动作>`：后端开发流水线
文档和状态存放在固态 `devdocs_root`（默认 `/Volumes/Games/Projects/DevDocs`，可在配置里改），每个任务一个目录 `<项目>/<时间-任务名>/`，不进项目仓库。动作：

| 动作 | 说明 |
|---|---|
| `new --name N --level 小\|标准 --reason R` | 建任务并生成产物模板；必须写难度理由；项目同时只能有一个未完成任务；极简不需要任务 |
| `skip --reason R` | 用户明确要求直接做时记录原因并放行，同时写入违规日志 |
| `status` | 当前任务的难度、已完成阶段、下一阶段和产物问题 |
| `review <阶段>` | 校验产物完整后，让最便宜的模型只读评审并记录结论；`hooks.stage_review: false` 可关闭，记为 skipped |
| `advance <阶段>` | 产物校验、红绿记录、评审通过且评审后未再改动，才推进；最后一个阶段 final 推进后任务完成 |
| `red --cmd C` | 运行命令，必须失败才记录；成功则拒绝（说明测试没覆盖新行为） |
| `green --cmd C` | 运行命令，必须成功才记录；必须发生在最后一次修改源码之后 |

阶段：小 = prd、tests、impl、final；标准 = prd、api、tests、impl、final。`--project` 默认当前目录，需在 Maven 或 Gradle 工程内。

产物校验：`prd.md` 必须填写规定的各节（标准 8 节，小 4 节）；`api.md` 必须有「接口清单」「新增与修改文件清单」「事务与并发」「错误码与异常」且出现 路径、方法、参数、校验、响应、状态码、鉴权、幂等；`test-plan.md` 必须覆盖九个维度（正常、边界、异常、权限、并发、幂等、数据一致性、安全、性能，小难度只要求前三个），每个维度有用例或写明「不适用：理由」。

### `rulehub package OUT [--dry-run | --check]`

从唯一 `.mdc` 规则源生成可分发安装包，输出目录必须在规则源目录之外：

```text
OUT/
  Mac/{codex,Claude,cursor}/
  Windows/{codex,Claude,cursor}/
```

各工具子目录按原生结构保存文件，并带 `INSTALL.md`；Codex 提供可合并的 `AGENTS.rulehub.md` 受管区块，Cursor 同时提供项目规则和用户规则粘贴稿。平台目录中的规则内容来自同一规则源，安装路径说明按 macOS/Windows 区分。规则源之外手工维护的适配文件不会自动包含。

| 参数 | 说明 |
|---|---|
| `OUT` | 安装包根目录；非空且没有 `.rulehub-package.json` 所有权标记时拒绝覆盖 |
| `--dry-run` | 不写入，只报告预计生成文件数 |
| `--check` | 只比较输出文件与当前规则源，漂移或缺失时退出码为 1 |

重新运行会更新由本工具管理的整个安装包目录；不要在该目录存放个人文件或手改生成内容。源规则不会被改动。

### `rulehub history` 与 `rulehub restore SNAPSHOT --yes`
每次 `sync` 前，规则源内容有变化就自动存一份快照到 `~/.local/share/rulehub/snapshots/`（在 iCloud 之外，保留最近 30 份）。`history` 列出快照（最新在前）。`restore` 把规则源的 `.mdc` 恢复到该快照，会先给当前状态再存一份保险快照，并删除快照里没有的 `.mdc`；必须带 `--yes`。恢复后运行 `sync`。

### `rulehub launchd print|install|uninstall`
后台自动 `sync`：监听规则源目录及其所有子目录的变化（WatchPaths，间隔至少 10 秒），并每 10 分钟定时跑一次作为兜底（目录监听感知不到子目录里文件的原地编辑），加载时也跑一次。日志在 `~/.local/share/rulehub/launchd.log`。`print` 只输出 plist；`install` 写入 `~/Library/LaunchAgents/com.rulehub.sync.plist` 并加载，是常驻后台配置，必须由用户在终端主动执行；`uninstall` 卸载。launcher 路径取自运行 `install` 的那份 `bin/rulehub`，建议从源码目录执行以免插件升级后路径失效。**限制（实测）**：规则源在 iCloud Drive、桌面、文稿等受 macOS 隐私保护的位置时，后台任务读不到文件，同步会失败。只有规则源放在不受保护的目录（如 `~/项目` 下），或给 `/usr/bin/python3` 授予完全磁盘访问权限（系统安全设置，需用户自己操作）时才可用。装好后用 `doctor` 和日志确认。

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

`secret_guard` 是写入前凭证扫描（所有文件类型）：私钥、AWS、GitHub、Slack、Anthropic/OpenAI、Google 的固定格式令牌直接拒绝；带密码的连接串、JDBC 密码参数、疑似明文口令询问；占位符、`localhost` 连接串和带 `rulehub:allow-secret` 注释的行放行；`application*.yml/yaml/properties`（按规则 java-10 本就是放口令的位置）只查固定格式令牌，不再询问口令；提示里只显示前 4 位。`evidence_gate` 是结束前的证据闸门与独立复核（见下），`rule_review` 单独关闭其中的模型复核；`reviewers` 指定各工具复核员的模型，默认 `{"claude": "haiku", "codex": "gpt-6-luna", "cursor": "composer-2.5"}`。`java_scope` 是路径列表，配置后 `java_guard` 和 `stop_gate` 只管这些目录里的文件（真实路径比较，支持 `~`），不配置则不限制；密钥守卫不受它影响，始终全局。`java_guard` 是 Java 与 mapper 的写入前拦截（违规内容直接拒绝写入，让模型按规则改正）、写入后扫描，以及写测试文件和新建 yml 前的询问，`rule_router` 是每轮路由提示，`stop_gate` 是结束前编译闸门。配置缺失或损坏时一律保持开启。

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

## 阶段闸门（stage_gate）

写文件前由钩子 `stage_gate` 检查，对象是 Maven 或 Gradle 工程里的 `.java` 与 mapper `.xml`：
- 没有进行中的任务：本次会话第一个源文件放行，改到第二个不同的源文件时拒绝，要求先 `task new` 或 `task skip`（防止把大改动当成极简）。
- 有任务：需求阶段（标准为接口阶段）通过评审前不能写测试；测试阶段通过评审前不能写实现；标准难度下实现只能改接口文档「新增与修改文件清单」里的文件。
- 跳过类任务全部放行。
- 任务状态文件（`state.json`、`red.json`、`green.json`、`reviews/`）只能由 `task` 命令写入，直接改会被拒绝。
- 固态文档目录不可用时放行并记入违规日志，`doctor` 会提示。
- 任务允许写测试后，`java_guard` 不再询问测试文件。
- 已知局限：通过 Bash 重定向直接改状态文件不在拦截范围内，靠评审后产物哈希校验和终审兜底。

## 证据闸门与独立复核

结束前钩子 `evidence_gate` 分两步，每次结束最多拦一次：

1. **证据检查（确定性）**：回答里出现「已验证、测试通过、已完成」等声称时，必须有证据——本轮完全没调用工具、改了代码却没在最后一次修改后运行命令、项目有测试却没运行测试命令，三种情形都会被拦下，要求先验证或改成「未验证」。
2. **独立复核（最便宜的模型，只读）**：通过后，用 `claude -p --model haiku`（禁用所有工具，不加载用户设置，子进程内所有钩子关闭）对照 6 项清单复核本轮——用户原话、操作摘要、回答草稿，凭证先脱敏。发现有证据的违规时拦下并转给主代理，**由主代理裁决**，复核员只出意见。复核失败、超时或没有违规一律静默放行。

Codex 与 Cursor 没有可挂接的结束钩子，只生成同一份复核员定义供手动调用：Codex 写 `~/.codex/agents/rule-reviewer.toml`（`gpt-6-luna`，只读沙箱），Cursor 写 `~/.cursor/agents/rule-reviewer.md`（`composer-2.5`，`readonly`）。已知 Cursor 子代理的 `model` 字段偶尔被忽略（论坛反馈为已确认缺陷）。Claude 的复核员随插件发布在 `agents/rule-reviewer.md`（`haiku`，只有 Read、Grep、Glob）。

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
