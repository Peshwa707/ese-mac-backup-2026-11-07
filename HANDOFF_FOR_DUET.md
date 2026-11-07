# Handoff for Duet: ESE copies that were only on the Mac

Private repo. Created 2026-11-07. Snapshots are file copies (no git history), made from tracked plus untracked-not-ignored files. Left out on purpose: `.env`, `providers.env`, `.ese/` (signing key), `runs/`, `.dual-review/`, `.agenttalk/`, caches, key and database files.

| Folder | Source on the Mac | What it holds |
|---|---|---|
| `01-ese-public-main-dirty/` | `Projects/evidence-synthesis-engine` (main, commit cbf8297 plus 2 uncommitted changes) | Main engine incl. an uncommitted `core/tools/sr_io.py` edit and a pilot-workflow plan |
| `02-ese-neurodevelopmental-review-dirty/` | `Projects/evidence-synthesis-engine-neurodevelopmental-review` (commit 3f13858 plus 36 uncommitted files) | Neurodevelopmental mismatch review work, new `verticals/ndem-*`, review outputs |
| `03-ese-webui-offline-build/` | `Projects/evidence-synthesis-engine-webui` (branch codex/webui-offline-build, 40 uncommitted files) | Private offline web UI for screen-prove; 675 tests passed |
| `04-ese-engine-june/` | `Projects/ese-engine` (main, commit c019813) | June 2026 parallel carve of the engine; its tests stopped at first failure because `openai` is not installed here |

Already on GitHub (private), not copied here:
- `Peshwa707/evidence-synthesis-engine-screen-prove`, branch `backup/mac-2026-11-07` (348 tests pass; see `MAC_INVENTORY.md`).
- `Peshwa707/amanah-evidence-onc` and `Peshwa707/onco-evidence-engine`, branch `backup/mac-2026-11-07` in each.

Open items for the owner:
- The public repo `Peshwa707/evidence-synthesis-engine` has not been pushed to. The proposal is to push only `feat/ese-recover` (one commit).
- Screen-prove numbers its work packages differently from the request; see `MAC_INVENTORY.md`.

## Added later (same repo)

| Folder | Source | Notes |
|---|---|---|
| `05-ese-screen-prove/` | `Projects/evidence-synthesis-engine-screen-prove`, branch `backup/mac-2026-11-07` | Offline screen-and-prove tool; 348 tests pass |
| `06-amanah-evidence-onc/` | `Projects/amanah-evidence-onc`, branch `backup/mac-2026-11-07` | Older oncology-named predecessor; 685 tests pass |
| `07-onco-evidence-engine/` | `Projects/onco-evidence-engine`, branch `backup/mac-2026-11-07` | Older predecessor; 649 tests pass |

Withheld from the public copy: the unpublished MM-BCMA project material (protocol document, `reviews/mm-bcma-early-relapse/`, `verticals/mm-bcma-*`). It stays in the private repos.
