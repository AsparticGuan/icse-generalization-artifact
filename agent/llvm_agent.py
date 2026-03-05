"""llvm_agent.py — LLVM InstCombine 修复专用 Agent（继承 mini-swe-agent DefaultAgent）。

在 DefaultAgent 基础上增加：
1. 安全校验：拦截危险命令
2. 自定义提交协议：检测 SUBMIT_PATCH 信号
3. 记录额外统计信息（build_count 等）
"""

import re
from minisweagent.agents.default import DefaultAgent, AgentConfig
from minisweagent.exceptions import FormatError, Submitted


class LLVMAgentConfig(AgentConfig):
    """扩展 AgentConfig，增加 LLVM 修复专用配置。"""
    # 禁止执行的危险命令模式
    forbidden_patterns: list[str] = [
        r"rm\s+-rf\s+/(?!.*llvm)",   # rm -rf / 但允许 llvm 相关路径
        r"mkfs\.",
        r"sudo\s+.*passwd",
        r":(){ :\|:& };:",           # fork bomb
    ]


class LLVMFixAgent(DefaultAgent):
    """LLVM InstCombine 修复专用 Agent。

    核心扩展点在 execute_actions()：
    - 拦截危险命令 → 抛 FormatError 让模型重试
    - 检测提交信号 → 抛 Submitted 终止 agent
    """

    def __init__(self, model, env, **kwargs):
        super().__init__(model, env, config_class=LLVMAgentConfig, **kwargs)

    def execute_actions(self, message: dict) -> list[dict]:
        actions = message.get("extra", {}).get("actions", [])

        for action in actions:
            cmd = action.get("command", "")

            # 1) 安全校验：拦截危险命令
            for pat in self.config.forbidden_patterns:
                if re.search(pat, cmd, re.IGNORECASE):
                    raise FormatError(self.model.format_message(
                        role="user",
                        content=(
                            f"⚠️ Action blocked by safety policy: pattern '{pat}' matched.\n"
                            f"Blocked command: {cmd}\n"
                            "Please use a safer alternative."
                        ),
                    ))

            # 2) 检测提交信号
            # 如果 agent 执行了 echo SUBMIT_PATCH 或类似命令
            if "SUBMIT_PATCH" in cmd and cmd.strip().startswith("echo"):
                raise Submitted({
                    "role": "exit",
                    "content": "Agent has submitted the patch.",
                    "extra": {
                        "exit_status": "submitted",
                        "submission": cmd,
                    },
                })

        # 正常执行
        return super().execute_actions(message)
