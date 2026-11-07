# Getting Started

## 1. Install

```bash
pip install -e ".[dev]"
```

## 2. API key (LLM stages)

Front-end stages (`idea_to_pico`, `novelty_check`, …) and cloud screening need an Anthropic key.

**Option A — `.env` in repo root (gitignored):**

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env
chmod 600 .env
```

`ese` loads `.env` automatically on startup.

**Option B — shell export:**

```bash
export ANTHROPIC_API_KEY='sk-ant-...'
```

**Option C — providers file (mode 0600):**

```bash
mkdir -p ~/.config/ese
printf 'ANTHROPIC_API_KEY=sk-ant-...\n' > ~/.config/ese/providers.env
chmod 600 ~/.config/ese/providers.env
```

## 3. Pick a vertical

```bash
ls verticals/
# systematic-review   — shared defaults
# mm-bcma-early-relapse — worked example
# heart-failure-sglt2   — non-oncology proof
```

## 4. Create a run

```bash
ese init --vertical mm-bcma-early-relapse --profile systematic
```

## 5. Drive the pipeline

```bash
RUN=runs/<id-from-init>

ese status $RUN
ese next $RUN        # repeat until blocked or complete

# When screen/extract await approval:
ese approve $RUN screen --actor "Your Name"
ese next $RUN
```

## 6. Export (after all gates clear)

```bash
ese export $RUN --approve-conclusions "Your Name"
```

Output: `~/Documents/EvidenceEngine/<vertical>/<title>_<date>/`

## New review from an idea

```bash
ese init --idea "Your research question in plain language"
ese next runs/<id>   # walks idea → PICO → novelty → feasibility → autoconfig
```

## R dependencies

```bash
Rscript -e 'install.packages(c("metafor", "netmeta"))'
```
