"""后端开发流水线 任务目录 阶段状态 产物校验 红绿记录 阶段评审 以及写源码前的阶段闸门"""
import hashlib
import json
import os
import re
import subprocess
import time

from .config import config, enabled, log_violation, reviewers
from . import review_run
from .review_run import ask_reviewer, extract_json

DEFAULT_ROOT = "/Volumes/Games/Projects/DevDocs"
MARKERS = ("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle")
STAGES = {"小": ["prd", "tests", "impl", "final"], "标准": ["prd", "api", "tests", "impl", "final"]}
ARTIFACT = {"prd": "prd.md", "api": "api.md", "tests": "test-plan.md"}
PRD_SECTIONS = {
    "标准": ["背景与目标", "功能范围", "涉及模块", "入参出参", "业务规则", "非功能要求", "约束与前置条件", "待确认点"],
    "小": ["背景与目标", "功能范围", "业务规则", "待确认点"],
}
API_SECTIONS = ["接口清单", "新增与修改文件清单", "事务与并发", "错误码与异常"]
API_WORDS = ["路径", "方法", "参数", "校验", "响应", "状态码", "鉴权", "幂等"]
DIMENSIONS = ["正常", "边界", "异常", "权限", "并发", "幂等", "数据一致性", "安全", "性能"]
REQUIRED_DIMS = {"小": DIMENSIONS[:3], "标准": DIMENSIONS}
PLACEHOLDER = "（待补全）"
PROTECTED = ("state.json", "red.json", "green.json")
CHECKS = {
    "prd": ["需求是否具体可验收，没有臆造用户没提供的信息", "待确认点是否真实列出了会改变结果的未知项", "范围是否与用户原话一致，没有扩范围", "非功能要求与约束是否合理"],
    "api": ["接口字段是否完整（路径、方法、参数、校验、响应、状态码、鉴权、幂等）", "是否覆盖需求里的全部业务规则",
            "是否遵守 Controller→Service→ServiceImpl→Mapper→XML 分层", "文件清单是否最小，是否复用已有能力而不是新建", "事务、并发、异常处理是否明确"],
    "tests": ["测试是否覆盖需求的业务规则和每个接口", "各维度用例是否有实质内容，不适用是否给了理由",
              "红阶段失败的原因是缺少实现，而不是测试本身写错（看红的输出）", "测试是否可重复，没有依赖外部环境的脆弱断言"],
    "impl": ["实现是否只改了清单内文件，没有越界", "是否遵守分层、异常、日志、事务规范", "测试是否由绿证明通过，且没有被改弱", "有无遗漏的业务规则或异常路径"],
    "final": ["需求、接口、测试、实现是否一致", "各阶段评审是否都通过且没有遗留问题", "有没有把待确认点默默当成已决定", "声明的难度是否与实际改动规模相符"],
}


class TaskError(Exception):
    pass


def devdocs_root():
    return os.path.expanduser(os.environ.get("RULEHUB_DEVDOCS") or config().get("devdocs_root") or DEFAULT_ROOT)


def cache_dir():
    return os.environ.get("RULEHUB_CACHE") or os.path.expanduser("~/.cache/rulehub")


def project_root(path):
    d = os.path.dirname(os.path.abspath(path)) if not os.path.isdir(path) else os.path.abspath(path)
    while d != os.path.dirname(d):
        if any(os.path.exists(os.path.join(d, m)) for m in MARKERS):
            return d
        d = os.path.dirname(d)
    return None


def root_available():
    root = devdocs_root()
    return os.path.isdir(root) or os.path.isdir(os.path.dirname(root))


def load_state(tpath):
    with open(os.path.join(tpath, "state.json"), encoding="utf-8") as f:
        return json.load(f)


