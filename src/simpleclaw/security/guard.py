"""Dangerous command detection guard.

Inspired by Hermes Agent's approval.py — pattern-based detection of
destructive, privileged, or data-exfiltration shell commands.
"""

from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)


class DangerousCommandError(Exception):
    """Raised when a command matches a dangerous pattern."""

    def __init__(self, command: str, pattern_key: str, description: str) -> None:
        self.command = command
        self.pattern_key = pattern_key
        self.description = description
        super().__init__(f"Dangerous command blocked ({pattern_key}): {description}")


# Each entry: (compiled regex, pattern_key, human-readable description)
_DANGEROUS_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # --- Filesystem destruction ---
    (re.compile(r"rm\s+(-\w*)?r", re.I), "rm_recursive", "Recursive file deletion"),
    (re.compile(r"rm\s+(-\w*)?f", re.I), "rm_force", "Forced file deletion"),
    (re.compile(r"shred\s+", re.I), "shred", "Secure file erasure"),
    (re.compile(r"mkfs[\s.]", re.I), "mkfs", "Filesystem format"),
    (re.compile(r"dd\s+if=", re.I), "dd", "Raw disk write"),
    (re.compile(r"fdisk\s+", re.I), "fdisk", "Partition table modification"),
    (re.compile(r"\bformat\s+[a-z]:", re.I), "format_drive", "Drive format"),

    # --- Git destructive ---
    (re.compile(r"git\s+push\s+.*--force", re.I), "git_force_push", "Git force push"),
    (re.compile(r"git\s+push\s+.*-f\b", re.I), "git_force_push_short", "Git force push"),
    (re.compile(r"git\s+reset\s+--hard", re.I), "git_reset_hard", "Git hard reset"),
    (re.compile(r"git\s+clean\s+.*-f", re.I), "git_clean", "Git clean (delete untracked)"),

    # --- Database destructive ---
    (re.compile(r"DROP\s+(TABLE|DATABASE|SCHEMA)", re.I), "drop_table", "Database drop"),
    (re.compile(r"TRUNCATE\s+TABLE", re.I), "truncate_table", "Table truncation"),
    (re.compile(r"DELETE\s+FROM\s+\S+\s*;?\s*$", re.I | re.M), "delete_no_where", "DELETE without WHERE"),

    # --- Permission escalation ---
    (re.compile(r"chmod\s+(777|666|a\+[rwx])", re.I), "chmod_wide", "Wide permission change"),
    (re.compile(r"chown\s+-R\s+root", re.I), "chown_root", "Recursive chown to root"),
    (re.compile(r"setuid|setgid", re.I), "setuid", "Set UID/GID bit"),

    # --- Pipe to shell ---
    (re.compile(r"curl\s+.*\|\s*(ba)?sh", re.I), "curl_pipe_shell", "Pipe curl to shell"),
    (re.compile(r"wget\s+.*\|\s*(ba)?sh", re.I), "wget_pipe_shell", "Pipe wget to shell"),
    (re.compile(r"curl\s+.*\|\s*python", re.I), "curl_pipe_python", "Pipe curl to python"),

    # --- System commands ---
    (re.compile(r"\breboot\b", re.I), "reboot", "System reboot"),
    (re.compile(r"\bshutdown\b", re.I), "shutdown", "System shutdown"),
    (re.compile(r"\binit\s+[06]\b", re.I), "init_halt", "System halt/reboot"),
    (re.compile(r"systemctl\s+(stop|disable)\s+(sshd|firewalld|NetworkManager)", re.I),
     "systemctl_stop_critical", "Stop critical service"),

    # --- Network ---
    (re.compile(r"iptables\s+-F", re.I), "iptables_flush", "Flush iptables rules"),
    (re.compile(r"ufw\s+disable", re.I), "ufw_disable", "Disable firewall"),

    # --- Process ---
    (re.compile(r"kill\s+-9\s+-1", re.I), "kill_all", "Kill all processes"),
    (re.compile(r"killall\s+", re.I), "killall", "Kill processes by name"),

    # --- Secret/env exfiltration ---
    (re.compile(r"\benv\s*>", re.I), "env_dump", "Dump environment to file"),
    (re.compile(r"printenv\s*\|", re.I), "printenv_pipe", "Pipe environment variables"),
    (re.compile(r"cat.*/etc/(shadow|passwd)", re.I), "read_shadow", "Read system credentials"),

    # --- Container escape ---
    (re.compile(r"--privileged", re.I), "privileged_container", "Privileged container"),
    (re.compile(r"\bnsenter\b", re.I), "nsenter", "Namespace enter"),

    # --- Fork bomb ---
    (re.compile(r":\(\)\{.*\|.*\};:", re.I), "fork_bomb", "Fork bomb"),

    # --- GUI / interactive ---
    (re.compile(r"^open\s+(https?://|/)", re.I | re.M), "open_url", "macOS open (launches GUI browser/app)"),
]


def _normalize(command: str) -> str:
    """Normalize a command string for safe pattern matching."""
    # Strip ANSI escape sequences
    command = re.sub(r"\x1b\[[0-9;]*m", "", command)
    # Unicode NFKC normalization (fullwidth → halfwidth)
    command = unicodedata.normalize("NFKC", command)
    # Remove null bytes
    command = command.replace("\x00", "")
    return command


class CommandGuard:
    """Pattern-based dangerous command detector with allowlist support."""

    def __init__(self, allowlist: list[str] | None = None) -> None:
        self._allowlist: set[str] = set(allowlist or [])

    def check(self, command: str) -> None:
        """Raise DangerousCommandError if the command matches a dangerous pattern.

        Skips patterns whose ``pattern_key`` is in the allowlist.
        """
        normalized = _normalize(command)

        for pattern, key, description in _DANGEROUS_PATTERNS:
            if key in self._allowlist:
                continue
            if pattern.search(normalized):
                logger.warning(
                    "Dangerous command blocked [%s]: %s — %s",
                    key, command[:200], description,
                )
                raise DangerousCommandError(command, key, description)

    def is_safe(self, command: str) -> bool:
        """Return True if the command does not match any dangerous pattern."""
        try:
            self.check(command)
            return True
        except DangerousCommandError:
            return False
