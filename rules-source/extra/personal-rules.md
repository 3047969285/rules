# 规则体系适配层

规则原文全部来自 iCloud 的 ~/Library/Mobile Documents/com~apple~CloudDocs/Documents/规则体系（.mdc，桌面和文稿已开 iCloud 同步，桌面上看不到是正常的），由 rule-hub 插件（源码 ~/项目/rule-hub）转成 Claude Code、Codex、Cursor 各自格式，每次新会话启动自动同步并校验。改规则只改这份原文。找不到源目录、里面没有规则、源有错误或规则数骤降时，工具拒绝写入，已有规则保持不变。排查用 rule-hub 技能（check、lint、sync）。

## 优先级与冲突
系统/安全政策 > 用户本轮明确要求 > 规则体系 > 自动记忆里的一般偏好。以下情况按用户已明确的偏好处理：
- 「拿不定先问用户」「复杂任务停下问」：用户记忆有「不再请示」，项目工作直接决策推进，只在会改变结果且无法自行判断的未知项上才问。
- cost-effective-models 是 Cursor 选模型规则：在这里按记忆「模型分工」执行（Opus 规划，haiku 优先 / sonnet 执行）。
- agent-exec-minimal 里 PowerShell/.bat 条款只适用 Windows；本机是 macOS。
- Java 仓库验证 = `mvn compile`（java-11）；仓库已有测试再按 craft-excellence 跑全套，不新增测试。
- 提问扩写、竞争式协同：「内部」完成，不贴回聊天；子代理由本代理裁决，不开多模型会审。

## 触发地图（rule-manifest.json 的路由在这里落地）
| 类别 | 机制 | 内容 |
|---|---|---|
| 常驻 | rules/rs-*.md 无 paths | core-digest、craft-excellence、code-first-facts、reply-style-core、security-confidentiality、competitive-collab、prompt-optimize、cost-effective-models |
| 碰 `*.java` `*.xml` | rules paths | java-01…11、architect-stance、solution-explanation-detail、reasoning-integrity |
| 碰 `*.docx` `*.xls` `*.xlsx` | rules paths | reasoning-integrity（见下方项目模板规则） |
| 按任务调用 | skills rs-* | quality-gate-advisor（实际交付前）、agent-exec-minimal（用户说直接做/改工程/跑通）、java-dev-standards（规则不够用的完整参考）、ruoyi-java-backend、chinese-only、00-rule-index、00-force-all-rules（规则维护或路由不清）、reply-style-girlfriend-pro 与 01-allow-explicit-intimacy（用户亲密口吻时） |
| 编码前流程 | skill java-plan | Java 新功能的需求扩写与四方案 |
| 硬校验 | rule-hub 插件 hooks | 写 Java/XML 后扫描违规；写测试文件、新建 yml 前询问用户；结束前未 mvn 编译则拦截 |

遇到上表「按任务调用」的情形，先用 Skill 工具加载对应 rs-* 技能再动手，不凭印象执行。

## 项目模板规则（rule-manifest.projectRules）
处理 example-project 下 docx/xls/xlsx、模板、源文件、结果样例或项目文档任务前，必须读项目内 `.cursor/rules/template-source-result-accuracy.mdc`；不存在则读 `~/.cursor/project-rules-hold/example-project/template-source-result-accuracy.mdc`。目前这两处在本机都不存在：遇到此类任务要告诉用户缺这份规则并暂停，不要猜。

## 参考文件
~/.claude/rule-system/ 下有 rule-manifest.json、00-先看这里.md 和 USER-RULES-NO-FIGHT-PASTE.txt 原文。
