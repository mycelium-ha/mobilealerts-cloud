# Contributing to Mobile Alerts Cloud

Contributions are welcome!

## Development Setup

1. Clone this repository and create a Python 3.14 virtual environment (required by Home Assistant):
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements_test.txt
   ```
2. Run the quality gate (the same checks as CI, see [AGENTS.md](AGENTS.md) section 7):
   ```bash
   ruff check custom_components tests scripts .claude/hooks
   ruff format --check custom_components tests scripts .claude/hooks
   pytest
   python3 scripts/check_translations.py
   mypy
   ```

## Architecture Guidelines

- Keep the API client and device model layer (`custom_components/mobilealerts_cloud/api/`) free of `homeassistant` imports so it remains independently testable.
- Never reuse the legacy `mobile_alerts` domain, event names, services, or device registry identifiers—everything in this integration must stay isolated under `mobilealerts_cloud`.
- Follow the official Mobile Alerts Public Server REST API specification (`POST`, `application/x-www-form-urlencoded`, rate-limit and invalid-call protection).
- Keep `strings.json`, `services.yaml`, and all 7 `translations/*.json` files in sync (`en`, `de`, `nl`, `fr`, `it`, `es`, `pl`).
- Add tests in `tests/` for any new device prefix (`IDxx`), measurement key, or API behavior. Tests run against real Home Assistant (`pytest-homeassistant-custom-component`) and the mock API in `tests/mock_api_server.py`; registry snapshots live in `tests/snapshots/` (update intentionally with `pytest --snapshot-update`).
- Claude Code users get project hooks from `.claude/settings.json` (no push to `main`, no commits with real-looking sensor IDs, ruff formatting after edits) and tiered agents in `.claude/agents/`; see AGENTS.md section 9. Other contributors are not affected.
- See [AGENTS.md](AGENTS.md) (binding rules), [docs/DECISIONS.md](docs/DECISIONS.md) (design decisions), [docs/API_SPEC_NOTES.md](docs/API_SPEC_NOTES.md) (API spec notes) and [docs/HA_INTEGRATION_STANDARDS.md](docs/HA_INTEGRATION_STANDARDS.md) (patterns).
