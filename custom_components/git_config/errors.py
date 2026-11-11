"""Errors raised by the git layer."""

from __future__ import annotations

AUTH_FAILED = "auth_failed"
HOST_KEY_MISMATCH = "host_key_mismatch"
REMOTE_MISMATCH = "remote_mismatch"
BRANCH_MISMATCH = "branch_mismatch"
UNRELATED_HISTORY = "unrelated_history"
DIVERGED = "diverged"
PENDING_CHANGES = "pending_changes"
OVERWRITE = "overwrite"
PROTECTED_IN_REMOTE = "protected_in_remote"
PROTECTED_STAGED = "protected_staged"
BEHIND_REMOTE = "behind_remote"
NOT_FOUND = "not_found"
OPERATION_FAILED = "operation_failed"

# Codes that mean a pull was refused rather than failed.
PULL_REFUSAL_CODES = frozenset(
    {
        PENDING_CHANGES,
        DIVERGED,
        UNRELATED_HISTORY,
        PROTECTED_IN_REMOTE,
        OVERWRITE,
    }
)


class GitConfigError(Exception):
    """A git operation failed or was refused."""

    def __init__(self, code: str, detail: str = "") -> None:
        """Initialize with a machine-readable code and optional detail."""
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
