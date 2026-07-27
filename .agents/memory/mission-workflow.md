---
name: Mission workflow pattern
description: Per-agent mission loop, closure baseline chain, and delivery-report conventions for Alpha Intelligence OS missions.
---

# Mission workflow pattern

Missions arrive as spec files in `attached_assets/` (one per agent). Each agent follows:
implement → tests → architect code-review subagent (fix real findings; re-review only for substantial rework) → `git checkout -- alpha20_v1/` before full regression (running bot mutates configs) → full `python -m pytest -q` (~90–110s) → commit ONLY scoped files → `gitPush({branch:"main",provider:"github"})` → Turkish delivery report with the spec's exact `── SECTION ──` headers, ending "Executive Review bekle".

**Why:** the user runs a chained multi-agent mission process; deviating from the loop or headers breaks their executive review flow.

**How to apply:** on any new mission spec upload, repeat the loop. Report must include: new-test count, FAIL/SKIP 0/0, total regression, Exchange Write 0, Secret Exposure 0, commit hash + push OK, NEXT AGENT.

## Closure baseline chain (verified from git history)
- Mission 1700 closed at 1335 PASS (`05eb08a`)
- Mission 1800 closed at 1596 PASS (`327e160`)
- Mission 1900 closed at 2207 PASS (closure commit `a79415e`; completion pre-closure 2146 at `08a409b`)
- Next: Mission 2000 — Execution Foundation (exchange adapters, order model, risk engine, kill switch, dry run, spot execution; live trading DISABLED by default)

## Standing constraints (all missions)
Read-only architecture (exchange writes forever 0); Decimal-only money math (AST-tested, no float literals); unknown → null; sterile error codes only; no threads/schedulers; wall-clock/UUID only at API boundary; never `pkill gunicorn`; keep `attached_assets/` out of scoped commits; MappingProxyType → plain dict before json.dumps.
