# rule-hub

Codex、Claude、Cursor共用规则体系插件。

单一规则源（`.mdc`）生成 Claude Code、Codex、Cursor 各自能稳定识别的格式，并在写入前后校验。会话启动自动同步，附带 Java 守卫、规则路由和结束前编译闸门三个 hook。

安装：`claude plugin marketplace add <本目录>`，再 `claude plugin install rule-hub@rule-hub`。命令与格式见 `docs/API.md`，需求见 `docs/PRD.md`，测试用 `python3 -m unittest discover -s tests`。

## 规则原文

`rules-source/` 是规则原文的公开副本，由 `rulehub export rules-source` 从本机规则源生成：项目名已通用化，导出时扫描凭证，扫到就拒绝。`extra/` 下是规则源之外的附加文件（Claude 适配层、Cursor 粘贴稿）。请改规则源后重新导出，不要手改这个目录：`rulehub export rules-source --extra ~/.claude/rules/personal-rules.md --extra ~/.claude/rule-system/USER-RULES-NO-FIGHT-PASTE.txt`。
