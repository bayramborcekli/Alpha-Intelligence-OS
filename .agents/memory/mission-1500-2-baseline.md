---
name: Mission 1500.2 baseline directives
description: Post-closure rules — 1500.2 Workspace is a frozen reference baseline; constraints on all future missions.
---

MISSION 1500.2 (Intelligence Workspace) is CLOSED and declared the official BASELINE (2026-07-26; final commit `24edfe0`, 980 PASS / 0 FAIL / 0 SKIP).

**Rules for all future work:**
- Closed scope is never reopened: timeline, workspace service/API/UI/export, security model, regression suite, and 1500.2 docs are frozen; no agent works on a closed mission.
- Immutable contracts: workspace read-only; timeline append-only; no exchange orders, ledger or audit writes from workspace; recommendations advisory-only; Risk Engine is the sole calculation authority.
- New features only under a new mission. 1500.2 commit history untouched. If a new mission must break 1500.2 behavior, it must be explicitly declared a breaking change.
- Every new mission ends with full 1400 + 1500.1 + 1500.2 regression; no PASS → no closure.
- Security guarantees preserved: read-only, append-only, Decimal integrity, deterministic output, exchange isolation, ledger/audit immutability, secret protection, CSP, XSS, auth/authorization.
- New missions do not edit 1500.2 docs beyond adding reference links; past mission records are never deleted.

**Why:** Owner issued formal post-closure directives making 1500.2 the reference version.
**How to apply:** Check these constraints before scoping or implementing anything touching workspace components or the mission workflow.
