"""Managed block handling for the configuration directory's .gitignore."""

from __future__ import annotations

from pathlib import Path

from .const import (
    BLOCK_BEGIN,
    BLOCK_END,
    DEFAULT_IGNORE_ENTRIES,
    PROTECTED_ENTRIES,
)


def normalize_entry(raw: str) -> str:
    """Validate one user-supplied ignore entry."""
    entry = raw.strip()
    if not entry or "\n" in entry or "\r" in entry:
        raise ValueError("empty or multi-line entry")
    if entry.startswith(("#", "!")):
        # A negation could re-include a Protected Entry.
        raise ValueError("comments and negations are not allowed")
    return entry


def _split(text: str) -> tuple[list[str], list[str]]:
    """Return (lines outside the block, entries inside the block)."""
    outside: list[str] = []
    inside: list[str] = []
    in_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == BLOCK_BEGIN:
            in_block = True
        elif stripped == BLOCK_END:
            in_block = False
        elif in_block:
            if stripped and not stripped.startswith("#"):
                inside.append(stripped)
        else:
            outside.append(line)
    return outside, inside


def read_entries(text: str) -> list[str]:
    """Return the user-managed entries, excluding Protected Entries."""
    _, inside = _split(text)
    return [e for e in dict.fromkeys(inside) if e not in PROTECTED_ENTRIES]


def render(text: str, user_entries: list[str]) -> str:
    """Rewrite the managed block, placed last so later lines cannot negate it."""
    outside, _ = _split(text)
    while outside and not outside[-1].strip():
        outside.pop()
    entries = list(dict.fromkeys([*PROTECTED_ENTRIES, *user_entries]))
    lines = [*outside, *([""] if outside else []), BLOCK_BEGIN, *entries, BLOCK_END]
    return "\n".join(lines) + "\n"


def default_entries() -> list[str]:
    """Entries seeded on first setup."""
    return list(DEFAULT_IGNORE_ENTRIES)


def sync_file(
    path: Path,
    *,
    add: list[str] | None = None,
    remove: list[str] | None = None,
    seed: bool = False,
) -> bool:
    """Apply changes to the managed block in .gitignore; return True if rewritten."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    entries = read_entries(text)
    if seed and BLOCK_BEGIN not in text:
        entries = [*entries, *default_entries()]
    if remove:
        entries = [e for e in entries if e not in remove]
    for raw in add or []:
        entries.append(normalize_entry(raw))
    new_text = render(text, list(dict.fromkeys(entries)))
    if new_text == text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True
