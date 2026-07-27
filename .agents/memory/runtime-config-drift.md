---
name: Runtime config drift into git baseline
description: The running bot mutates alpha20_v1 config files; platform auto-commits can bake unsafe runtime state into the committed baseline.
---
The live bot rewrites `alpha20_v1/config.json` and `alpha20_v1/smart_config.json` at runtime (e.g. flipping `adaptive_system.enabled` or advisory mode to automatic). Platform auto-commits ("Update configuration...") can capture that mutated state, silently regressing paper-safety defaults and breaking `tests/test_adaptive.py` guards.

**Why:** Happened during Mission 1700 (Agents 03–04): an auto-commit baked `adaptive_system.enabled: true` into HEAD; `git checkout --` no longer helped because HEAD itself was wrong. Fix was `git show <last-intentional-commit>:alpha20_v1/config.json > alpha20_v1/config.json` and committing the revert.

**How to apply:** Before each regression run, revert `alpha20_v1/config.json` + `smart_config.json`; if the guard test still fails with a clean tree, diff the file against the last *intentional* commit — the drift may already be committed. Keep `adaptive_system.enabled=false` as baseline. For a fully clean regression, the workflow can mutate configs mid-run; re-check after.
