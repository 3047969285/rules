#!/usr/bin/env python3
"""UserPromptSubmit 每句话注入规则体系路由与提示词整理骨架 整理由模型按任务自己完成"""
import json
import os
import re
import sys

from _cfg import enabled

JAVA = r"(?<![A-Za-z])[Jj]ava(?![A-Za-z])|\.java\b|Spring ?Boot|[Mm]y[Bb]atis|[Mm]apper"
ROUTES = [
    (r"直接(做|实现|写|改)|改工程|跑通|写脚本|交付可用", "rs-agent-exec-minimal", "用户要求直接实现或交付"),
    (r"交付|提交|发布|上线|验收|做完|完成了吗|review|审查", "rs-quality-gate-advisor", "实际交付前质量闸门"),
    (r"爸爸|老公|宝贝|撒娇|调情|亲密|骚|涩|女朋友", "rs-reply-style-girlfriend-pro", "用户用亲密口吻"),
    (r"爸爸|老公|宝贝|撒娇|调情|亲密|骚|涩", "rs-01-allow-explicit-intimacy", "成人亲密表达边界"),
    (r"规则体系|规则索引|规则维护|\.mdc|rule-manifest|force-all", "rs-00-rule-index 和 rs-00-force-all-rules", "规则维护或路由不清"),
    (r"(?s)(?=.*(?:" + JAVA + r"))(?=.*(?:写|改|实现|开发|新增|添加|修复|重构|做个|加个|需求|功能)).*", "rule-hub:backend-flow", "Java 后端开发：先判难度，再按需求、接口、测试、实现、终审推进，文档存固态 DevDocs"),
    (r"PlagiarismChecking|黄岛|(模板|template).{0,10}(docx|xlsx?|文档)|(docx|xlsx?).{0,10}(模板|template)", "项目模板规则", "先读 template-source-result-accuracy.mdc；本机缺失则告知用户并暂停"),
]
BASE = (
    "【规则体系路由】本轮先分类：简单问答直接答；只读查看不触发执行技能；实际变更或交付走完整流程；复杂任务自己多角度审查并裁决再动手。"
    "交付前必须跑仓库全套测试，Java 另须 mvn compile 0 错误；证据不足不得声称完成。"
    "你是资深专家，不是执行器；身份随任务自己判断，不固定。先判断这句话的真实意图，与用户此前明确说过的话冲突、含糊或不可逆时，先说出判断，再按最可能且可撤回的理解去做。"
)


def sync_warning():
    path = os.environ.get("RULEHUB_STATE") or os.path.expanduser("~/.config/rulehub/state.json")
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return ""
    return "" if st.get("ok", True) else "\n【注意】上次规则同步失败，当前规则可能不是最新: " + str(st.get("message", ""))[:120]


def scaffold():
    return (
        "\n【提示词整理】按下面结构在内部整理本句再执行，默认不贴回聊天；简单问题只留 #task。\n"
        "#role: （你判断：随任务变化，只选本任务最贴切的一个专家身份）\n#task: （你补全：结果、对象、完成标准，补不上的列待确认）\n"
        "#约束: （你补全：安全、保密、本轮明确禁止、命中规则里的硬性要求、会改变结果的未知项）"
    )


def main():
    if not enabled("rule_router"):
        return
    data = json.load(sys.stdin)
    prompt = data.get("prompt", "")
    hits, seen = [], set()
    for pat, skill, why in ROUTES:
        if re.search(pat, prompt) and skill not in seen:
            seen.add(skill)
            hits.append((skill, f"- 先用 Skill 工具加载 {skill}（{why}），再动手"))
    msg = BASE + ("\n命中按任务技能:\n" + "\n".join(h[1] for h in hits) if hits else "")
    msg += scaffold() + sync_warning()
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": msg}}, ensure_ascii=False))


main()
