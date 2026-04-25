"""위험 명령어 탐지 가드 모듈.

Hermes Agent의 approval.py에서 영감을 받아 구현한 패턴 기반 위험 명령어 탐지기.
파괴적 명령(rm -rf), 권한 상승(chmod 777), 데이터 유출(env 덤프) 등을 정규식으로 검사한다.

설계 결정:
- 정규식 패턴 목록으로 관리하여 확장·유지보수 용이
- allowlist로 특정 패턴을 예외 처리 가능
- 유니코드 정규화(NFKC)로 전각 문자 우회 공격 방지
"""

from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)


class DangerousCommandError(Exception):
    """위험한 패턴에 매칭된 명령어가 감지되었을 때 발생하는 예외."""

    def __init__(self, command: str, pattern_key: str, description: str) -> None:
        self.command = command
        self.pattern_key = pattern_key
        self.description = description
        super().__init__(f"Dangerous command blocked ({pattern_key}): {description}")


# 각 항목: (컴파일된 정규식, 패턴 키, 사람이 읽을 수 있는 설명)
_DANGEROUS_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # --- 파일시스템 파괴 ---
    (re.compile(r"rm\s+-\w*r", re.I), "rm_recursive", "Recursive file deletion"),
    (re.compile(r"rm\s+-\w*f", re.I), "rm_force", "Forced file deletion"),
    (re.compile(r"shred\s+", re.I), "shred", "Secure file erasure"),
    (re.compile(r"mkfs[\s.]", re.I), "mkfs", "Filesystem format"),
    (re.compile(r"dd\s+if=", re.I), "dd", "Raw disk write"),
    (re.compile(r"fdisk\s+", re.I), "fdisk", "Partition table modification"),
    (re.compile(r"\bformat\s+[a-z]:", re.I), "format_drive", "Drive format"),

    # --- Git 파괴적 명령 ---
    (re.compile(r"git\s+push\s+.*--force", re.I), "git_force_push", "Git force push"),
    (re.compile(r"git\s+push\s+.*-f\b", re.I), "git_force_push_short", "Git force push"),
    (re.compile(r"git\s+reset\s+--hard", re.I), "git_reset_hard", "Git hard reset"),
    (re.compile(r"git\s+clean\s+.*-f", re.I), "git_clean", "Git clean (delete untracked)"),

    # --- 데이터베이스 파괴 ---
    (re.compile(r"DROP\s+(TABLE|DATABASE|SCHEMA)", re.I), "drop_table", "Database drop"),
    (re.compile(r"TRUNCATE\s+TABLE", re.I), "truncate_table", "Table truncation"),
    (re.compile(r"DELETE\s+FROM\s+\S+\s*;?\s*\Z", re.I), "delete_no_where", "DELETE without WHERE"),

    # --- 권한 상승 ---
    (re.compile(r"chmod\s+(777|666|a\+[rwx])", re.I), "chmod_wide", "Wide permission change"),
    (re.compile(r"chown\s+-R\s+root", re.I), "chown_root", "Recursive chown to root"),
    (re.compile(r"setuid|setgid", re.I), "setuid", "Set UID/GID bit"),

    # --- 셸로 파이프 (원격 코드 실행 위험) ---
    (re.compile(r"curl\s+.*\|\s*(ba)?sh", re.I), "curl_pipe_shell", "Pipe curl to shell"),
    (re.compile(r"wget\s+.*\|\s*(ba)?sh", re.I), "wget_pipe_shell", "Pipe wget to shell"),
    (re.compile(r"curl\s+.*\|\s*python", re.I), "curl_pipe_python", "Pipe curl to python"),

    # --- 시스템 명령 ---
    (re.compile(r"\breboot\b", re.I), "reboot", "System reboot"),
    (re.compile(r"\bshutdown\b", re.I), "shutdown", "System shutdown"),
    (re.compile(r"\binit\s+[06]\b", re.I), "init_halt", "System halt/reboot"),
    (re.compile(r"systemctl\s+(stop|disable)\s+(sshd|firewalld|NetworkManager)", re.I),
     "systemctl_stop_critical", "Stop critical service"),

    # --- 네트워크 ---
    (re.compile(r"iptables\s+-F", re.I), "iptables_flush", "Flush iptables rules"),
    (re.compile(r"ufw\s+disable", re.I), "ufw_disable", "Disable firewall"),

    # --- 프로세스 ---
    (re.compile(r"kill\s+-9\s+-1", re.I), "kill_all", "Kill all processes"),
    (re.compile(r"killall\s+", re.I), "killall", "Kill processes by name"),

    # --- 시크릿/환경변수 유출 ---
    (re.compile(r"\benv\s*>", re.I), "env_dump", "Dump environment to file"),
    (re.compile(r"printenv\s*\|", re.I), "printenv_pipe", "Pipe environment variables"),
    (re.compile(r"cat.*/etc/(shadow|passwd)", re.I), "read_shadow", "Read system credentials"),

    # --- 컨테이너 탈출 ---
    (re.compile(r"--privileged", re.I), "privileged_container", "Privileged container"),
    (re.compile(r"\bnsenter\b", re.I), "nsenter", "Namespace enter"),

    # --- 포크 폭탄 ---
    (re.compile(r":\(\)\{.*\|.*\};:", re.I), "fork_bomb", "Fork bomb"),

    # --- GUI / 대화형 명령 ---
    (re.compile(r"^open\s+(https?://|/)", re.I | re.M), "open_url", "macOS open (launches GUI browser/app)"),
]


def _normalize(command: str) -> str:
    """패턴 매칭을 위해 명령어 문자열을 정규화한다."""
    # ANSI 이스케이프 시퀀스 제거
    command = re.sub(r"\x1b\[[0-9;]*m", "", command)
    # 유니코드 NFKC 정규화 (전각 문자 → 반각 문자 변환으로 우회 방지)
    command = unicodedata.normalize("NFKC", command)
    # 널 바이트 제거
    command = command.replace("\x00", "")
    return command


class CommandGuard:
    """허용 목록(allowlist)을 지원하는 패턴 기반 위험 명령어 탐지기."""

    def __init__(
        self,
        allowlist: list[str] | None = None,
        enabled: bool = True,
    ) -> None:
        self._allowlist: set[str] = set(allowlist or [])
        self._enabled = enabled

    def check(self, command: str) -> None:
        """명령어가 위험 패턴에 매칭되면 DangerousCommandError를 발생시킨다.

        ``pattern_key``가 허용 목록에 있는 패턴은 건너뛴다.
        가드가 비활성화(enabled=False)된 경우 아무 동작도 하지 않는다.
        """
        if not self._enabled:
            return

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
        """명령어가 위험 패턴에 매칭되지 않으면 True를 반환한다."""
        try:
            self.check(command)
            return True
        except DangerousCommandError:
            return False
