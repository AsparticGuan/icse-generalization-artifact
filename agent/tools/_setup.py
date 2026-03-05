"""agent/tools/_setup.py — 工具脚本的公共初始化模块。

所有 CLI 工具脚本在 import lab_env / llvm_helper 之前先 import 本模块，
以确保 sys.path 和环境变量就绪。
"""

import os
import sys

# 定位项目根目录（agent/tools/_setup.py -> agent/tools -> agent -> 项目根）
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))

# 把 scripts/ 加入 sys.path，使得 import llvm_helper / lab_env 可用
_scripts_dir = os.path.join(PROJECT_ROOT, "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

# 同时也把 pipeline/ 加入，以便复用 generate.py 中的工具函数
_pipeline_dir = os.path.join(PROJECT_ROOT, "pipeline")
if _pipeline_dir not in sys.path:
    sys.path.insert(0, _pipeline_dir)

# 把 agent/ 自身也加入，方便相互 import
_agent_dir = os.path.join(PROJECT_ROOT, "agent")
if _agent_dir not in sys.path:
    sys.path.insert(0, _agent_dir)


def get_env_or_die(key: str) -> str:
    """获取环境变量，缺失时直接报错退出。"""
    val = os.environ.get(key)
    if not val:
        print(
            f"ERROR: 环境变量 {key} 未设置。请配置 agent/.env（见 .env.example）。",
            file=sys.stderr,
        )
        sys.exit(1)
    return val
