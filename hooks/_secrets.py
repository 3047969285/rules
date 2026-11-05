"""凭证识别 密钥守卫与复核打包共用"""
import re

ALLOW = "rulehub:allow-secret"
HARD = [
    ("私钥", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("AWS 访问密钥", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub 令牌", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{50,}\b")),
    ("Slack 令牌", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Anthropic 或 OpenAI 密钥", re.compile(r"\bsk-(?:ant-)?(?=[A-Za-z_-]*\d)[A-Za-z0-9_-]{32,}")),
    ("Google API 密钥", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
]
URI = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@'\"]+:([^\s:@/'\"]{3,})@([^\s/'\"]+)")
JDBC = re.compile(r"jdbc:[^\s'\"]*[?&;]password=([^&;\s'\"]{3,})")
ASSIGN = re.compile(r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?key|token)\b['\"]?\s*[=:]\s*['\"]([^'\"\s]{8,})['\"]")
PLACEHOLDER = re.compile(r"(?i)\{|<[^>]*>|your|example|changeme|placeholder|x{4,}|\*{3,}|process\.env|getenv|environ")
CONFIG_FILE = re.compile(r"(?:^|/)application[\w.-]*\.(?:ya?ml|properties)$")
LOCAL_HOST = re.compile(r"^(?:localhost|127\.|0\.0\.0\.0|\[::1\])")


def mask(value):
    return value[:4] + "***"


def scan(text, config_file=False):
    """返回 (必须拒绝的, 需要询问的) 每项形如 行号: 类型 (前缀***) 配置文件 application 只查固定格式令牌 口令按 java-10 本就该放这里"""
    hard, soft = [], []
    for i, line in enumerate(text.splitlines(), 1):
        if ALLOW in line:
            continue
        for kind, rx in HARD:
            m = rx.search(line)
            if m:
                hard.append(f"{i}: {kind} ({mask(m.group(0))})")
        if config_file:
            continue
        for m in URI.finditer(line):
            if not PLACEHOLDER.search(m.group(1)) and not LOCAL_HOST.match(m.group(2)):
                soft.append(f"{i}: 带密码的连接串 ({mask(m.group(1))})")
        for rx, kind in ((JDBC, "JDBC 密码参数"), (ASSIGN, "疑似明文口令")):
            for m in rx.finditer(line):
                if not PLACEHOLDER.search(m.group(1)):
                    soft.append(f"{i}: {kind} ({mask(m.group(1))})")
    return hard, soft


def redact(text):
    """把固定格式令牌替换掉 用于把内容交给别的模型之前"""
    for _, rx in HARD:
        text = rx.sub("[已脱敏]", text)
    return text
