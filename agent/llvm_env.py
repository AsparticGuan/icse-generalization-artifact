"""llvm_env.py — LLVM 开发专用执行环境（继承 mini-swe-agent LocalEnvironment）。

针对 LLVM InstCombine 修复场景做适配：
- cwd 设为 issue 专属 LLVM 克隆目录
- 注入构建/测试/工具路径等环境变量
- 延长超时（LLVM 构建耗时较长）
"""

import os
from minisweagent.environments.local import LocalEnvironment
from env_config import cfg


class LLVMEnvironment(LocalEnvironment):
    """LLVM InstCombine 修复专用执行环境。

    在 LocalEnvironment 基础上：
    1. 设置 cwd 为 issue 对应的 LLVM 源码目录
    2. 注入 agent/tools/ 脚本所需的全部环境变量
    3. 超时设为 600s（LLVM 增量构建通常在数分钟内完成）
    """

    def __init__(
        self,
        *,
        issue_id: str,
        issue_llvm_dir: str,
        llvm_build_dir: str,
        tools_dir: str | None = None,
        timeout: int = 600,
        extra_env: dict[str, str] | None = None,
    ):
        os.makedirs(cfg.tmp_dir, exist_ok=True)

        # 构建传给工具脚本的环境变量映射（从 cfg 集中读取，不再散落 os.environ.get）
        env_vars: dict[str, str] = {
            # 核心路径
            "AGENT_ISSUE_ID": issue_id,
            "LAB_LLVM_DIR": issue_llvm_dir,
            "LAB_LLVM_BUILD_DIR": llvm_build_dir,
            # 从集中配置继承
            "LAB_DATASET_DIR": cfg.dataset_dir,
            "LAB_TMP_DIR": cfg.tmp_dir,
            "TMPDIR": cfg.tmp_dir,
            "LAB_LLVM_ALIVE_TV": cfg.alive_tv,
            "LAB_LLVM_COST_TOOL": cfg.cost_tool,
            "LAB_LLM_BASEMODEL_CUTOFF": cfg.llm_basemodel_cutoff,
            "LAB_MAX_BUILD_JOBS": str(cfg.max_build_jobs),
            # 禁用分页
            "PAGER": "cat",
            "MANPAGER": "cat",
            "LESS": "-R",
            "GIT_PAGER": "cat",
        }

        # tools 目录（方便 agent 调用 python tools/xxx.py）
        if tools_dir is None:
            tools_dir = cfg.tools_dir
        env_vars["AGENT_TOOLS_DIR"] = tools_dir

        # 确保 Python 能找到 scripts/ 和 agent/tools/
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        pythonpath_parts = [
            os.path.join(project_root, "scripts"),
            tools_dir,
        ]
        existing_pp = os.environ.get("PYTHONPATH", "")
        if existing_pp:
            pythonpath_parts.append(existing_pp)
        env_vars["PYTHONPATH"] = ":".join(pythonpath_parts)

        # 合并额外环境变量（调用方可覆盖）
        if extra_env:
            env_vars.update(extra_env)

        super().__init__(
            cwd=issue_llvm_dir,
            env=env_vars,
            timeout=timeout,
        )

        # 保留引用，供外部访问
        self.issue_id = issue_id
        self.issue_llvm_dir = issue_llvm_dir
        self.llvm_build_dir = llvm_build_dir
        self.tools_dir = tools_dir
