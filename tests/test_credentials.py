"""
test_credentials.py — Unit tests for OS keyring and pluggable credential helpers.
"""

import os
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from empower_personal_dashboard.credentials import (
    DEFAULT_KEYRING_SERVICE,
    get_password_from_helper,
    get_password_from_keyring,
    is_keyring_available,
    resolve_password,
    set_password_in_keyring,
)
from empower_personal_dashboard.exceptions import EmpowerError, LoginFailedException


class TestCredentials(unittest.TestCase):
    def test_is_keyring_available(self):
        # By default in this test environment, keyring should be available
        self.assertIsInstance(is_keyring_available(), bool)

    @patch("empower_personal_dashboard.credentials.keyring", None)
    def test_keyring_unavailable_get_returns_none(self):
        self.assertFalse(is_keyring_available())
        pw = get_password_from_keyring("user@example.com")
        self.assertIsNone(pw)

    @patch("empower_personal_dashboard.credentials.keyring", None)
    def test_keyring_unavailable_set_raises_importerror(self):
        with self.assertRaises(ImportError) as ctx:
            set_password_in_keyring("user@example.com", "secret123")
        self.assertIn("keyring", str(ctx.exception).lower())

    def test_get_password_from_keyring_success(self):
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = "synthetic-secret-pass"
        with patch("empower_personal_dashboard.credentials.keyring", mock_keyring):
            pw = get_password_from_keyring("user@example.com")
            self.assertEqual(pw, "synthetic-secret-pass")
            mock_keyring.get_password.assert_called_once_with(DEFAULT_KEYRING_SERVICE, "user@example.com")

    def test_get_password_from_keyring_custom_service(self):
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = "custom-pass"
        with patch("empower_personal_dashboard.credentials.keyring", mock_keyring):
            pw = get_password_from_keyring("user@example.com", service_name="custom-service")
            self.assertEqual(pw, "custom-pass")
            mock_keyring.get_password.assert_called_once_with("custom-service", "user@example.com")

    def test_get_password_from_keyring_not_found(self):
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = None
        with patch("empower_personal_dashboard.credentials.keyring", mock_keyring):
            pw = get_password_from_keyring("user@example.com")
            self.assertIsNone(pw)

    def test_get_password_from_keyring_backend_error_handled(self):
        mock_keyring = MagicMock()
        mock_keyring.get_password.side_effect = RuntimeError("Keyring locked or unavailable")
        with patch("empower_personal_dashboard.credentials.keyring", mock_keyring):
            pw = get_password_from_keyring("user@example.com")
            self.assertIsNone(pw)

    def test_set_password_in_keyring_success(self):
        mock_keyring = MagicMock()
        with patch("empower_personal_dashboard.credentials.keyring", mock_keyring):
            set_password_in_keyring("user@example.com", "synthetic-secret-pass")
            mock_keyring.set_password.assert_called_once_with(
                DEFAULT_KEYRING_SERVICE, "user@example.com", "synthetic-secret-pass"
            )

    def test_set_password_in_keyring_backend_error(self):
        mock_keyring = MagicMock()
        mock_keyring.set_password.side_effect = RuntimeError("Keychain access denied")
        with patch("empower_personal_dashboard.credentials.keyring", mock_keyring):
            with self.assertRaises(EmpowerError) as ctx:
                set_password_in_keyring("user@example.com", "synthetic-secret-pass")
            self.assertIn("Failed to store password in keyring", str(ctx.exception))

    @patch("subprocess.run")
    def test_get_password_from_helper_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="my-secret-vault-password\n", stderr="")
        pw = get_password_from_helper("pass show finance/empower")
        self.assertEqual(pw, "my-secret-vault-password")
        mock_run.assert_called_once()
        cmd_arg = mock_run.call_args[0][0]
        self.assertEqual(cmd_arg, ["pass", "show", "finance/empower"])
        self.assertFalse(mock_run.call_args[1].get("shell", False))

    @patch("subprocess.run")
    def test_get_password_from_helper_strips_crlf(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="op-secret\r\n", stderr="")
        pw = get_password_from_helper("op read op://finance/empower/password")
        self.assertEqual(pw, "op-secret")

    def test_get_password_from_helper_empty_command(self):
        with self.assertRaises(ValueError):
            get_password_from_helper("")

    @patch("subprocess.run")
    def test_get_password_from_helper_nonzero_exit(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Entry finance/empower not found")
        with self.assertRaises(LoginFailedException) as ctx:
            get_password_from_helper("pass show finance/empower")
        self.assertIn("exited with status 1", str(ctx.exception))
        self.assertIn("Entry finance/empower not found", str(ctx.exception))

    @patch("subprocess.run")
    def test_get_password_from_helper_command_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError("No such file or directory: 'lpass'")
        with self.assertRaises(LoginFailedException) as ctx:
            get_password_from_helper("lpass show --password empower")
        self.assertIn("Command not found", str(ctx.exception))

    @patch("subprocess.run")
    def test_get_password_from_helper_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="op read ...", timeout=30.0)
        with self.assertRaises(LoginFailedException) as ctx:
            get_password_from_helper("op read op://finance/empower/password", timeout=30.0)
        self.assertIn("timed out", str(ctx.exception))

    @patch("subprocess.run")
    def test_get_password_from_helper_multiline_takes_first_line(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="my-secret-vault-password\nusername: user@example.com\nurl: https://empower.com\n",
            stderr="",
        )
        pw = get_password_from_helper("pass show finance/empower")
        self.assertEqual(pw, "my-secret-vault-password")

    def test_get_password_from_helper_unbalanced_quotes(self):
        with self.assertRaises(LoginFailedException) as ctx:
            get_password_from_helper('op read "unbalanced quote')
        self.assertIn("Invalid credential helper command", str(ctx.exception))

    def test_resolve_password_explicit_cli(self):
        pw = resolve_password(
            cli_password="cli-provided-password",
            username="user@example.com",
            credential_helper="pass show dummy",
            use_keyring=True,
        )
        self.assertEqual(pw, "cli-provided-password")

    @patch.dict(os.environ, {"EMPOWER_PASSWORD": "env-empower-password"})
    def test_resolve_password_env_var(self):
        pw = resolve_password(
            username="user@example.com",
            credential_helper="pass show dummy",
            use_keyring=True,
        )
        self.assertEqual(pw, "env-empower-password")

    @patch.dict(os.environ, {"PERSONAL_CAPITAL_PASSWORD": "env-pc-password"}, clear=True)
    def test_resolve_password_personal_capital_env_var(self):
        pw = resolve_password(
            username="user@example.com",
            credential_helper="pass show dummy",
            use_keyring=True,
        )
        self.assertEqual(pw, "env-pc-password")

    @patch("empower_personal_dashboard.credentials.get_password_from_helper")
    def test_resolve_password_credential_helper(self, mock_helper):
        mock_helper.return_value = "helper-password"
        with patch.dict(os.environ, {}, clear=True):
            pw = resolve_password(
                username="user@example.com",
                credential_helper="op read op://empower/pw",
                use_keyring=True,
            )
            self.assertEqual(pw, "helper-password")
            mock_helper.assert_called_once_with("op read op://empower/pw")

    @patch("empower_personal_dashboard.credentials.get_password_from_helper")
    @patch.dict(os.environ, {"EMPOWER_CREDENTIAL_HELPER": "pass show empower"}, clear=True)
    def test_resolve_password_env_credential_helper(self, mock_helper):
        mock_helper.return_value = "env-helper-password"
        pw = resolve_password(
            username="user@example.com",
            use_keyring=True,
        )
        self.assertEqual(pw, "env-helper-password")
        mock_helper.assert_called_once_with("pass show empower")

    @patch("empower_personal_dashboard.credentials.get_password_from_keyring")
    def test_resolve_password_keyring(self, mock_keyring):
        mock_keyring.return_value = "keyring-password"
        with patch.dict(os.environ, {}, clear=True):
            pw = resolve_password(
                username="user@example.com",
                use_keyring=True,
            )
            self.assertEqual(pw, "keyring-password")
            mock_keyring.assert_called_once_with("user@example.com", service_name="empower-personal-dashboard")

    @patch("empower_personal_dashboard.credentials.get_password_from_keyring")
    @patch.dict(os.environ, {"EMPOWER_KEYRING": "1"}, clear=True)
    def test_resolve_password_env_keyring(self, mock_keyring):
        mock_keyring.return_value = "env-keyring-password"
        pw = resolve_password(username="user@example.com")
        self.assertEqual(pw, "env-keyring-password")
        mock_keyring.assert_called_once_with("user@example.com", service_name="empower-personal-dashboard")

    def test_resolve_password_none_when_unspecified(self):
        with patch.dict(os.environ, {}, clear=True):
            pw = resolve_password(username="user@example.com")
            self.assertIsNone(pw)


if __name__ == "__main__":
    unittest.main()
