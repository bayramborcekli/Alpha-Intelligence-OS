---
name: Windows local auth separation
description: Non-Replit login source is data/local_admin.json only; env can never override; Replit Secrets flow untouched.
---

Rule: On non-Replit (Windows/local), admin login credentials come EXCLUSIVELY from `data/local_admin.json` (schema_version, username, password_hash, created_at; gitignored, 0600, atomic write, symlink refused). Environment variables (`ALPHA_OWNER_*`, `ADMIN_*`) are only a ONE-TIME migration source when the file does not exist — never when a file exists, even a corrupt one (corrupt = fail-closed, setup wizard reopens, file is never silently overwritten). On Replit, env/Secrets remain the only source and the file is ignored.

**Why:** Stale clones / system env vars were able to hijack Windows login; corrupt-file + env combo would otherwise silently re-enable access (architect flagged this bypass).

**How to apply:** Any new auth/setup code must branch on `local_admin.enabled()` (i.e. `not local_env.is_replit()`); tests simulate Windows by monkeypatching `local_env.is_replit` AND `local_admin.ROOT/DATA_DIR/FILE` to tmp_path (path-safety check requires FILE under ROOT).
