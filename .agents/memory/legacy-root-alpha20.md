---
name: Legacy root alpha20.py shadows the real one
description: Import-resolution trap — a stale copy of alpha20.py sits at the repo root
---

A stale initial-commit copy of `alpha20.py` lives at the repo root; the live module is `alpha20_v1/alpha20.py`. Runtime (app.py) and existing tests insert `alpha20_v1` at `sys.path[0]` before `import alpha20`.

**Why:** A test that does a plain `import alpha20` picks up the root copy (missing newer functions) and, via the `sys.modules` cache, poisons every later test in the same pytest run — causing mass unrelated failures.

**How to apply:** Any new file that imports `alpha20`, `market_regime`, etc. must first do `sys.path.insert(0, <root>/alpha20_v1)` (copy the pattern from tests/test_ssl_diagnostics.py). Rate-limit backoff state is shared across processes via flock+JSON at `alpha20_v1/rate_limit_state.json`; tests get an isolated tmp path via the autouse conftest fixture.
