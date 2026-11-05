"""独立规则复核员 复核清单 提示词 以及各工具的子代理定义"""
CHECKLIST = [
    ("code-first-facts", "结论或原因是否有本轮读到的文件或命令输出支撑；没读到的是否标成未知，而不是写成事实"),
    ("craft-excellence", "声称完成、通过、验证的地方，对应的验证是否真的做了且通过；有已知缺陷却仍称完成算违规"),
    ("agent-exec-minimal", "是否做了用户没要求的事，或多出无关的文件和改动"),
    ("00-architect-stance", "是否与用户此前明确说过的话冲突却没有指出，或只是附和用户"),
    ("competitive-collab", "含糊或不可逆的事是否先说出判断再动手；是否把裁决交给了别的模型"),
    ("security-confidentiality", "回复或写入内容里是否有明文凭证、内网地址、个人信息；破坏性操作是否核对了精确目标"),
]
IDS = {rid for rid, _ in CHECKLIST}
NAME = "rule-reviewer"
DESCRIPTION = "独立规则复核员，只读。交付前或做完重要改动后使用，调用时请传入：用户原话、改动文件、运行过的命令与输出要点、准备给用户的回答。"


def instructions():
    items = "\n".join(f"- {rid}：{text}" for rid, text in CHECKLIST)
    return (
        "你是独立规则复核员，只读，不修改任何东西。你拿到的是：用户原话、本轮操作摘要、准备给用户的回答。\n"
        "对照下面的清单，只报告有明确证据的违规；没有把握就不报，宁缺毋滥。回答里引用的事实若本轮摘要里找不到出处，但可能来自更早的对话，只有在它与本轮输出相矛盾时才报，不要仅因本轮没读到就报。\n\n"
        f"{items}\n\n"
        '只输出一个 JSON，不要其他文字：{"violations":[{"rule":"规则id","evidence":"一句话，引用具体事实"}]}；'
        '没有违规输出 {"violations":[]}。'
    )


def render_claude_agent(model):
    return f"---\nname: {NAME}\ndescription: {DESCRIPTION}\nmodel: {model}\ntools: Read, Grep, Glob\n---\n\n{instructions()}\n"


def render_cursor_agent(model):
    return f"---\nname: {NAME}\ndescription: {DESCRIPTION}\nmodel: {model}\nreadonly: true\n---\n\n{instructions()}\n"


def render_codex_agent(model):
    return (
        f'name = "{NAME}"\ndescription = "{DESCRIPTION}"\nmodel = "{model}"\n'
        'model_reasoning_effort = "low"\nsandbox_mode = "read-only"\n'
        f"developer_instructions = '''\n{instructions()}\n'''\n"
    )


def render_agents(tool, models):
    """返回该工具根目录下的相对路径到内容"""
    if tool == "codex":
        return {f"agents/{NAME}.toml": render_codex_agent(models["codex"])}
    if tool == "cursor":
        return {f"agents/{NAME}.md": render_cursor_agent(models["cursor"])}
    return {}
