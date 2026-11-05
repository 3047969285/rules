---
name: rule-hub
description: 维护或排查用户的规则体系时使用，包括同步规则到 Claude Code、Codex、Cursor，检查规则是否被正确生成，检查规则源重复和格式问题
---

规则源是 `.mdc` 目录（默认 iCloud `Documents/规则体系`），改规则只改源，不改生成物。命令在插件目录下，用 `python3 "${CLAUDE_PLUGIN_ROOT}/bin/rulehub"` 调用：

- 同步：`sync`，加 `--dry-run` 只看不写；Cursor 项目规则加 `--target cursor --project <项目目录>`
- 检查生成物是否与源一致且结构合法：`check`，有问题退出码非零
- 检查规则源格式、引用和重复：`lint --dups`，`lint --fix` 只规范化格式
- 查看分类：`list`

排查"规则没生效"的顺序：先 `check`；再看 `~/.claude/rules/` 里是否有 `rs-` 文件；再看 SessionStart 是否跑过 sync。sync 退出码 2 表示护栏触发（源缺失、无规则、源有错误、AGENTS.md 超 32KiB），已有规则保持不变，按报错修源即可。
