"""Async wrapper around the CodeFuse CLI."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from shutil import which
from typing import TYPE_CHECKING

from ductor_bot.cli.base import BaseCLI, CLIConfig, docker_wrap
from ductor_bot.cli.executor import SubprocessSpec, run_oneshot_subprocess
from ductor_bot.cli.stream_events import ResultEvent, StreamEvent
from ductor_bot.cli.types import CLIResponse

if TYPE_CHECKING:
    from ductor_bot.cli.timeout_controller import TimeoutController

logger = logging.getLogger(__name__)


class CfuseCLI(BaseCLI):
    """Async wrapper around the CodeFuse CLI."""

    def __init__(self, config: CLIConfig) -> None:
        self._config = config
        self._working_dir = Path(config.working_dir).resolve()
        self._cli = "cfuse" if config.docker_container else self._find_cli()
        logger.info("Cfuse CLI wrapper: cwd=%s, model=%s", self._working_dir, config.model)

    @staticmethod
    def _find_cli() -> str:
        path = which("cfuse")
        if not path:
            msg = "cfuse CLI not found on PATH. Install CodeFuse CLI before using this provider."
            raise FileNotFoundError(msg)
        return path

    def _compose_prompt(self, prompt: str) -> str:
        """Inject system context into the prompt when cfuse has no system flag."""
        cfg = self._config
        parts: list[str] = []
        if cfg.system_prompt:
            parts.append(cfg.system_prompt)
        parts.append(prompt)
        if cfg.append_system_prompt:
            parts.append(cfg.append_system_prompt)
        return "\n\n".join(parts)

    def _approval_mode(self) -> str:
        """Map ductor permission modes to cfuse approval modes."""
        permission_mode = self._config.permission_mode
        if permission_mode == "bypassPermissions":
            return "yolo"
        if permission_mode in {"acceptEdits", "autoEdit"}:
            return "auto_edit"
        return "default"

    def _build_command(self, prompt: str) -> list[str]:
        cmd = [
            self._cli,
            "-C",
            str(self._working_dir),
            "-o",
            "text",
            "--approval-mode",
            self._approval_mode(),
        ]
        if self._config.model:
            cmd += ["-m", self._config.model]
        if self._config.sandbox_mode not in {"full-access", "danger-full-access"}:
            cmd.append("-s")
        if self._config.agent_name:
            cmd += ["--agent", self._config.agent_name]
        if self._config.cli_parameters:
            cmd.extend(self._config.cli_parameters)
        cmd.append(self._compose_prompt(prompt))
        return cmd

    async def send(
        self,
        prompt: str,
        resume_session: str | None = None,
        continue_session: bool = False,
        timeout_seconds: float | None = None,
        timeout_controller: TimeoutController | None = None,
    ) -> CLIResponse:
        """Send a prompt and return the final result."""
        if resume_session:
            logger.debug("Cfuse resume_session is not supported yet, ignoring session=%s", resume_session)
        if continue_session:
            logger.debug("Cfuse continue_session is not supported yet")

        cmd = self._build_command(prompt)
        exec_cmd, use_cwd = docker_wrap(cmd, self._config)
        _log_cmd(exec_cmd)
        return await run_oneshot_subprocess(
            config=self._config,
            spec=SubprocessSpec(exec_cmd, use_cwd, prompt, timeout_seconds, timeout_controller),
            parse_output=_parse_output,
            provider_label="Cfuse",
        )

    async def send_streaming(
        self,
        prompt: str,
        resume_session: str | None = None,
        continue_session: bool = False,
        timeout_seconds: float | None = None,
        timeout_controller: TimeoutController | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Fallback to one-shot until cfuse exposes a stable stream format."""
        response = await self.send(
            prompt,
            resume_session=resume_session,
            continue_session=continue_session,
            timeout_seconds=timeout_seconds,
            timeout_controller=timeout_controller,
        )
        yield ResultEvent(
            type="result",
            session_id=response.session_id,
            result=response.result,
            is_error=response.is_error,
            returncode=response.returncode,
            total_cost_usd=response.total_cost_usd,
            usage=response.usage,
            model_usage=response.model_usage,
            duration_ms=response.duration_ms,
            duration_api_ms=response.duration_api_ms,
            num_turns=response.num_turns,
        )


def _log_cmd(cmd: list[str]) -> None:
    """Log the CLI command with truncated long values."""
    safe_cmd = [(c[:80] + "...") if len(c) > 80 else c for c in cmd]
    logger.info("Cfuse cmd: %s", " ".join(safe_cmd))


def _parse_output(stdout: bytes, stderr: bytes, returncode: int | None) -> CLIResponse:
    """Parse cfuse text output into a CLIResponse."""
    stdout_text = stdout.decode(errors="replace").strip() if stdout else ""
    stderr_text = stderr.decode(errors="replace")[:2000] if stderr else ""
    result_text = stdout_text or stderr_text
    is_error = bool(returncode and returncode != 0)
    if stderr_text:
        logger.warning("Cfuse stderr (exit=%s): %s", returncode, stderr_text[:500])
    return CLIResponse(
        result=result_text,
        is_error=is_error,
        returncode=returncode,
        stderr=stderr_text,
    )
