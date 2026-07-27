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
- Mission 2000 closed at 4375 PASS (closure `03e181d`; Execution Core v1.0.0 CERTIFIED; core manifest baseline `01aa429`:3704 deliberately distinct)
- Mission 2100 (v1.1.0 "Controlled Execution") A01: Controlled Execution Foundation, commit `4304527`, regression 4619 PASS (244 new tests)
- Mission 2100 A02: Runtime Domain Models, commit `69bd05c`, regression 5215 PASS (596 new tests)
- Mission 2100 A03: Paper Broker & Ledger, commit `32f4a3a`, regression 5585 PASS (370 new tests; exact double-entry via cost_basis, IMMEDIATE_FULL_FILL only)

## Standing constraints (all missions)
Read-only architecture (exchange writes forever 0); Decimal-only money math (AST-tested, no float literals); unknown → null; sterile error codes only; no threads/schedulers; wall-clock/UUID only at API boundary; never `pkill gunicorn`; keep `attached_assets/` out of scoped commits; MappingProxyType → plain dict before json.dumps.
