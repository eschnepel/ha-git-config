"""Constants for the Git Config integration."""

from datetime import timedelta
import re

DOMAIN = "git_config"

CONF_REMOTE_URL = "remote_url"
CONF_BRANCH = "branch"
CONF_AUTHOR_NAME = "author_name"
CONF_AUTHOR_EMAIL = "author_email"
CONF_PRIVATE_KEY = "private_key"
CONF_PUBLIC_KEY = "public_key"

DEFAULT_BRANCH = "main"
DEFAULT_AUTHOR_NAME = "Home Assistant"
DEFAULT_AUTHOR_EMAIL = "homeassistant@localhost"

# Only GitHub SSH remotes of the form git@github.com:owner/repo(.git).
REMOTE_URL_RE = re.compile(r"^git@github\.com:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")

STATUS_INTERVAL = timedelta(minutes=5)
FETCH_INTERVAL = timedelta(hours=1)

# Entries that may never leave the system; see ADR 0002.
PROTECTED_ENTRIES: tuple[str, ...] = (".storage/", "secrets.yaml")
DEFAULT_IGNORE_ENTRIES: tuple[str, ...] = (
    "*.db*",
    "*.log*",
    ".cloud/",
    "deps/",
    "tts/",
    "__pycache__/",
)
BLOCK_BEGIN = "# >>> git_config managed >>>"
BLOCK_END = "# <<< git_config managed <<<"

ISSUE_RESTART_REQUIRED = "restart_required"
ISSUE_CONFIG_INVALID = "config_invalid"
ISSUE_PULL_REFUSED = "pull_refused"
ISSUE_SECRETS_IN_HISTORY = "secrets_in_history"
ISSUE_AUTH_FAILED = "auth_failed"
ISSUE_HOST_KEY_MISMATCH = "host_key_mismatch"

SERVICE_COMMIT_PUSH = "commit_push"
SERVICE_FETCH = "fetch"
SERVICE_PULL = "pull"
ATTR_MESSAGE = "message"
