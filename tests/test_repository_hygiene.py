from __future__ import annotations

from pathlib import Path


def test_committed_project_files_do_not_contain_local_identity() -> None:
    root = Path(__file__).resolve().parents[1]
    included = (
        root / "README.md",
        root / "LICENSE",
        root / "THIRD_PARTY_NOTICES.md",
        root / "config.example.toml",
        root / "pyproject.toml",
    )
    forbidden = ("/Users/", "jspray contributors")
    for path in included:
        text = path.read_text(encoding="utf-8")
        for value in forbidden:
            assert value not in text, f"local identity marker {value!r} found in {path.name}"
