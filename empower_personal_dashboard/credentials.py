"""
credentials.py — Credential helper and OS keyring integration for Empower Personal Dashboard.

Supports:
1. Pluggable external credential helpers (e.g. `pass`, `op`, `lpass`, `git-credential`).
2. OS system keyring / keychain via the standard `keyring` library (macOS Keychain, Linux SecretService, Windows Credential Manager).
3. Secure hierarchical password resolution for unattended syncs.
"""

import logging
import os
import shlex
import subprocess
from typing import Optional

from .exceptions import EmpowerError, LoginFailedException

logger = logging.getLogger(__name__)

DEFAULT_KEYRING_SERVICE = "empower-personal-dashboard"

# Optional import of keyring library
try:
    import keyring
except ImportError:
    keyring = None  # type: ignore[assignment]

_FALSY_ENV_VALUES = frozenset({"", "0", "false", "no", "off", "none"})


def _is_env_flag_set(value: Optional[str]) -> bool:
    """Return True if environment variable string represents a truthy flag."""
    if value is None:
        return False
    return value.strip().lower() not in _FALSY_ENV_VALUES


def is_keyring_available() -> bool:
    """Return True if the python `keyring` package is installed and importable."""
    return keyring is not None


def get_password_from_keyring(
    username: str,
    service_name: str = DEFAULT_KEYRING_SERVICE,
) -> Optional[str]:
    """Retrieve password from system keyring for the given username.

    Returns the password string if found, or None if keyring is unavailable,
    the password does not exist, or a backend error occurs.
    """
    if not is_keyring_available() or not username:
        return None

    try:
        pw = keyring.get_password(service_name, username)  # type: ignore[union-attr]
        if pw is not None:
            logger.debug("Successfully retrieved password from keyring for service '%s'", service_name)
        return pw
    except Exception as e:
        logger.debug("Failed to read password from keyring for %s: %s", username, e)
        return None


def set_password_in_keyring(
    username: str,
    password: str,
    service_name: str = DEFAULT_KEYRING_SERVICE,
) -> None:
    """Store password in system keyring for the given username.

    Raises:
        ImportError: If the `keyring` package is not installed.
        EmpowerError: If storing the password in the keyring fails.
    """
    if not is_keyring_available():
        raise ImportError(
            "The 'keyring' package is required to use the system keychain. "
            "Install it via: pip install 'empower-personal-dashboard[keyring]' or pip install keyring"
        )
    if not username:
        raise ValueError("Username cannot be empty when storing password in keyring.")

    try:
        keyring.set_password(service_name, username, password)  # type: ignore[union-attr]
        logger.debug("Successfully stored password in keyring for service '%s'", service_name)
    except Exception as e:
        raise EmpowerError(f"Failed to store password in keyring for '{username}': {e}") from e


def get_password_from_helper(
    command: str,
    timeout: float = 30.0,
) -> str:
    """Execute an external credential helper command to retrieve the password.

    The command is parsed using `shlex.split` and executed with `shell=False`
    for process isolation and security.

    Args:
        command: Command string (e.g. "pass show empower", "op read op://...").
        timeout: Maximum execution time in seconds (default: 30.0).

    Returns:
        The stripped password string from standard output.

    Raises:
        ValueError: If command is empty.
        LoginFailedException: If the helper fails, times out, or exits non-zero.
    """
    if not command or not command.strip():
        raise ValueError("Credential helper command cannot be empty.")

    try:
        cmd_args = shlex.split(command.strip())
    except ValueError as e:
        raise LoginFailedException(f"Invalid credential helper command: {e}") from e
    if not cmd_args:
        raise ValueError("Credential helper command cannot be empty.")
    helper_bin = cmd_args[0]

    try:
        proc = subprocess.run(
            cmd_args,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except FileNotFoundError as e:
        raise LoginFailedException(f"Command not found for credential helper '{helper_bin}': {e}") from e
    except subprocess.TimeoutExpired as e:
        raise LoginFailedException(f"Credential helper '{helper_bin}' timed out after {timeout}s") from e
    except Exception as e:
        raise LoginFailedException(f"Failed to execute credential helper '{helper_bin}': {e}") from e

    if proc.returncode != 0:
        err_msg = proc.stderr.strip() if proc.stderr else f"process exited with status {proc.returncode}"
        raise LoginFailedException(f"Credential helper '{helper_bin}' exited with status {proc.returncode}: {err_msg}")

    # Standard convention for password managers (pass, 1Password, etc.) outputs
    # the password on the first line, followed by optional metadata or keys.
    lines = proc.stdout.splitlines()
    return lines[0] if lines else ""


def resolve_password(
    cli_password: Optional[str] = None,
    username: Optional[str] = None,
    credential_helper: Optional[str] = None,
    use_keyring: bool = False,
    service_name: str = DEFAULT_KEYRING_SERVICE,
) -> Optional[str]:
    """Resolve password following hierarchical security precedence:

    1. Explicit CLI argument (`cli_password`)
    2. Environment variables (`EMPOWER_PASSWORD` or `PERSONAL_CAPITAL_PASSWORD`)
    3. External credential helper (`credential_helper` arg or `EMPOWER_CREDENTIAL_HELPER` env var)
    4. OS keyring (`use_keyring` flag or `EMPOWER_KEYRING` env var with `username`)
    5. Returns None if no credential source succeeded.
    """
    # 1. Explicit argument
    if cli_password:
        return cli_password

    # 2. Environment variable
    env_pw = os.environ.get("EMPOWER_PASSWORD") or os.environ.get("PERSONAL_CAPITAL_PASSWORD")
    if env_pw:
        return env_pw

    # 3. Credential helper
    helper_cmd = credential_helper or os.environ.get("EMPOWER_CREDENTIAL_HELPER")
    if helper_cmd:
        return get_password_from_helper(helper_cmd)

    # 4. OS Keyring
    keyring_enabled = use_keyring or _is_env_flag_set(os.environ.get("EMPOWER_KEYRING"))
    if keyring_enabled and username:
        return get_password_from_keyring(username, service_name=service_name)

    return None
