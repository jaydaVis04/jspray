from __future__ import annotations

from pathlib import Path

import pytest

from jayspray.config import ConfigurationError, load_config


@pytest.mark.parametrize("relative_path", ["config.example.toml", "tests/live/config.toml"])
def test_repository_configuration_examples_parse(relative_path: str) -> None:
    root = Path(__file__).resolve().parents[1]
    load_config(root / relative_path)


def test_loads_discovery_window(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        f"""
[paths]
database = "{tmp_path}/db.sqlite"
downloads = "{tmp_path}/downloads"
extracted = "{tmp_path}/extracted"
cache = "{tmp_path}/cache"
state = "{tmp_path}/state"

[discovery]
lookback_days = 21
""",
        encoding="utf-8",
    )
    loaded = load_config(config)
    assert loaded.discovery.lookback_days == 21
    assert loaded.discovery.sources == ("samfrew", "sammobile")
    assert loaded.metadata.path == Path("/var/lib/jayspray/metadata.json")
    assert loaded.metadata.append_completed


def test_rejects_relative_storage_path(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[paths]\ndatabase = "relative.db"\n', encoding="utf-8")
    with pytest.raises(ConfigurationError):
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


def test_metadata_append_requires_a_path(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[metadata]\npath = ""\nappend_completed = true\n', encoding="utf-8"
    )
    with pytest.raises(ConfigurationError, match=r"requires metadata\.path"):
        load_config(config)


def test_automatic_metadata_append_requires_automatic_extraction(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        "[download]\nautomatic = true\nautomatic_extract = false\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ConfigurationError, match=r"requires download\.automatic_extract"
    ):
        load_config(config)


def test_rejects_unknown_or_duplicate_discovery_sources(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[discovery]\nsources = ["samfrew", "samfrew"]\n', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="must not contain duplicates"):
        load_config(config)
    config.write_text('[discovery]\nsources = ["unknown"]\n', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="unsupported discovery source"):
        load_config(config)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('[download]\nautomatic = "false"\n', "must be true or false"),
        ("[download]\nconcurrency = true\n", "must be an integer"),
        ('[http]\nuser_agent = ["bad"]\n', "must be a string"),
        ('logging_level = "VERBOSE"\n', "logging_level must be"),
    ],
)
def test_rejects_unsafe_configuration_coercions(tmp_path: Path, body: str, message: str) -> None:
    config = tmp_path / "config.toml"
    config.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigurationError, match=message):
        load_config(config)
