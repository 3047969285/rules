"""钩子公共入口 把插件根目录加入路径后复用 rulehub 包里的配置与记录函数"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rulehub.config import enabled, in_scope, log_violation, new_text, reviewers  # noqa: E402,F401
