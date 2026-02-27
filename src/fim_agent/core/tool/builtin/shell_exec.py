"""Built-in tool for executing shell commands in a sandboxed environment."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any

from ..base import BaseTool

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS: int = 30
_MAX_TIMEOUT_SECONDS: int = 120

# Maximum captured output size (bytes) before truncation.
_MAX_OUTPUT_BYTES: int = 100 * 1024  # 100 KB

# Default sandbox workspace lives under the project-level tmp/ directory.
_DEFAULT_SANDBOX_DIR = Path(__file__).resolve().parents[4] / "tmp" / "sandbox"

# -----------------------------------------------------------------------
# Security: command blocklist patterns
# -----------------------------------------------------------------------
# Each entry is a compiled regex that is tested against the full command
# string.  We use word boundaries (\b) to avoid false positives (e.g.
# blocking "rm" should not block "curl" or "format").

_BLOCKED_COMMAND_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Privilege escalation
    (re.compile(r"\bsudo\b"), "privilege escalation (sudo)"),
    (re.compile(r"\bsu\b"), "privilege escalation (su)"),
    (re.compile(r"\bdoas\b"), "privilege escalation (doas)"),

    # Destructive root operations
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/(?:\s|$|\*)"), "destructive operation (rm -rf /)"),
    (re.compile(r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*\s+/(?:\s|$|\*)"), "destructive operation (rm -fr /)"),
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+~"), "destructive operation (rm -rf ~)"),
    (re.compile(r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*\s+~"), "destructive operation (rm -fr ~)"),

    # Disk operations
    (re.compile(r"\bmkfs\b"), "disk operation (mkfs)"),
    (re.compile(r"\bfdisk\b"), "disk operation (fdisk)"),
    (re.compile(r"\bdd\b"), "disk operation (dd)"),

    # System control
    (re.compile(r"\bshutdown\b"), "system control (shutdown)"),
    (re.compile(r"\breboot\b"), "system control (reboot)"),
    (re.compile(r"\bhalt\b"), "system control (halt)"),
    (re.compile(r"\bpoweroff\b"), "system control (poweroff)"),

    # Service management
    (re.compile(r"\bsystemctl\b"), "service management (systemctl)"),
    (re.compile(r"\bservice\b"), "service management (service)"),

    # Package installation
    (re.compile(r"\bapt\b"), "package installation (apt)"),
    (re.compile(r"\bapt-get\b"), "package installation (apt-get)"),
    (re.compile(r"\byum\b"), "package installation (yum)"),
    (re.compile(r"\bdnf\b"), "package installation (dnf)"),
    (re.compile(r"\bbrew\b"), "package installation (brew)"),
    (re.compile(r"\bpacman\b"), "package installation (pacman)"),
    (re.compile(r"\bpip\s+install\b"), "package installation (pip install)"),
    (re.compile(r"\bnpm\s+install\b"), "package installation (npm install)"),

    # Permission changes
    (re.compile(r"\bchmod\b"), "permission change (chmod)"),
    (re.compile(r"\bchown\b"), "permission change (chown)"),
    (re.compile(r"\bchgrp\b"), "permission change (chgrp)"),

    # Filesystem mounting
    (re.compile(r"\bmount\b"), "filesystem mounting (mount)"),
    (re.compile(r"\bumount\b"), "filesystem mounting (umount)"),

    # Firewall changes
    (re.compile(r"\biptables\b"), "firewall change (iptables)"),
    (re.compile(r"\bufw\b"), "firewall change (ufw)"),

    # Process killing
    (re.compile(r"\bkill\b"), "process killing (kill)"),
    (re.compile(r"\bkillall\b"), "process killing (killall)"),
    (re.compile(r"\bpkill\b"), "process killing (pkill)"),

    # Cron manipulation
    (re.compile(r"\bcrontab\b"), "cron manipulation (crontab)"),

    # Remote access
    (re.compile(r"\bssh\b"), "remote access (ssh)"),
    (re.compile(r"\bscp\b"), "remote access (scp)"),
    (re.compile(r"\brsync\b"), "remote access (rsync)"),

    # Listening sockets (connecting is fine)
    (re.compile(r"\bnc\s+-[a-zA-Z]*l"), "listening socket (nc -l)"),
    (re.compile(r"\bncat\s+-[a-zA-Z]*l"), "listening socket (ncat -l)"),
]

# System paths that must not be written to.
_BLOCKED_WRITE_PATHS: tuple[str, ...] = (
    "/etc/", "/usr/", "/bin/", "/sbin/", "/boot/", "/sys/", "/proc/",
)

# Regex to detect write redirections to blocked system paths.
# Matches patterns like: > /etc/passwd, >> /usr/bin/foo, tee /etc/hosts
_WRITE_TO_SYSTEM_PATH_PATTERN = re.compile(
    r"(?:>{1,2}\s*|tee\s+(?:-a\s+)?)(" + "|".join(re.escape(p) for p in _BLOCKED_WRITE_PATHS) + r")"
)

# -----------------------------------------------------------------------
# Security: environment variable scrubbing
# -----------------------------------------------------------------------

# Patterns for env var names that should be scrubbed from child processes.
_SENSITIVE_ENV_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^AWS_", re.IGNORECASE),
    re.compile(r"^OPENAI_", re.IGNORECASE),
    re.compile(r"^ANTHROPIC_", re.IGNORECASE),
    re.compile(r"^AZURE_", re.IGNORECASE),
    re.compile(r"^GOOGLE_", re.IGNORECASE),
    re.compile(r"^JINA_", re.IGNORECASE),
    re.compile(r"_KEY$", re.IGNORECASE),
    re.compile(r"_SECRET$", re.IGNORECASE),
    re.compile(r"_TOKEN$", re.IGNORECASE),
    re.compile(r"_PASSWORD$", re.IGNORECASE),
    re.compile(r"^DATABASE_URL$", re.IGNORECASE),
    re.compile(r"^REDIS_URL$", re.IGNORECASE),
    re.compile(r"^MONGO_URL$", re.IGNORECASE),
    re.compile(r"^DSN$", re.IGNORECASE),
)

# Standard PATH for child processes (no user-specific paths).
_SAFE_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _truncate_output(text: str) -> str:
    """Truncate *text* if it exceeds ``_MAX_OUTPUT_BYTES``."""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= _MAX_OUTPUT_BYTES:
        return text
    truncated = encoded[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    return (
        truncated
        + f"\n\n[Output truncated — exceeded {_MAX_OUTPUT_BYTES // 1024} KB limit]"
    )


def _is_env_sensitive(name: str) -> bool:
    """Return True if the environment variable *name* looks sensitive."""
    return any(pat.search(name) for pat in _SENSITIVE_ENV_PATTERNS)


def _build_safe_env(sandbox_dir: str) -> dict[str, str]:
    """Build a restricted environment dict for the child process."""
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if not _is_env_sensitive(key):
            env[key] = value

    # Override sensitive defaults regardless of what was inherited.
    env["HOME"] = sandbox_dir
    env["PATH"] = _SAFE_PATH
    env["TMPDIR"] = sandbox_dir
    # Prevent any lingering credentials from leaking.
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                "AWS_SESSION_TOKEN", "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY", "DATABASE_URL"):
        env.pop(key, None)
    return env


def _validate_command(command: str) -> str | None:
    """Validate *command* against the blocklist.

    Returns an error message string if the command is blocked, or ``None``
    if it passes all checks.
    """
    for pattern, reason in _BLOCKED_COMMAND_PATTERNS:
        if pattern.search(command):
            return reason

    # Check for writes to system paths.
    match = _WRITE_TO_SYSTEM_PATH_PATTERN.search(command)
    if match:
        return f"write to system path ({match.group(1)})"

    return None


def _validate_working_dir(working_dir: str, sandbox_dir: Path) -> str | None:
    """Validate that *working_dir* resolves within the sandbox.

    Returns an error message if validation fails, ``None`` on success.
    """
    try:
        resolved = Path(working_dir).resolve()
    except (OSError, ValueError) as exc:
        return f"invalid working directory: {exc}"

    sandbox_resolved = sandbox_dir.resolve()
    if not (resolved == sandbox_resolved or sandbox_resolved in resolved.parents):
        return (
            f"working directory must be within the sandbox "
            f"({sandbox_resolved}), got {resolved}"
        )
    return None


class ShellExecTool(BaseTool):
    """Execute shell commands in a sandboxed environment.

    Commands run via ``asyncio.create_subprocess_shell`` with a restricted
    environment: sensitive environment variables are scrubbed, PATH is
    limited to standard system directories, and a blocklist prevents
    dangerous operations such as privilege escalation, package installation,
    and writes to system paths.
    """

    def __init__(
        self,
        *,
        timeout: int = _DEFAULT_TIMEOUT_SECONDS,
        sandbox_dir: Path | None = None,
    ) -> None:
        self._timeout = timeout
        self._sandbox_dir = sandbox_dir or _DEFAULT_SANDBOX_DIR

    # ------------------------------------------------------------------
    # Tool protocol properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "shell_exec"

    @property
    def category(self) -> str:
        return "system"

    @property
    def description(self) -> str:
        return (
            "Execute shell commands in a sandboxed environment. "
            "Useful for running CLI tools like curl, jq, awk, sed, wc, "
            "sort, head, tail, grep, etc. "
            "Commands run with restricted permissions — no access to "
            "system files, no sudo, no package installation."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute.",
                },
                "timeout": {
                    "type": "number",
                    "description": (
                        "Execution timeout in seconds. "
                        "Defaults to 30, max 120."
                    ),
                },
                "working_dir": {
                    "type": "string",
                    "description": (
                        "Working directory for the command. "
                        "Defaults to a temporary workspace."
                    ),
                },
            },
            "required": ["command"],
        }

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def run(self, **kwargs: Any) -> str:
        """Execute the provided shell *command* and return its output.

        Args:
            **kwargs: Must contain ``command`` (str).  Optionally
                ``timeout`` (number) and ``working_dir`` (str).

        Returns:
            Formatted string with exit code, stdout, and stderr.
        """
        command: str = kwargs.get("command", "").strip()
        if not command:
            return "[Error] No command provided."

        timeout = min(int(kwargs.get("timeout", self._timeout)), _MAX_TIMEOUT_SECONDS)
        if timeout <= 0:
            timeout = self._timeout

        # 1. Validate command against blocklist.
        block_reason = _validate_command(command)
        if block_reason is not None:
            logger.warning("Blocked shell command (%s): %s", block_reason, command)
            return f"[Error] Command blocked: {block_reason}"

        # 2. Ensure sandbox directory exists.
        sandbox = self._sandbox_dir
        sandbox.mkdir(parents=True, exist_ok=True)

        # 3. Resolve working directory.
        working_dir_arg: str | None = kwargs.get("working_dir")
        if working_dir_arg is not None:
            working_dir_arg = working_dir_arg.strip()

        if working_dir_arg:
            # If relative, interpret relative to the sandbox.
            wd_path = Path(working_dir_arg)
            if not wd_path.is_absolute():
                wd_path = sandbox / wd_path
            err = _validate_working_dir(str(wd_path), sandbox)
            if err is not None:
                return f"[Error] {err}"
            wd_path.mkdir(parents=True, exist_ok=True)
            cwd = str(wd_path)
        else:
            cwd = str(sandbox)

        # 4. Build restricted environment.
        env = _build_safe_env(cwd)

        # 5. Execute the command.
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
            except TimeoutError:
                # Kill the timed-out process.
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
                return f"[Timeout] Command timed out after {timeout}s"

        except OSError as exc:
            return f"[Error] Failed to execute command: {exc}"

        # 6. Decode output.
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        exit_code = proc.returncode

        # 7. Format and truncate output.
        parts: list[str] = [f"Exit Code: {exit_code}"]

        if stdout_text:
            parts.append(f"\nstdout:\n{stdout_text}")
        else:
            parts.append("\nstdout:\n(empty)")

        if stderr_text:
            parts.append(f"\nstderr:\n{stderr_text}")

        result = "\n".join(parts)
        return _truncate_output(result)
