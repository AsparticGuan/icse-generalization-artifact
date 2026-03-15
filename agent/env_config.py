"""agent/env_config.py — 集中管理 Agent 所需的全部环境变量 / 配置项。

仅需一个 agent/.env 即可完全替代 init_agent_env.sh + init_env.sh。

配置来源（优先级从高到低）：
1. 系统环境变量（export LAB_xxx=...）
2. agent/.env 文件（python-dotenv 自动加载，不覆盖已有环境变量）
3. 代码内默认值

用法:
    from env_config import cfg

    print(cfg.llm_model)        # → "gpt-4o"
    print(cfg.step_limit)       # → 30
    print(cfg.fix_dir)          # → 自动根据模型名生成
"""

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ── 路径常量 ──
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent

# ── 自动加载 .env（python-dotenv 为可选依赖）──
try:
    from dotenv import load_dotenv

    # agent/.env 优先，项目根 .env 兜底；override=False 不覆盖已有环境变量
    load_dotenv(_THIS_DIR / ".env", override=False)
    load_dotenv(_PROJECT_ROOT / ".env", override=False)
except ImportError:
    pass


# ── 辅助读取函数 ──


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    v = os.environ.get(key)
    return int(v) if v is not None else default


def _env_float(key: str, default: float) -> float:
    v = os.environ.get(key)
    return float(v) if v is not None else default


def _setdefault(key: str, value: str) -> None:
    """写入 os.environ（仅当尚未设置时），供 mini-swe-agent 等外部库读取。"""
    if key not in os.environ:
        os.environ[key] = value


def _append_timestamp_suffix(path: str, ts: str) -> str:
    """为路径最后一段追加时间戳后缀（若已包含则保持不变）。"""
    p = Path(path)
    name = p.name
    if ts in name:
        return str(p)
    return str(p.with_name(f"{name}-{ts}"))


# ── 配置数据类 ──


@dataclass
class AgentConfig:
    """Agent 运行所需的全部配置，一次性从环境读取。

    字段与环境变量的映射见各 field 的 default_factory。
    frozen=False 以允许运行期按需覆盖（如 per-issue LLVM 路径）。
    """

    # ── LLM ──
    llm_token: str = field(default_factory=lambda: _env("LAB_LLM_TOKEN"))
    llm_url: str = field(
        default_factory=lambda: _env("LAB_LLM_URL", "https://api.openai.com/v1")
    )
    llm_model: str = field(default_factory=lambda: _env("LAB_LLM_MODEL", "gpt-4o"))
    llm_basemodel_cutoff: str = field(
        default_factory=lambda: _env("LAB_LLM_BASEMODEL_CUTOFF", "1970-12-31Z")
    )
    llm_temperature: float = field(
        default_factory=lambda: _env_float("LAB_LLM_TEMP", 0.6)
    )

    # ── Agent 行为 ──
    step_limit: int = field(
        default_factory=lambda: _env_int("LAB_AGENT_STEP_LIMIT", 30)
    )
    cost_limit: float = field(
        default_factory=lambda: _env_float("LAB_AGENT_COST_LIMIT", 5.0)
    )
    max_build_jobs: int = field(
        default_factory=lambda: _env_int("LAB_MAX_BUILD_JOBS", os.cpu_count())
    )

    # ── mini-swe-agent 全局安全限制 ──
    mswea_global_cost_limit: float = field(
        default_factory=lambda: _env_float("MSWEA_GLOBAL_COST_LIMIT", 10.0)
    )
    mswea_global_call_limit: int = field(
        default_factory=lambda: _env_int("MSWEA_GLOBAL_CALL_LIMIT", 0)
    )

    # ── 核心路径 ──
    project_root: Path = field(default_factory=lambda: _PROJECT_ROOT)
    run_timestamp: str = field(
        default_factory=lambda: _env(
            "LAB_RUN_TIMESTAMP", datetime.now().strftime("%Y%m%d-%H%M%S")
        )
    )
    llvm_dir: str = field(
        default_factory=lambda: _env(
            "LAB_LLVM_DIR", str(_PROJECT_ROOT / "utils" / "llvm" / "llvm-project")
        )
    )
    llvm_build_dir: str = field(
        default_factory=lambda: _env("LAB_LLVM_BUILD_DIR", str(_PROJECT_ROOT / "build"))
    )
    dataset_dir: str = field(default_factory=lambda: str(_PROJECT_ROOT / "dataset"))
    localize_output: str = field(default_factory=lambda: _env("LAB_LOCALIZE_OUTPUT"))

    # ── 外部工具路径 ──
    alive_tv: str = field(
        default_factory=lambda: _env(
            "LAB_LLVM_ALIVE_TV",
            str(_PROJECT_ROOT / "utils" / "alive2" / "build" / "alive-tv"),
        )
    )
    cost_tool: str = field(
        default_factory=lambda: _env(
            "LAB_LLVM_COST_TOOL", str(_PROJECT_ROOT / "utils" / "cost" / "cost")
        )
    )
    tools_dir: str = field(default_factory=lambda: str(_THIS_DIR / "tools"))

    # ── 输出目录（延迟计算） ──
    fix_dir: str = ""
    traj_dir: str = ""

    def __post_init__(self):
        # 输出目录：优先环境变量，否则根据模型名自动生成
        if not self.fix_dir:
            self.fix_dir = _env(
                "LAB_FIX_DIR_AGENT",
                str(
                    self.project_root
                    / "results"
                    / "agent"
                    / f"fixes-{self.llm_model}-agent"
                ),
            )
        if not self.traj_dir:
            self.traj_dir = _env(
                "LAB_TRAJ_DIR_AGENT",
                str(
                    self.project_root
                    / "results"
                    / "agent"
                    / f"traj-{self.llm_model}-agent"
                ),
            )

        # 为输出目录名统一追加时间戳，避免覆盖并便于追踪。
        self.fix_dir = _append_timestamp_suffix(self.fix_dir, self.run_timestamp)
        self.traj_dir = _append_timestamp_suffix(self.traj_dir, self.run_timestamp)

        # mini-swe-agent 通过 os.environ 读取全局限制，确保默认值写入
        _setdefault("MSWEA_GLOBAL_COST_LIMIT", str(self.mswea_global_cost_limit))
        _setdefault("MSWEA_GLOBAL_CALL_LIMIT", str(self.mswea_global_call_limit))
        _setdefault("LAB_RUN_TIMESTAMP", self.run_timestamp)

        # 与 pipeline/generate.py 对齐：固定使用仓库 dataset/。
        os.environ["LAB_DATASET_DIR"] = self.dataset_dir


# ── 模块级单例：import 即可用 ──
cfg = AgentConfig()
