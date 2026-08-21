from __future__ import annotations

import re
from pathlib import Path


def test_committed_project_files_do_not_contain_local_identity() -> None:
    root = Path(__file__).resolve().parents[1]
    project_roots = tuple(
        root / name for name in ("src", "tests", "docs", "scripts", "packaging", ".github")
    )
    included = [
        root / name
        for name in (
            "README.md",
            "THIRD_PARTY_NOTICES.md",
            "LICENSE",
            "config.example.toml",
            "pyproject.toml",
        )
    ]
    included.extend(
        path
        for project_root in project_roots
        for path in project_root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    forbidden = (
        re.compile(r"/" + r"Users/"),
        re.compile(r"\b[A-Z0-9._%+-]+@(?:gmail|outlook|yahoo)\.com\b", re.IGNORECASE),
    )
    for path in included:
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            assert not pattern.search(text), f"local identity marker found in {path}"
