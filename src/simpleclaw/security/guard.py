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


# 시스템 설정 경로 — macOS 의 /private/{etc,var,tmp,home}/ symlink 미러를
# 포함한다. /etc/sudoers 같은 파일은 macOS 에선 실제로 /private/etc/sudoers
# 에 있고 ``echo x > /private/etc/sudoers`` 는 동일하게 동작한다.
# Hermes Agent PR #26829 (Claude Code 2.1.113 영향) 에서 도입.
_SYSTEM_CONFIG_PATH = r"(?:/etc/|/private/(?:etc|var|tmp|home)/)"


# 각 항목: (컴파일된 정규식, 패턴 키, 사람이 읽을 수 있는 설명)
_DANGEROUS_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # --- 파일시스템 파괴 ---
    (re.compile(r"rm\s+-\w*r", re.IGNORECASE), "rm_recursive", "Recursive file deletion"),
    (re.compile(r"rm\s+-\w*f", re.IGNORECASE), "rm_force", "Forced file deletion"),
    (re.compile(r"shred\s+", re.IGNORECASE), "shred", "Secure file erasure"),
    (re.compile(r"mkfs[\s.]", re.IGNORECASE), "mkfs", "Filesystem format"),
    (re.compile(r"dd\s+if=", re.IGNORECASE), "dd", "Raw disk write"),
    (re.compile(r"fdisk\s+", re.IGNORECASE), "fdisk", "Partition table modification"),
    (re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE), "format_drive", "Drive format"),

    # --- Git 파괴적 명령 ---
    (re.compile(r"git\s+push\s+.*--force", re.IGNORECASE), "git_force_push", "Git force push"),
    (re.compile(r"git\s+push\s+.*-f\b", re.IGNORECASE), "git_force_push_short", "Git force push"),
    (re.compile(r"git\s+reset\s+--hard", re.IGNORECASE), "git_reset_hard", "Git hard reset"),
    (re.compile(r"git\s+clean\s+.*-f", re.IGNORECASE), "git_clean", "Git clean (delete untracked)"),

    # --- 데이터베이스 파괴 ---
    (re.compile(r"DROP\s+(TABLE|DATABASE|SCHEMA)", re.IGNORECASE), "drop_table", "Database drop"),
    (re.compile(r"TRUNCATE\s+TABLE", re.IGNORECASE), "truncate_table", "Table truncation"),
    (re.compile(r"DELETE\s+FROM\s+\S+\s*;?\s*\Z", re.IGNORECASE), "delete_no_where", "DELETE without WHERE"),

    # --- 권한 상승 ---
    (re.compile(r"chmod\s+(777|666|a\+[rwx])", re.IGNORECASE), "chmod_wide", "Wide permission change"),
    (re.compile(r"chown\s+-R\s+root", re.IGNORECASE), "chown_root", "Recursive chown to root"),
    (re.compile(r"setuid|setgid", re.IGNORECASE), "setuid", "Set UID/GID bit"),

    # --- 셸로 파이프 (원격 코드 실행 위험) ---
    (re.compile(r"curl\s+.*\|\s*(ba)?sh", re.IGNORECASE), "curl_pipe_shell", "Pipe curl to shell"),
    (re.compile(r"wget\s+.*\|\s*(ba)?sh", re.IGNORECASE), "wget_pipe_shell", "Pipe wget to shell"),
    (re.compile(r"curl\s+.*\|\s*python", re.IGNORECASE), "curl_pipe_python", "Pipe curl to python"),

    # --- 시스템 명령 ---
    (re.compile(r"\breboot\b", re.IGNORECASE), "reboot", "System reboot"),
    (re.compile(r"\bshutdown\b", re.IGNORECASE), "shutdown", "System shutdown"),
    (re.compile(r"\binit\s+[06]\b", re.IGNORECASE), "init_halt", "System halt/reboot"),
    (re.compile(r"systemctl\s+(stop|disable)\s+(sshd|firewalld|NetworkManager)", re.IGNORECASE),
     "systemctl_stop_critical", "Stop critical service"),

    # --- 네트워크 ---
    (re.compile(r"iptables\s+-F", re.IGNORECASE), "iptables_flush", "Flush iptables rules"),
    (re.compile(r"ufw\s+disable", re.IGNORECASE), "ufw_disable", "Disable firewall"),

    # --- 프로세스 ---
    (re.compile(r"kill\s+-9\s+-1", re.IGNORECASE), "kill_all", "Kill all processes"),
    (re.compile(r"killall\s+", re.IGNORECASE), "killall", "Kill processes by name"),

    # --- 시크릿/환경변수 유출 ---
    (re.compile(r"\benv\s*>", re.IGNORECASE), "env_dump", "Dump environment to file"),
    (re.compile(r"printenv\s*\|", re.IGNORECASE), "printenv_pipe", "Pipe environment variables"),
    # macOS 의 /etc → /private/etc symlink 우회를 같은 패턴으로 막는다.
    # Hermes Agent PR #26829 적용.
    (re.compile(rf"cat[^|;&\n]*{_SYSTEM_CONFIG_PATH}(?:shadow|passwd|sudoers)", re.IGNORECASE),
     "read_shadow", "Read system credentials"),

    # --- 시스템 설정 파일 수정 (Hermes PR #26829: macOS /private/ 미러 포함) ---
    (re.compile(rf">\s*{_SYSTEM_CONFIG_PATH}", re.IGNORECASE),
     "write_system_config", "Overwrite system config"),
    (re.compile(rf"\btee\b[^|;&\n]*\s{_SYSTEM_CONFIG_PATH}", re.IGNORECASE),
     "tee_system_config", "Overwrite system config via tee"),
    (re.compile(rf"\b(cp|mv|install)\b[^|;&\n]*\s{_SYSTEM_CONFIG_PATH}", re.IGNORECASE),
     "copy_system_config", "Copy/move file into system config path"),
    (re.compile(rf"\bsed\s+-[^\s]*i[^|;&\n]*\s{_SYSTEM_CONFIG_PATH}", re.IGNORECASE),
     "sed_inplace_system_config", "In-place edit of system config"),
    (re.compile(rf"\bsed\s+--in-place\b[^|;&\n]*\s{_SYSTEM_CONFIG_PATH}", re.IGNORECASE),
     "sed_inplace_system_config_long", "In-place edit of system config (long flag)"),

    # --- 권한 상승 우회: sudo brute-force / askpass (Hermes PR #23736) ---
    # LLM-driven agent 는 TTY 가 없으므로 sudo 가 사람 개입 없이 성공하는
    # 형태는 stdin 으로 비밀번호를 흘려넣는 ``-S`` / ``--stdin`` 또는
    # askpass helper 를 쓰는 ``-A`` / ``--askpass`` 뿐. 둘 다 차단한다.
    # lazy [^;|&\n]*? 로 ``sudo -u root -S`` 같이 플래그 인자가 끼어들어도
    # 매칭되지만 ``;`` / ``|`` / ``&`` 같은 명령 구분자는 넘어가지 않는다.
    (re.compile(r"\bsudo\b[^;|&\n]*?\s+(?:-S\b|--stdin\b|-A\b|--askpass\b)", re.IGNORECASE),
     "sudo_stdin", "sudo with stdin/askpass (password guessing)"),
    # 단축 플래그 묶음: ``-nS``, ``-Su``, ``-SA`` — S/A 가 [a-z]* 안에 끼어든
    # 형태. 같은 위협 클래스를 packed 형태로 잡는다.
    (re.compile(r"\bsudo\b[^;|&\n]*?\s+-[a-z]*[sa][a-z]*\b", re.IGNORECASE),
     "sudo_combined_flag", "sudo with combined-flag (stdin/askpass packed)"),

    # --- 컨테이너 탈출 ---
    (re.compile(r"--privileged", re.IGNORECASE), "privileged_container", "Privileged container"),
    (re.compile(r"\bnsenter\b", re.IGNORECASE), "nsenter", "Namespace enter"),

    # --- 포크 폭탄 ---
    (re.compile(r":\(\)\{.*\|.*\};:", re.IGNORECASE), "fork_bomb", "Fork bomb"),

    # --- find 파괴적 액션 (Hermes PR #26829: -execdir / -delete 보강) ---
    # -execdir 은 -exec 과 동일하게 매칭된 경로마다 명령을 실행하지만
    # 각 경로의 디렉토리에서 실행된다. 두 형태 모두 차단.
    (re.compile(r"\bfind\b.*-exec(?:dir)?\s+(?:/\S*/)?rm\b", re.IGNORECASE),
     "find_exec_rm", "find -exec/-execdir rm"),
    (re.compile(r"\bfind\b.*-delete\b", re.IGNORECASE),
     "find_delete", "find -delete"),

    # --- GUI / 대화형 명령 ---
    (re.compile(r"^open\s+(https?://|/)", re.IGNORECASE | re.MULTILINE), "open_url", "macOS open (launches GUI browser/app)"),
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
