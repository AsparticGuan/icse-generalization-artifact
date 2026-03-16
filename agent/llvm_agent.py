"""llvm_agent.py — LLVM InstCombine 修复专用 Agent（继承 mini-swe-agent DefaultAgent）。

在 DefaultAgent 基础上增加：
1. 安全校验：拦截危险命令
2. 自定义提交协议：检测 SUBMIT_PATCH 信号
3. 记录额外统计信息（build_count 等）
"""

import re
from minisweagent.agents.default import DefaultAgent, AgentConfig
from minisweagent.exceptions import Submitted


class LLVMAgentConfig(AgentConfig):
    """扩展 AgentConfig，增加 LLVM 修复专用配置。"""

    # 禁止执行的危险命令模式
    forbidden_patterns: list[str] = [
        r"(^|[;&|]\s*)rm\s+-rf\s+/\s*($|[;&|])",  # rm -rf /
        r"(^|[;&|]\s*)rm\s+-rf\s+/\*\s*($|[;&|])",  # rm -rf /*
        r"(^|[;&|]\s*)rm\s+-rf\s+/(bin|boot|dev|etc|home|lib|lib64|proc|root|run|sbin|sys|usr|var)\b",
        r"mkfs\.",
        r"sudo\s+.*passwd",
        r":(){ :\|:& };:",  # fork bomb
    ]


class LLVMFixAgent(DefaultAgent):
    """LLVM InstCombine 修复专用 Agent。

    核心扩展点在 execute_actions()：
    - 拦截危险命令 → 抛 FormatError 让模型重试
    - 检测提交信号 → 抛 Submitted 终止 agent
    """

    def __init__(self, model, env, **kwargs):
        super().__init__(model, env, config_class=LLVMAgentConfig, **kwargs)

    @staticmethod
    def _extract_commands(msg: dict) -> list[str]:
        extra = msg.get("extra", {}) if isinstance(msg, dict) else {}
        actions = extra.get("actions", []) if isinstance(extra, dict) else []
        if not isinstance(actions, list):
            return []
        cmds: list[str] = []
        for action in actions:
            if not isinstance(action, dict):
                continue
            cmd = action.get("command")
            if isinstance(cmd, str) and cmd.strip():
                cmds.append(cmd)
        return cmds

    @staticmethod
    def _escape_double_quotes(text: str) -> str:
        return text.replace("\\", "\\\\").replace('"', '\\"')

    @classmethod
    def _build_safety_block_echo(cls, cmd: str, pat: str) -> str:
        esc_cmd = cls._escape_double_quotes(cmd)
        esc_pat = cls._escape_double_quotes(pat)
        return (
            f'echo "[SAFETY] Action blocked by policy pattern: {esc_pat}"; '
            f'echo "[SAFETY] Blocked command: {esc_cmd}"; '
            'echo "[SAFETY] Please use a safer alternative."'
        )

    @staticmethod
    def _is_submit_signal(cmd: str) -> bool:
        return "SUBMIT_PATCH" in cmd and cmd.strip().startswith("echo")

    def _can_submit(self) -> tuple[bool, str]:
        messages = getattr(self, "messages", []) or []

        last_apply_idx = -1
        for idx, msg in enumerate(messages):
            for cmd in self._extract_commands(msg):
                if "apply_code.py write" in cmd or "apply_code.py sed" in cmd:
                    last_apply_idx = idx

        has_fast_pass = False
        has_full_pass = False
        for idx, msg in enumerate(messages):
            if idx <= last_apply_idx:
                continue
            content = msg.get("content", "") if isinstance(msg, dict) else ""
            if not isinstance(content, str):
                continue
            if (
                "BUILD SUCCESS + FAST CHECK PASSED" in content
                or "FAST CHECK PASSED" in content
            ):
                has_fast_pass = True
            if "FULL CHECK PASSED" in content:
                has_full_pass = True

        if not has_fast_pass and not has_full_pass:
            return (
                False,
                "missing passing build_and_check.py and check_full.py after latest code edit",
            )
        if not has_fast_pass:
            return False, "missing passing build_and_check.py after latest code edit"
        if not has_full_pass:
            return False, "missing passing check_full.py after latest code edit"
        return True, ""

    def execute_actions(self, message: dict) -> list[dict]:
        actions = message.get("extra", {}).get("actions", [])

        for action in actions:
            cmd = action.get("command", "")

            # 1) 安全校验：拦截危险命令
            blocked_pat = None
            for pat in self.config.forbidden_patterns:
                if re.search(pat, cmd, re.IGNORECASE):
                    blocked_pat = pat
                    break
            if blocked_pat:
                action["command"] = self._build_safety_block_echo(cmd, blocked_pat)
                continue

            # 2) 检测提交信号
            # 如果 agent 执行了 echo SUBMIT_PATCH 或类似命令
            if self._is_submit_signal(cmd):
                can_submit, reason = self._can_submit()
                if not can_submit:
                    esc_reason = self._escape_double_quotes(reason)
                    action["command"] = f'echo "[SUBMIT BLOCKED] {esc_reason}"'
                    continue
                raise Submitted(
                    {
                        "role": "exit",
                        "content": "Agent has submitted the patch.",
                        "extra": {
                            "exit_status": "submitted",
                            "submission": cmd,
                        },
                    }
                )

        # 正常执行
        return super().execute_actions(message)
