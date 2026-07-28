---
name: Test env isolation for exchange creds
description: Why credential-related tests flake in the full suite and how the sanitizer must be maintained
---
Rule: any code path that writes credentials **directly** into `os.environ` (e.g. the project-env loader) leaks past `monkeypatch` — monkeypatch only restores keys it saw at delenv/setenv time, so keys created by production code during a test survive into later tests.

**Why:** a legacy-name warning test loaded a fake `.env`, the loader set legacy `BINANCE_API_KEY/SECRET` into `os.environ`, and the credential resolver later treated them as valid Global creds — flaking an auth security test thousands of tests later.

**How to apply:** the autouse sanitizer in `tests/conftest.py` must delete EVERY alias the credential resolver recognizes (canonical + all legacy spellings). When adding a new alias to the resolver, add it to the sanitizer too. Tests that trigger the env loader should scrub `BINANCE*` keys from `os.environ` in fixture teardown.