def save_state(tpath, state):
    with open(os.path.join(tpath, "state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tasks_of(project):
    base = os.path.join(devdocs_root(), os.path.basename(os.path.realpath(project)))
    names = sorted((n for n in os.listdir(base) if os.path.isfile(os.path.join(base, n, "state.json"))), reverse=True) if os.path.isdir(base) else []
    return [os.path.join(base, n) for n in names]


def active_task(project):
    return next((t for t in tasks_of(project) if not load_state(t).get("done")), None)


def sections(text):
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"##\s+(.*\S)\s*$", line)
        if m:
            cur = m.group(1)
            out[cur] = []
        elif cur is not None:
            out[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def filled(body):
    return bool(body) and PLACEHOLDER not in body


def validate_prd(level, text):
    sec = sections(text)
    return [f"需求文档缺少或未填写「{h}」" for h in PRD_SECTIONS[level] if not filled(sec.get(h, ""))]


def manifest(text):
    entries = []
    for line in sections(text).get("新增与修改文件清单", "").splitlines():
        m = re.match(r"\s*[-*]\s*[`\s]*([\w./$-]+)", line)
        if m and PLACEHOLDER not in line:
            entries.append(m.group(1))
    return entries


def validate_api(text):
    sec = sections(text)
    problems = [f"接口文档缺少或未填写「{h}」" for h in API_SECTIONS if not filled(sec.get(h, ""))]
    problems += [f"接口文档里没有出现「{w}」，接口要素不全" for w in API_WORDS if w not in text]
    if not manifest(text):
        problems.append("「新增与修改文件清单」里至少要有一个文件，用 - 开头逐行列出")
    return problems


def validate_tests(level, text):
    sec, problems = sections(text), []
    for dim in REQUIRED_DIMS[level]:
        body = sec.get(dim)
        if body is None:
            problems.append(f"测试计划缺少「## {dim}」维度")
            continue
        cases = [l for l in body.splitlines() if l.strip().startswith("-") and PLACEHOLDER not in l]
        reason = re.search(r"不适用[:：]?\s*(\S.{3,})", body)
        if not cases and not reason:
            problems.append(f"测试计划「{dim}」维度既没有用例，也没有写明不适用的理由")
    return problems


def validate(level, stage, tpath):
    if stage not in ARTIFACT:
        return []
    text = read(os.path.join(tpath, ARTIFACT[stage]))
    if stage == "prd":
        return validate_prd(level, text)
    if stage == "api":
        return validate_api(text)
    if stage == "tests":
        return validate_tests(level, text)
    return []


TEMPLATES = {"prd": lambda level: "# 需求文档\n\n" + "".join(f"## {h}\n{PLACEHOLDER}\n\n" for h in PRD_SECTIONS[level]),
             "api": lambda level: "# 接口文档\n\n" + "".join(f"## {h}\n{PLACEHOLDER}\n\n" for h in API_SECTIONS)
             + "要素提示：每个接口写明 路径、方法、参数、校验、响应、状态码、鉴权、幂等。文件清单用 - 逐行列出路径。\n",
             "tests": lambda level: "# 测试计划\n\n" + "".join(f"## {d}\n{PLACEHOLDER}\n\n" for d in REQUIRED_DIMS[level])
             + "写法：每个维度用 - 逐行列出用例；确实不适用时写「不适用：理由」。\n"}


def make_task_dir(proj, slug):
    """目录名带时间 同一秒重名时加序号 绝不覆盖"""
    base = os.path.join(devdocs_root(), os.path.basename(proj), time.strftime("%Y%m%d-%H%M%S") + "-" + slug)
    for i in range(1, 100):
        path = base if i == 1 else f"{base}-{i}"
        try:
            os.makedirs(path)
            return path
        except FileExistsError:
            continue
    raise TaskError("无法创建任务目录")


def new_task(project, name, level, reason):
    if level not in STAGES:
        raise TaskError(f"难度只能是 小 或 标准，极简不需要任务，用户要求直接做用 skip；收到 {level!r}")
    if not reason.strip():
        raise TaskError("必须写出难度判断的理由 --reason")
    proj = project_root(project)
    if not proj:
        raise TaskError(f"{project} 不在 Maven 或 Gradle 工程里")
    if not root_available():
        raise TaskError(f"固态文档目录不可用: {devdocs_root()}")
    if active_task(proj):
        raise TaskError(f"该项目还有未完成的任务 {active_task(proj)}，先完成它")
    slug = re.sub(r"[^A-Za-z0-9-]+", "-", name).strip("-") or "task-" + sha(name)[:6]
    tpath = make_task_dir(proj, slug)
    for stage in STAGES[level]:
        if stage in ARTIFACT:
            with open(os.path.join(tpath, ARTIFACT[stage]), "w", encoding="utf-8") as f:
                f.write(TEMPLATES[stage](level))
    save_state(tpath, {"level": level, "reason": reason, "project": proj, "name": name, "stages": [], "done": False,
                       "created": time.time(), "files_written": [], "last_source_write": 0, "reviews": {}})
    return tpath


def skip_task(project, reason):
    proj = project_root(project)
    if not proj or not reason.strip() or not root_available():
        raise TaskError("需要在工程里执行、写明用户要求直接做的原因，且固态文档目录可用")
    tpath = make_task_dir(proj, "skip")
    save_state(tpath, {"level": "跳过", "reason": reason, "project": proj, "name": "skip", "stages": [], "done": False,
                       "created": time.time(), "files_written": [], "last_source_write": 0, "reviews": {}})
    log_violation("stage_gate", "java-12-pipeline", "用户要求直接做 跳过流水线: " + reason[:80])
    return tpath


def next_stage(state):
    return next((s for s in STAGES[state["level"]] if s not in state["stages"]), None)


def stamp(tpath, state, stage):
    if stage in ARTIFACT:
        return sha(read(os.path.join(tpath, ARTIFACT[stage])))
    return str(state["last_source_write"])


def record_run(tpath, kind, cmd, cwd):
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        raise TaskError(f"命令超过 900 秒: {cmd}")
    out = (r.stdout + r.stderr)[-1500:]
    if kind == "red" and r.returncode == 0:
        raise TaskError("红阶段要求测试先失败，但命令成功了：说明测试没有覆盖新行为，或实现已经存在。请先让测试因缺少实现而失败。")
    if kind == "green" and r.returncode != 0:
        raise TaskError("绿阶段要求全套测试通过，但命令失败了：\n" + out)
    with open(os.path.join(tpath, kind + ".json"), "w", encoding="utf-8") as f:
        json.dump({"cmd": cmd, "exit": r.returncode, "tail": out, "time": time.time()}, f, ensure_ascii=False, indent=1)
    return r.returncode, out


def run_record(tpath, kind):
    try:
        return json.loads(read(os.path.join(tpath, kind + ".json")))
    except ValueError:
        return None


def git_context(proj):
    def git(*a):
        try:
            return subprocess.run(["git", "-C", proj, *a], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return ""
    return git("status", "--short")[:1500], git("diff", "HEAD")[:8000]


def stage_prompt(tpath, state, stage):
    parts = [f"任务: {state['name']}　难度: {state['level']}　难度理由: {state['reason']}"]
    for s in ("prd", "api", "tests"):
        if s in STAGES[state["level"]] and (s == stage or STAGES[state["level"]].index(s) < STAGES[state["level"]].index(stage)):
            parts.append(f"=== {ARTIFACT[s]} ===\n{read(os.path.join(tpath, ARTIFACT[s]))[:6000]}")
    for kind in ("red", "green"):
        rec = run_record(tpath, kind)
        if rec and stage in ("tests", "impl", "final"):
            parts.append(f"=== {kind} 记录 ===\n命令 {rec['cmd']} 退出码 {rec['exit']}\n{rec['tail'][-800:]}")
    if stage == "final":
        done = [f"{s}: {state['reviews'][s]['verdict']}（{state['reviews'][s]['model']}）" for s in STAGES[state["level"]] if s in state["reviews"] and s != stage]
        parts.append("=== 各阶段评审结论 ===\n" + ("\n".join(done) or "无"))
    if stage in ("impl", "final"):
        status, diff = git_context(state["project"])
        parts.append(f"=== 工程改动 git status ===\n{status}\n=== git diff HEAD（截断）===\n{diff}")
    items = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(CHECKS[stage]))
    return ("你是后端开发流水线的阶段评审员，只读。对照清单评审下面的产物，只报告有明确证据的问题。\n"
            f"评审阶段: {stage}\n清单:\n{items}\n\n"
            '只输出一个 JSON：{"verdict":"pass 或 fail","findings":[{"rule":"清单序号","evidence":"一句话引用事实"}]}；'
            "有任何具体问题 verdict 必须是 fail，没有明确问题才是 pass。\n\n" + "\n\n".join(parts))


def review(tpath, stage):
    state = load_state(tpath)
    if stage != next_stage(state):
        raise TaskError(f"现在该评审的阶段是 {next_stage(state)}，不是 {stage}")
    problems = validate(state["level"], stage, tpath)
    if problems:
        raise TaskError("产物还不完整，先补全再评审:\n- " + "\n- ".join(problems))
    if stage == "tests" and not (run_record(tpath, "red") or {}).get("exit"):
        raise TaskError("测试阶段需要先有红的记录：rulehub task red --cmd \"<测试命令>\"")
    if stage in ("impl", "final"):
        green = run_record(tpath, "green")
        if not green or green["exit"] != 0 or green["time"] < state["last_source_write"]:
            raise TaskError("需要在最后一次修改源码之后有一次绿的全套测试记录：rulehub task green --cmd \"<测试命令>\"")
    if not enabled("stage_review"):
        rec = {"verdict": "skipped", "findings": [], "model": "-"}
    else:
        model = reviewers()["claude"]
        data = extract_json(ask_reviewer(stage_prompt(tpath, state, stage), model, 120)) or {}
        findings = [f for f in data.get("findings", []) if isinstance(f, dict) and str(f.get("evidence", "")).strip()]
        verdict = "pass" if data.get("verdict") == "pass" and not findings else "fail" if data else "unavailable"
        rec = {"verdict": verdict, "findings": findings[:8], "model": model, "error": review_run.last_error if verdict == "unavailable" else ""}
    rec.update(time=time.time(), stamp=stamp(tpath, state, stage))
    state["reviews"][stage] = rec
    save_state(tpath, state)
    os.makedirs(os.path.join(tpath, "reviews"), exist_ok=True)
    with open(os.path.join(tpath, "reviews", stage + ".md"), "w", encoding="utf-8") as f:
        f.write(f"# {stage} 评审：{rec['verdict']}（{rec['model']}）\n\n" + "\n".join(f"- [{x.get('rule', '')}] {x['evidence']}" for x in rec["findings"]) + "\n")
    for x in rec["findings"]:
        log_violation("stage_review", "java-12-pipeline", f"{stage}: {x['evidence']}"[:200])
    return rec


def advance(tpath, stage):
    state = load_state(tpath)
    if state["level"] == "跳过":
        raise TaskError("这是跳过流水线的任务，没有阶段可推进")
    if stage != next_stage(state):
        raise TaskError(f"下一阶段是 {next_stage(state)}，不能直接到 {stage}")
    problems = validate(state["level"], stage, tpath)
    if stage == "tests" and not (run_record(tpath, "red") or {}).get("exit"):
        problems.append("缺少红的记录")
    if stage in ("impl", "final"):
        green = run_record(tpath, "green")
        if not green or green["exit"] != 0 or green["time"] < state["last_source_write"]:
            problems.append("最后一次修改源码之后没有绿的全套测试记录")
    rev = state["reviews"].get(stage)
    if not rev or rev["verdict"] not in ("pass", "skipped"):
        problems.append(f"评审没有通过（当前: {rev['verdict'] if rev else '未评审'}），先运行 rulehub task review {stage}")
    elif rev["stamp"] != stamp(tpath, state, stage):
        problems.append("评审之后产物或源码又改动过，需要重新评审")
    if problems:
        raise TaskError("不能推进:\n- " + "\n- ".join(problems))
    state["stages"].append(stage)
    state["done"] = stage == "final"
    save_state(tpath, state)
    return state


def status_lines(tpath):
    state = load_state(tpath)
    nxt = next_stage(state) if state["level"] != "跳过" else None
    lines = [f"任务 {tpath}", f"难度 {state['level']}（{state['reason']}）", f"已完成 {state['stages'] or '无'}　下一阶段 {nxt or ('已完成' if state['done'] else '无')}"]
    if nxt:
        rev = state["reviews"].get(nxt)
        lines.append(f"下一阶段评审: {rev['verdict'] if rev else '未评审'}；产物问题: {validate(state['level'], nxt, tpath) or '无'}")
    return lines


def is_test_file(path):
    p = path.replace("\\", "/")
    return "/src/test/" in p or bool(re.search(r"(Test|Tests|IT)\.java$", p))


def is_source(path):
    return path.endswith(".java") or (path.endswith(".xml") and "/mapper" in path.replace("\\", "/").lower())


def entry_matches(entry, path):
    p = path.replace("\\", "/")
    return p.endswith(entry.lstrip("./")) or os.path.basename(entry) == os.path.basename(p)


def tests_allowed(path):
    """已有任务且需求或接口阶段已通过 此时写测试文件不必再询问"""
    proj = project_root(path)
    t = active_task(proj) if proj and root_available() else None
    if not t:
        return False
    state = load_state(t)
    return state["level"] == "跳过" or ("prd" if state["level"] == "小" else "api") in state["stages"]


def session_files(session_id, proj):
    path = os.path.join(cache_dir(), "sessions", f"{session_id or 'nosession'}-{sha(proj)[:8]}.json")
    try:
        return path, set(json.loads(read(path)))
    except ValueError:
        return path, set()


def gate_decision(path, session_id=""):
    """写文件前的阶段闸门 返回 (deny, 原因) 或 None 表示不表态"""
    real = os.path.realpath(path)
    root = os.path.realpath(devdocs_root())
    if real.startswith(root + os.sep):
        rel = os.path.relpath(real, root).replace(os.sep, "/")
        if os.path.basename(real) in PROTECTED or "/reviews/" in "/" + rel:
            return "deny", "任务状态、红绿记录和评审记录只能由 rulehub task 命令写入，不能直接修改"
        return None
    if not is_source(path):
        return None
    proj = project_root(path)
    if not proj or not enabled("stage_gate") or not root_available():
        if proj and enabled("stage_gate"):
            log_violation("stage_gate", "java-12-pipeline", "固态文档目录不可用 闸门放行")
        return None
    t = active_task(proj)
    if t is None:
        sf, files = session_files(session_id, proj)
        if real in files or not files:
            os.makedirs(os.path.dirname(sf), exist_ok=True)
            with open(sf, "w", encoding="utf-8") as f:
                json.dump(sorted(files | {real}), f)
            return None
        return "deny", ("本次会话已改过一个源文件，现在要改第二个，说明不是极简改动。请先判断难度并建任务：\n"
                        'rulehub task new --project "' + proj + '" --name <任务名> --level 小|标准 --reason "<难度理由>"\n'
                        '用户明确要求直接做时：rulehub task skip --project "' + proj + '" --reason "<用户原话>"')
    state = load_state(t)
    if state["level"] == "跳过":
        return None
    if is_test_file(path):
        need = "prd" if state["level"] == "小" else "api"
        if need not in state["stages"]:
            return "deny", f"测试要在{'需求' if need == 'prd' else '接口'}阶段通过评审之后才能写。当前任务 {t}\n下一步: rulehub task review {next_stage(state)}"
        return None
    if "tests" not in state["stages"]:
        return "deny", f"先写测试并留下红的记录、通过测试阶段评审，才能写实现。当前下一阶段是 {next_stage(state)}。任务 {t}"
    if state["level"] == "标准":
        entries = manifest(read(os.path.join(t, "api.md")))
        if entries and not any(entry_matches(e, path) for e in entries):
            return "deny", f"{os.path.basename(path)} 不在接口文档的「新增与修改文件清单」里。先更新 api.md 并重新评审，或改用清单内的文件。任务 {t}"
    state["files_written"] = sorted(set(state["files_written"]) | {real})
    state["last_source_write"] = time.time()
    save_state(t, state)
    return None
