---
name: backend-flow
description: Java 后端开发任务一开始就使用。先判断难度，再按需求、接口、测试、实现、终审的流水线推进，每阶段由子代理完成并由只读评审员评审，文档存固态 DevDocs
---

规则见 java-12-pipeline。你是编排者和裁决者，不亲手写各阶段产物。`rulehub` 命令在 PATH 里，没有时用 `python3 "${CLAUDE_PLUGIN_ROOT}/bin/rulehub"`。

## 1. 判断难度，并对用户说出判断和理由
- **极简**：单文件、不涉及接口、数据、事务，改动很小。直接改，不建任务。
- **小**：单模块，不新增接口或表。流程：需求、测试、实现、终审。
- **标准**：新增或改动接口、跨模块、数据库、事务或并发、对外对接。流程：需求、接口、测试、实现、终审。
- 用户明确说「直接做」：`rulehub task skip --project <项目目录> --reason "<用户原话>"`。
- 拿不准就往高一档判；机器也会在你改到第二个源文件时拦下。

## 2. 建任务（小和标准）
`rulehub task new --project <项目目录> --name <任务名> --level 小|标准 --reason "<难度理由>"`，会在固态 DevDocs 下生成任务目录和产物模板。
标准难度先按 java-plan 出四方案让用户选，选完再继续。

## 3. 逐阶段推进，每阶段都是同一套动作
调用对应子代理（传入任务目录、项目目录、用户原话）→ `rulehub task review <阶段>` → 通过后 `rulehub task advance <阶段>`。评审不通过：把意见交回子代理改，再评审。评审意见由你裁决，认为不属实就在回复里写明理由并请用户定夺，不要绕过命令。

| 阶段 | 子代理 | 额外要求 |
|---|---|---|
| prd | rule-hub:backend-prd-writer | 待确认点里有会改变结果的未知项，先问用户 |
| api（标准） | rule-hub:backend-api-designer | 文件清单要最小 |
| tests | rule-hub:backend-test-writer | 测试写完后 `rulehub task red --cmd "<测试命令>"`，必须因缺少实现而失败 |
| impl | rule-hub:backend-implementer | 完成后 `rulehub task green --cmd "<全套测试命令>"`，并确认 mvn compile 0 错误 |
| final | 你 | review final 前，再用 rule-hub:rule-reviewer 复核一次 |

## 4. 收尾
`rulehub task advance final` 后，向用户汇报任务目录路径、各阶段评审结论、测试结果。
机器闸门拒绝你写文件时，按提示补做前一步，不要尝试绕过。
