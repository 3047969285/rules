# rule-hub 产品需求说明书

## 背景与目标

用户的规则体系用 Cursor 的 `.mdc` 写成，存放在 iCloud `Documents/规则体系`。Claude Code、Codex、Cursor 各自读不同位置和格式，手工转换导致三个问题：格式识别不稳（Cursor 对 frontmatter 错误静默跳过、含 BOM 的文件识别失败）、同步脚本出错会误删已生成规则、规则之间内容重复与冲突没有检查手段。

目标：**一份规则源，一条命令，生成三个工具各自能稳定识别的格式，并在写入前后自动校验**。以 Claude Code 插件形式分发，会话启动时自动同步。

## 范围

| 在范围 | 不在范围 |
|---|---|
| 解析 `.mdc` 规则源（frontmatter：description、globs、alwaysApply、可选 when） | 修改规则语义（由用户在规则源里改）；二进制文档（docx、pdf）的解析 |
| 生成 Claude Code 规则与技能、Codex 技能与 AGENTS.md 受管区块、Cursor 项目规则与用户规则粘贴稿 | Cursor 全局规则文件（Cursor 无此机制，只能粘贴到设置里） |
| 源规则 lint、重复内容检测、生成物校验与漂移检查 | 规则内容的自动改写 |
| Claude Code 插件打包：hooks、技能、marketplace | 其它 IDE |

## 规则分类（决定落点）

| 类别 | 判定 | Claude Code | Codex | Cursor |
|---|---|---|---|---|
| always | `alwaysApply: true` | `~/.claude/rules/rs-*.md` 无 paths，常驻 | `~/.codex/AGENTS.md` 受管区块 | `.cursor/rules/rs-*.mdc`，alwaysApply true |
| scoped | 有 `globs` | `rules/rs-*.md` 带 `paths` | `skills/rs-*/SKILL.md`，描述写明适用文件 | `.mdc` 带 globs |
| ondemand | 其余 | `skills/rs-*/SKILL.md` | `skills/rs-*/SKILL.md` | `.mdc`，仅 description |

## 功能需求

1. **sync**：读取规则源，校验后原子写入各目标；只增删自己生成的文件。
2. **安全护栏**：规则源不存在、没有任何规则、或规则数低于上次的一半时，拒绝写入并保持现状（修复已发生过的"源目录搬走后规则被清空"事故）。
3. **所有权**：每个目标目录记录清单（路径与哈希），只清理清单内与带生成标记的 `rs-` 文件，不碰用户自己的文件。Codex 的 `AGENTS.md` 只改标记区块，区块外内容原样保留。
4. **稳定识别校验**：写入前对每个生成文件做结构校验——无 BOM、LF 换行、frontmatter 可解析、技能名与目录名一致且合法、描述单行且不超 1024 字符、scoped 规则必有 paths、`.mdc` 含 alwaysApply；Codex `AGENTS.md` 不超过 32 KiB（超出会被 Codex 静默截断）。
5. **check**：不写入，重新渲染并与磁盘比对，报告缺失、漂移、结构问题，有问题则退出码非零。
6. **lint**：检查规则源（缺 frontmatter、BOM/CRLF、alwaysApply 非法值、重复 id、悬空引用），`--dups` 报告跨规则的重复行，`--fix` 只做格式规范化。
7. **list**：列出规则及其类别。
8. **import**：把 `.md`、`.txt`、`.cursorrules` 等普通文档转成规则源 `.mdc`，默认按需类别，校验失败回滚。
9. **Hook 开关**：三个 hook 可在配置文件里单独关闭，配置损坏时保持开启。
10. **快照与回滚**：sync 前对有变化的规则源自动存快照，可列出、可回滚，历史在 iCloud 之外。
11. **doctor**：一键体检环境、规则源、各目标和插件状态；**stats**：按类别统计规则体积；**launchd**：可选的变更监听，仅在用户主动安装时生效。
12. **同步状态**：记录上次同步结果，失败时路由钩子每轮提示。
13. **插件**：会话启动自动 sync；附带 Java 守卫、规则路由、结束前编译闸门三个 hook 和 `java-plan`、`rule-hub` 两个技能。

## 非功能需求

- 仅依赖 Python 3.9+ 标准库，无需安装。
- 写入原子化（临时文件加替换），幂等：重复 sync 无变化。
- sync 失败不得留下半成品；任何错误都以非零退出码和明确信息报告；在钩子里运行时失败必须让用户看见。
- 单个目标不可写（如固态未挂载）不得影响其它目标。
- 钩子精确度：规则路由不得被无关词误触发，结束闸门只对 Maven/Gradle 工程里的 Java 生效。

## 验收标准

- 对用户真实规则源运行 sync，Claude Code 新会话能列出全部常驻规则与技能。
- 模拟规则源缺失，sync 退出码 2 且已有规则不变。
- 手改一个生成文件后 `check` 报漂移，`sync` 后恢复。
- 单元测试全部通过。
