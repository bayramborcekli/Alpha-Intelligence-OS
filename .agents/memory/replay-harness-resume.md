---
name: Replay harness resume determinism
description: Rules for safe checkpoint/resume in mission replay harnesses
---
Rule: a replay harness resume must (1) re-seek cursors by candle open_time anchor (indices shift as "latest N pages" data moves), refusing to resume if the anchor is missing; (2) persist scheduler state (loop counter driving symbol/side alternation); (3) truncate the isolated trade-history file to the snapshot's trade count on resume — the engine appends history between checkpoints, so after a crash the file is AHEAD of the snapshot and otherwise triggers a false ledger mismatch; refuse if history is shorter or pnls disagree.

**Why:** a kill-then-resume test produced a false ledger_mismatch=1 purely from history/snapshot misalignment; code review also flagged silent data-drift risk in index-based cursors.

**How to apply:** any mission harness with checkpoint/resume (mission100/1000 pattern). Also keep the validation oracle's fee rate as a harness-local constant, not imported from the engine, so the oracle stays independent.
