"""Work out how to bring the running system in line with files changed by a pull."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

from .git_ops import ChangedFile

CORE_DOMAIN = "homeassistant"
_FILE_INCLUDE = "!include"
_DIR_INCLUDES = {
    "!include_dir_list",
    "!include_dir_merge_list",
    "!include_dir_named",
    "!include_dir_merge_named",
}
_YAML_SUFFIXES = (".yaml", ".yml")
_MAX_INCLUDE_DEPTH = 5


@dataclass
class ApplyPlan:
    """What to reload, and why a restart is needed if it is."""

    reload_domains: set[str] = field(default_factory=set)
    restart_reasons: list[str] = field(default_factory=list)

    @property
    def needs_restart(self) -> bool:
        return bool(self.restart_reasons)


@dataclass
class _IncludeMap:
    files: dict[str, set[str]] = field(default_factory=dict)
    dirs: list[tuple[str, str]] = field(default_factory=list)
    package_files: set[str] = field(default_factory=set)
    package_dirs: list[str] = field(default_factory=list)
    unresolved_package_dirs: list[str] = field(default_factory=list)


def _domain(key: str) -> str:
    """`automation ui` and `automation manual` both belong to `automation`."""
    return key.split(" ", 1)[0]


def _compose(text: str) -> yaml.MappingNode | None:
    node = yaml.compose(text, Loader=yaml.SafeLoader)
    return node if isinstance(node, yaml.MappingNode) else None


def _rel(base: str, target: str) -> str:
    return PurePosixPath(base, target).as_posix() if base else PurePosixPath(target).as_posix()


def _walk_includes(node: yaml.Node) -> Iterable[tuple[str, str]]:
    if isinstance(node, yaml.ScalarNode):
        if node.tag == _FILE_INCLUDE or node.tag in _DIR_INCLUDES:
            yield node.tag, node.value
    elif isinstance(node, yaml.SequenceNode):
        for child in node.value:
            yield from _walk_includes(child)
    elif isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            yield from _walk_includes(key)
            yield from _walk_includes(value)


def _collect(
    config_dir: Path,
    base: str,
    node: yaml.Node,
    domain: str,
    out: _IncludeMap,
    depth: int = 0,
) -> None:
    """Record includes below `node` as belonging to `domain`, following nested includes."""
    for tag, target in _walk_includes(node):
        rel = _rel(base, target)
        if tag in _DIR_INCLUDES:
            out.dirs.append((rel.rstrip("/"), domain))
            continue
        out.files.setdefault(rel, set()).add(domain)
        if depth >= _MAX_INCLUDE_DEPTH:
            continue
        try:
            nested = yaml.compose(
                (config_dir / rel).read_text(encoding="utf-8"), Loader=yaml.SafeLoader
            )
        except (OSError, yaml.YAMLError):
            continue
        if nested is not None:
            parent = PurePosixPath(rel).parent.as_posix()
            _collect(config_dir, "" if parent == "." else parent, nested, domain, out, depth + 1)


def _collect_packages(node: yaml.Node, out: _IncludeMap) -> None:
    if isinstance(node, yaml.ScalarNode):
        if node.tag == "!include_dir_named":
            out.package_dirs.append(node.value.rstrip("/"))
        elif node.tag in _DIR_INCLUDES:
            out.unresolved_package_dirs.append(node.value.rstrip("/"))
    elif isinstance(node, yaml.MappingNode):
        for _, value in node.value:
            if isinstance(value, yaml.ScalarNode) and value.tag == _FILE_INCLUDE:
                out.package_files.add(PurePosixPath(value.value).as_posix())


def _build_include_map(config_dir: Path, root: yaml.MappingNode) -> _IncludeMap:
    out = _IncludeMap()
    for key_node, value_node in root.value:
        domain = _domain(str(key_node.value))
        if domain == CORE_DOMAIN and isinstance(value_node, yaml.MappingNode):
            for sub_key, sub_value in value_node.value:
                if sub_key.value == "packages":
                    _collect_packages(sub_value, out)
        _collect(config_dir, "", value_node, domain, out)
    return out


def _key_spans(text: str, root: yaml.MappingNode) -> dict[str, str]:
    spans: dict[str, str] = {}
    for key_node, value_node in root.value:
        spans[str(key_node.value)] = text[key_node.start_mark.index : value_node.end_mark.index]
    return spans


def _top_level_domains(text: str | None) -> set[str]:
    if not text:
        return set()
    try:
        root = _compose(text)
    except yaml.YAMLError:
        return set()
    return {_domain(str(k.value)) for k, _ in root.value} if root else set()


def _in_dir(path: str, directory: str) -> bool:
    return path.startswith(f"{directory}/") and path.endswith(_YAML_SUFFIXES)


def build_plan(
    config_dir: Path,
    changed: list[ChangedFile],
    has_reload: Callable[[str], bool],
) -> ApplyPlan:
    """Map changed files to reloadable domains; anything unresolved requires a restart."""
    plan = ApplyPlan()
    domains: set[str] = set()
    paths = {c.path: c for c in changed}

    include_map = _IncludeMap()
    config_text = ""
    try:
        config_text = (config_dir / "configuration.yaml").read_text(encoding="utf-8")
        root = _compose(config_text)
        if root is not None:
            include_map = _build_include_map(config_dir, root)
    except (OSError, yaml.YAMLError) as err:
        plan.restart_reasons.append(f"configuration.yaml could not be analysed: {err}")
        return plan

    for path, change in paths.items():
        if path.startswith("custom_components/"):
            plan.restart_reasons.append(path)
        elif path == "configuration.yaml":
            _diff_configuration(change, domains, plan)
        elif not path.endswith(_YAML_SUFFIXES):
            continue
        elif path in include_map.package_files or any(
            _in_dir(path, d) for d in include_map.package_dirs
        ):
            found = _top_level_domains(change.old_text) | _top_level_domains(change.new_text)
            if found:
                domains |= found
            else:
                plan.restart_reasons.append(path)
        elif any(_in_dir(path, d) for d in include_map.unresolved_package_dirs):
            plan.restart_reasons.append(path)
        elif path in include_map.files:
            domains |= include_map.files[path]
        elif matched := [dom for d, dom in include_map.dirs if _in_dir(path, d)]:
            domains.update(matched)
        elif path.startswith("blueprints/") and len(PurePosixPath(path).parts) > 2:
            domains.add(PurePosixPath(path).parts[1])
        else:
            plan.restart_reasons.append(path)

    for domain in sorted(domains):
        if has_reload(domain):
            plan.reload_domains.add(domain)
        else:
            plan.restart_reasons.append(f"{domain} cannot be reloaded")
    return plan


def _diff_configuration(change: ChangedFile, domains: set[str], plan: ApplyPlan) -> None:
    try:
        new_root = _compose(change.new_text or "")
        old_root = _compose(change.old_text or "") if change.old_text else None
    except yaml.YAMLError as err:
        plan.restart_reasons.append(f"configuration.yaml: {err}")
        return
    if new_root is None:
        plan.restart_reasons.append("configuration.yaml")
        return
    new_spans = _key_spans(change.new_text or "", new_root)
    old_spans = _key_spans(change.old_text or "", old_root) if old_root else {}
    for key in new_spans.keys() | old_spans.keys():
        if new_spans.get(key) == old_spans.get(key):
            continue
        if key == CORE_DOMAIN and "packages" in (new_spans.get(key, "") + old_spans.get(key, "")):
            plan.restart_reasons.append("homeassistant packages changed")
        else:
            domains.add(_domain(key))
