from __future__ import annotations

from pathlib import Path

import pytest

from fwtool.config import ConfigurationError, load_config


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
