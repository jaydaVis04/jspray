# Contributing

Contributions should preserve the project's Linux-only, Samsung-only, PDA-oriented scope.
Read [docs/scope.md](docs/scope.md) and [docs/architecture.md](docs/architecture.md) before
changing discovery, identity, or downloader behavior.

## Development checks

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check src tests
.venv/bin/mypy src
scripts/test-linux.sh
```

Tests must use mock metadata and tiny generated ZIPs. Never add real firmware packages,
credentials, captured authorization material, local databases, or private user/device data.
Security-sensitive changes should include negative tests for rejected input and redaction.

Add a new numbered SQL migration instead of editing a migration already included in a
release. Keep commits focused and do not mix generated files with source changes. Report
security defects privately using [SECURITY.md](SECURITY.md).
