"""Built-in tool for executing shell commands in a sandboxed environment."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from ..base import BaseTool, ToolResult
from ..sandbox import get_sandbox_backend
from ..truncation import truncate_bytes

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS: int = 30
_MAX_TIMEOUT_SECONDS: int = 120

# Default sandbox workspace lives under the project-level tmp/ directory.
_DEFAULT_SANDBOX_DIR = Path(__file__).resolve().parents[4] / "tmp" / "default" / "sandbox"

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

    # Shell interpreters
    (re.compile(r"\bbash\b"), "shell interpreter (bash)"),
    (re.compile(r"\bsh\b"), "shell interpreter (sh)"),
    (re.compile(r"\bzsh\b"), "shell interpreter (zsh)"),
    (re.compile(r"\bdash\b"), "shell interpreter (dash)"),
    (re.compile(r"\bfish\b"), "shell interpreter (fish)"),

    # Script interpreters
    (re.compile(r"\bpython\b"), "script interpreter (python)"),
    (re.compile(r"\bpython3\b"), "script interpreter (python3)"),
    (re.compile(r"\bperl\b"), "script interpreter (perl)"),
    (re.compile(r"\bruby\b"), "script interpreter (ruby)"),
    (re.compile(r"\bnode\b"), "script interpreter (node)"),
    (re.compile(r"\bphp\b"), "script interpreter (php)"),

    # Network tools
    (re.compile(r"\bcurl\b"), "network tool (curl)"),
    (re.compile(r"\bwget\b"), "network tool (wget)"),

    # Command wrappers
    (re.compile(r"\benv\b"), "command wrapper (env)"),
    (re.compile(r"\bxargs\b"), "command wrapper (xargs)"),
    (re.compile(r"\bexec\b"), "command wrapper (exec)"),
    (re.compile(r"\beval\b"), "command wrapper (eval)"),
    (re.compile(r"\bnohup\b"), "command wrapper (nohup)"),
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
# Security: metacharacter evasion detection
# -----------------------------------------------------------------------
# These patterns detect shell metacharacters commonly used to bypass
# word-boundary blocklist matching. Checked BEFORE the blocklist.

_METACHAR_EVASION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'\$\('), "command substitution $()"),
    (re.compile(r'`'), "backtick substitution"),
    (re.compile(r'\$\{'), "variable expansion ${}"),
    (re.compile(r'\w""\w'), 'empty quote insertion (e.g. su""do)'),
    (re.compile(r"\w''\w"), "empty quote insertion (e.g. su''do)"),
    (re.compile(r'base64\s+(-d|--decode)', re.IGNORECASE), "base64 decode obfuscation"),
]

# Known-safe shell variables that may appear in legitimate commands
# (e.g. `echo $HOME`, `ls $PWD`).  Any $VAR not in this set is blocked
# to prevent command-blocklist bypass via variable expansion (e.g. $SHELL).
_SAFE_SHELL_VARS: frozenset[str] = frozenset({
    "HOME", "USER", "PWD", "OLDPWD", "LANG", "LC_ALL", "LC_CTYPE",
    "TERM", "COLUMNS", "LINES", "TMPDIR", "HOSTNAME", "PATH",
    "SHLVL", "LOGNAME",
})
_DOLLAR_VAR_RE = re.compile(r'\$([A-Za-z_]\w*)')


def _check_shell_metacharacters(command: str) -> str | None:
    """Pre-screen for shell metacharacter evasion patterns.

    Returns a reason string if evasion is detected, None if clean.
    Checked BEFORE the blocklist to prevent bypass techniques.
    """
    for pattern, reason in _METACHAR_EVASION_PATTERNS:
        if pattern.search(command):
            return reason
    # Fine-grained $VAR check: allow known-safe variables, block the rest.
    for m in _DOLLAR_VAR_RE.finditer(command):
        if m.group(1) not in _SAFE_SHELL_VARS:
            return f"variable reference ${m.group(1)}"
    return None


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

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

    Commands are dispatched to the configured sandbox backend (local or
    docker), selected via the ``CODE_EXEC_BACKEND`` environment variable.
    A blocklist prevents dangerous operations such as privilege escalation,
    package installation, and writes to system paths.
    """

    def __init__(
        self,
        *,
        timeout: int = _DEFAULT_TIMEOUT_SECONDS,
        sandbox_dir: Path | None = None,
        memory: str | None = None,
        cpu: float | None = None,
        artifacts_dir: Path | None = None,
    ) -> None:
        self._timeout = timeout
        self._sandbox_dir = sandbox_dir or _DEFAULT_SANDBOX_DIR
        self._memory = memory
        self._cpu = cpu
        self._artifacts_dir = artifacts_dir

        backend_name = os.environ.get("CODE_EXEC_BACKEND", "local").lower()
        if backend_name == "local":
            logger.warning(
                "shell_exec is using the LOCAL backend — commands run directly on the "
                "host machine with no OS-level isolation. "
                "Use CODE_EXEC_BACKEND=docker in production multi-user deployments."
            )

    # ------------------------------------------------------------------
    # Tool protocol properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "shell_exec"

    @property
    def category(self) -> str:
        return "computation"

    @property
    def description(self) -> str:
        return (
            "Execute shell commands in a sandboxed environment. "
            "Useful for running CLI tools like jq, awk, sed, wc, "
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

    async def run(self, **kwargs: Any) -> str | ToolResult:  # type: ignore[override]
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

        # 1a. Pre-screen for metacharacter evasion patterns.
        metachar_reason = _check_shell_metacharacters(command)
        if metachar_reason is not None:
            logger.warning("Blocked shell command (%s): %s", metachar_reason, command)
            return f"[Error] Command blocked: {metachar_reason}"

        # 1b. Validate command against blocklist.
        block_reason = _validate_command(command)
        if block_reason is not None:
            logger.warning("Blocked shell command (%s): %s", block_reason, command)
            return f"[Error] Command blocked: {block_reason}"

        # 2. Ensure sandbox directory exists.
        sandbox = self._sandbox_dir
        sandbox.mkdir(parents=True, exist_ok=True)

        # Snapshot files before execution for artifact detection.
        before: set[str] = set()
        if self._artifacts_dir:
            before = {f.name for f in sandbox.iterdir() if f.is_file()}

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

        # 4. Dispatch to the configured sandbox backend.
        backend = get_sandbox_backend()
        result = await backend.run_shell(
            command,
            sandbox_dir=Path(cwd),
            timeout=timeout,
            memory=self._memory,
            cpu=self._cpu,
        )

        # 5. Format and return output.
        if result.timed_out:
            return f"[Timeout] Command timed out after {timeout}s"

        if result.error:
            return f"[Error] Failed to execute command: {result.error}"

        parts: list[str] = [f"Exit Code: {result.exit_code}"]

        if result.stdout:
            parts.append(f"\nstdout:\n{result.stdout}")
        else:
            parts.append("\nstdout:\n(empty)")

        if result.stderr:
            parts.append(f"\nstderr:\n{result.stderr}")

        output = "\n".join(parts)
        output = truncate_bytes(output)

        # Scan for new files after execution.
        if self._artifacts_dir:
            from ..artifact_utils import scan_new_files
            from ..base import ToolResult

            artifacts = scan_new_files(sandbox, before, self._artifacts_dir)
            if artifacts:
                return ToolResult(content=output, artifacts=artifacts)

        return output
