# Provider-neutral refactor test loop 04

- **Command:** `pytest -q --strict-markers -m 'not e2e'`
- **Result:** `passed`
- **Attempt policy:** case attempts are bounded to at most 5; a case that remains failing after five attempts must be recorded and stopped.
- **Verified changes:** configuration-driven gateway registry/factory, strict evidence-only success, provider-neutral stats, and per-attempt evidence metadata.
- **Live provider checks:** not included; external provider/network failures remain separate and are never converted to pass.
- **Static checks:** focused Ruff on changed provider/case/evidence/benchmark files passed; full-repository Ruff remains noisy because of pre-existing unrelated violations.
