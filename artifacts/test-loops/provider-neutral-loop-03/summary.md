# Provider-neutral refactor test loop 03

- **Scope:** complete gateway identity enforcement, decision identity metadata, generic stats/provenance/budget contracts, post-action reanalysis.
- **Command:** `pytest -q --strict-markers -m 'not e2e'`
- **Result:** `423 passed, 4 deselected`
- **Static checks:** focused Ruff and `python -m compileall -q hpcu tests` passed.
- **Safety:** model identity is now checked as configured provider/model metadata; concrete MiniMax transport details remain confined to the MiniMax adapter and default runtime configuration.
- **Live check:** the opt-in live provider test remains an external `httpx.ReadTimeout` and is not treated as a code-level success.
- **Remaining work:** remove stale MiniMax wording from planning/test docs, implement generic provider factory/registry and terminal/OpenCode physical runner, add screenshot manifests and bounded per-case reports.
