"""Tests for mapping changed files to reloads or a restart."""

from __future__ import annotations

from pathlib import Path

from custom_components.git_config.apply import build_plan
from custom_components.git_config.git_ops import ChangedFile

RELOADABLE = {"automation", "script", "scene", "template", "input_boolean", "homeassistant"}


def has_reload(domain: str) -> bool:
    return domain in RELOADABLE


CONFIG = """\
homeassistant:
  name: Home
  packages: !include_dir_named packages/
automation ui: !include automations.yaml
automation manual: !include_dir_merge_list automations/
template: !include templates.yaml
script: !include_dir_named scripts/
logger:
  default: warning
input_boolean:
  a:
"""


def setup(config_dir: Path, config: str = CONFIG) -> None:
    (config_dir / "configuration.yaml").write_text(config)
    (config_dir / "templates.yaml").write_text("- sensor: []\n")


def plan(config_dir: Path, *changes: ChangedFile):
    return build_plan(config_dir, list(changes), has_reload)


def test_included_file_maps_to_domain(tmp_path: Path) -> None:
    setup(tmp_path)
    result = plan(tmp_path, ChangedFile("templates.yaml"), ChangedFile("automations.yaml"))
    assert result.reload_domains == {"template", "automation"}
    assert not result.needs_restart


def test_included_directory_maps_to_domain(tmp_path: Path) -> None:
    setup(tmp_path)
    result = plan(tmp_path, ChangedFile("automations/lights/evening.yaml"), ChangedFile("scripts/x.yaml"))
    assert result.reload_domains == {"automation", "script"}


def test_nested_include_follows_domain(tmp_path: Path) -> None:
    setup(tmp_path, "template: !include templates.yaml\n")
    (tmp_path / "templates.yaml").write_text("- !include sensors/a.yaml\n")
    result = plan(tmp_path, ChangedFile("sensors/a.yaml"))
    assert result.reload_domains == {"template"}


def test_configuration_key_diff(tmp_path: Path) -> None:
    old = CONFIG
    new = CONFIG.replace("a:\n", "a:\n  b:\n")
    setup(tmp_path, new)
    result = plan(tmp_path, ChangedFile("configuration.yaml", old, new))
    assert result.reload_domains == {"input_boolean"}
    assert not result.needs_restart


def test_configuration_non_reloadable_key_requires_restart(tmp_path: Path) -> None:
    old = CONFIG
    new = CONFIG.replace("warning", "debug")
    setup(tmp_path, new)
    result = plan(tmp_path, ChangedFile("configuration.yaml", old, new))
    assert result.needs_restart
    assert "logger cannot be reloaded" in result.restart_reasons


def test_configuration_core_key_reloads_core(tmp_path: Path) -> None:
    old = CONFIG.replace("packages: !include_dir_named packages/\n", "")
    new = old.replace("name: Home", "name: House")
    setup(tmp_path, new)
    result = plan(tmp_path, ChangedFile("configuration.yaml", old, new))
    assert result.reload_domains == {"homeassistant"}


def test_core_packages_change_requires_restart(tmp_path: Path) -> None:
    new = CONFIG.replace("packages/", "pkgs/")
    setup(tmp_path, new)
    result = plan(tmp_path, ChangedFile("configuration.yaml", CONFIG, new))
    assert result.needs_restart


def test_package_file_domains(tmp_path: Path) -> None:
    setup(tmp_path)
    result = plan(
        tmp_path,
        ChangedFile("packages/lights.yaml", "scene: []\n", "scene: []\ninput_boolean: {}\n"),
    )
    assert result.reload_domains == {"scene", "input_boolean"}


def test_package_with_non_reloadable_domain(tmp_path: Path) -> None:
    setup(tmp_path)
    result = plan(tmp_path, ChangedFile("packages/log.yaml", None, "logger: {}\n"))
    assert result.needs_restart


def test_custom_components_require_restart(tmp_path: Path) -> None:
    setup(tmp_path)
    result = plan(tmp_path, ChangedFile("custom_components/foo/__init__.py"))
    assert result.restart_reasons == ["custom_components/foo/__init__.py"]


def test_unreferenced_yaml_requires_restart(tmp_path: Path) -> None:
    setup(tmp_path)
    assert plan(tmp_path, ChangedFile("mystery.yaml")).needs_restart


def test_non_yaml_files_need_no_action(tmp_path: Path) -> None:
    setup(tmp_path)
    result = plan(tmp_path, ChangedFile("README.md"), ChangedFile("www/logo.png"))
    assert not result.needs_restart and not result.reload_domains


def test_blueprints_reload_their_domain(tmp_path: Path) -> None:
    setup(tmp_path)
    result = plan(tmp_path, ChangedFile("blueprints/automation/me/motion.yaml"))
    assert result.reload_domains == {"automation"}
