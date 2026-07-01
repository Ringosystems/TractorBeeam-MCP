# Contributing

Thanks for your interest in TractorBeeam365 MCP. This is an independent project
and is not affiliated with Veeam Software or Microsoft.

## Development setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install pytest          # for the test suite
python -m pytest -q tests/
```

## Design rules to preserve

These invariants are load-bearing for safety. Keep them when you change code.

- **Read-only stays read-only.** The always-on review tools only ever issue HTTP
  GET. The single mutating primitive (`vb365.action` / `vb365.download`) must only
  be reached through the opt-in action and restore tiers.
- **Gate every write twice.** A new mutating tool must be registered only when its
  category flag is enabled (`actions.category_enabled`) AND re-check that flag at
  call time, then use the two-call confirm-token pattern (`actions.prepare` /
  `actions.consume`). The token binds the exact payload, so the second call cannot
  be escalated to a different operation.
- **Never leak credentials.** Secrets come from the environment only. Do not log
  them, return them in tool output, write them to the audit log, or echo them in a
  preview.
- **Client-supplied paths are relative.** Do not reintroduce absolute-URL handling
  into `vb365.py`; resource paths must resolve under the configured VB365 host
  only.
- **Verify endpoints against the OpenAPI spec.** New VB365 REST paths in
  `endpoints.py` must be confirmed against the live swagger spec, not guessed.

## Pull requests

- Keep changes focused and matched to the surrounding style.
- Add or update tests in `tests/` for analysis and gating logic.
- Do not commit `.env`, real hostnames, tenant data, credentials, or restored
  data. Personal or troubleshooting scripts belong outside the repo.

## Releasing

Bump `__version__` in `tractorbeeam365/__init__.py`, update `CHANGELOG.md`, and
create a GitHub Release tagged `vX.Y.Z`. That triggers the multi-arch publish to
GHCR, Docker Hub, and the MCP Registry. The full runbook, one-time setup, and
troubleshooting are in [PUBLISHING.md](PUBLISHING.md).
