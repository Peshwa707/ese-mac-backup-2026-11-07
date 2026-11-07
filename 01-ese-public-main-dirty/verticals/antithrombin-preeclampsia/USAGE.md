# Running this vertical

> The four gotchas that bit the first run (2026-07-09) are now **FIXED in the engine**
> (see the run's ISA changelog + the engine ISA.md). The vertical is persisted in the
> run and `providers.env` is auto-loaded, so the simple flow below Just Works — no
> `export ESE_VERTICAL` and no manual `source` needed.

```bash
cd ~/Projects/evidence-synthesis-engine
source .venv/bin/activate

RUN=$(ese init --vertical antithrombin-preeclampsia --profile systematic | tail -1)
ese next "$RUN"      # search   (free, live PubMed/EuropePMC/CT.gov)
ese next "$RUN"      # prescreen (free, rule-based)
ese next "$RUN"      # screen   (LLM = AI Reviewer 1; stops at honesty gate)
ese approve "$RUN" screen --actor "Osama"           # human Reviewer 2 sign-off — required
ese next "$RUN"      # fulltext … extract (2nd honesty gate) … rob … stats … grade … manuscript
ese approve "$RUN" extract --actor "Osama"
ese export "$RUN" --approve-conclusions "Osama"     # → ~/Documents/EvidenceEngine/antithrombin-preeclampsia/
```

Good practice (belt-and-suspenders): still eyeball the corpus topic before the paid
screen stage — `grep -c antithrombin runs/$RUN/corpus.json`.

## What was fixed in the engine (2026-07-09)

1. **Vertical is persisted in `run_state.json`** and every `ese` subcommand resolves it
   from the run (precedence: `--vertical` > persisted > env/default). A run can no longer
   be silently screened/graded against another review's PICO.
2. **`providers.env` is auto-loaded** (hardened: 0600 / no-symlink / owner-checked).
3. **`ese export` no longer crashes** on `cfg.vertical`, and refuses (fail-closed) to
   export a pre-pinning legacy run without an explicit `--vertical`.
4. **GRADE uses this vertical's own outcomes** (via `grade_config.yaml` here) — no more
   myeloma outcome list leaking in from the shared default.

## Two more gotchas found completing the run (2026-07-09)

- **GRADE falls back to the shared MM-BCMA config.** This vertical has no `grade_config.yaml`,
  so `shared_or_vertical()` uses `verticals/systematic-review/grade_config.yaml` — whose outcome
  list is myeloma (ORR/CR/MRD/PFS/CRS/ICANS...). The grade stage therefore "grades" myeloma
  outcomes with n_studies=0. Add a domain `grade_config.yaml` (+ optional `rob_config.yaml`) to
  this vertical before trusting the GRADE table. Stats itself was honest (nothing poolable at
  abstract level; "none fabricated").
- **`ese export` is broken:** raises `'SRConfig' object has no attribute 'vertical'`
  (cli.py:103 `load_run_bundle(run_dir, vertical=cfg.vertical)` — SRConfig has no `.vertical`).
  All 11 stages complete and the export gate clears, but the prose-manuscript assembly cannot run
  via the CLI until that attribute bug is fixed. Manuscript was authored directly from the
  verified artifacts (extractions.json, screening.json, corpus.json) instead.
