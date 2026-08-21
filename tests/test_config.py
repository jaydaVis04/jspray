from __future__ import annotations

from pathlib import Path

import pytest

from jayspray.config import ConfigurationError, load_config


@pytest.mark.parametrize("relative_path", ["config.example.toml", "tests/live/config.toml"])
def test_repository_configuration_examples_parse(relative_path: str) -> None:
    root = Path(__file__).resolve().parents[1]
    load_config(root / relative_path)


def test_loads_watch_targets(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        f"""
[paths]
database = "{tmp_path}/db.sqlite"
downloads = "{tmp_path}/downloads"
extracted = "{tmp_path}/extracted"
cache = "{tmp_path}/cache"
state = "{tmp_path}/state"

[[targets]]
model = "SM-S928U1"
csc = "XAA"

[[targets]]
model = "SM-S928U1"
csc = "EUX"
enabled = false

[discovery]
history_limit_per_target = 7
""",
        encoding="utf-8",
    )
    loaded = load_config(config)
    assert [(target.model, target.csc, target.enabled) for target in loaded.targets] == [
        ("SM-S928U1", "XAA", True),
        ("SM-S928U1", "EUX", False),
    ]
    assert loaded.discovery.history_limit_per_target == 7


def test_rejects_relative_storage_path(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[paths]\ndatabase = "relative.db"\n', encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(config)


def test_rejects_duplicate_model_csc_target(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        """
[[targets]]
model = "SM-S928U1"
csc = "XAA"

[[targets]]
model = "sm-s928u1"
csc = "xaa"
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="duplicate Samsung target"):
        load_config(config)


def test_rejects_relative_downloader_path(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[download]\nsamloader_executable = "samloader"\n', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="absolute path"):
        load_config(config)


def test_rejects_invalid_downloader_digest(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[download]\nsamloader_sha256 = "not-a-digest"\n', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="64-character hex digest"):
        load_config(config)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('[download]\nautomatic = "false"\n', "must be true or false"),
        ('[download]\nconcurrency = true\n', "must be an integer"),
        ('logging_level = "VERBOSE"\n', "logging_level must be"),
    ],
)
def test_rejects_unsafe_configuration_coercions(
    tmp_path: Path, body: str, message: str
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigurationError, match=message):
        load_config(config)
