"""
empower_personal_dashboard — Python client & CLI for Empower Personal Dashboard.

Export all core models, exceptions, and client.
"""

from .client import (
    DEFAULT_BASE_URL,
    DEFAULT_SESSION_FILE,
    MIGRATED_BASE_URL,
    EmpowerDashboardClient,
)
from .exceptions import (
    EmpowerError,
    LedgerAppendError,
    LoginFailedException,
    ReconstructionWindowError,
    RequireTwoFactorException,
    SessionExpiredError,
)
from .models import (
    AccountBalance,
    DailyHistoryPoint,
    DashboardBalances,
    DashboardHistories,
    DashboardHoldings,
    DashboardTransactions,
    InvestmentHolding,
    Transaction,
)
from .beancount import (
    BeancountGenerator,
    BeancountMapper,
    slugify_account_name,
)
from .credentials import (
    DEFAULT_KEYRING_SERVICE,
    get_password_from_helper,
    get_password_from_keyring,
    is_keyring_available,
    resolve_password,
    set_password_in_keyring,
)
from .sanitizers import clean_api_text

__version__ = "0.8.0"

def create_mcp_server(*args, **kwargs):
    """Lazy loader for creating the FastMCP server instance."""
    from .mcp_server import create_mcp_server as _create_server
    return _create_server(*args, **kwargs)


__all__ = [
    "EmpowerDashboardClient",
    "AccountBalance",
    "DailyHistoryPoint",
    "DashboardBalances",
    "DashboardHistories",
    "InvestmentHolding",
    "DashboardHoldings",
    "Transaction",
    "DashboardTransactions",
    "EmpowerError",
    "RequireTwoFactorException",
    "SessionExpiredError",
    "LoginFailedException",
    "ReconstructionWindowError",
    "LedgerAppendError",
    "clean_api_text",
    "DEFAULT_BASE_URL",
    "MIGRATED_BASE_URL",
    "DEFAULT_SESSION_FILE",
    "DEFAULT_KEYRING_SERVICE",
    "get_password_from_helper",
    "get_password_from_keyring",
    "set_password_in_keyring",
    "resolve_password",
    "is_keyring_available",
    "create_mcp_server",
    "BeancountGenerator",
    "BeancountMapper",
    "slugify_account_name",
]


