# Design: The Evidence Tool Platform

*Written in plain language on purpose — so a non-engineer can read, understand, and steer it.*

## 1. What we're building and why

We are turning the existing evidence engine into a **product** that works like OpenEvidence, but with one defining difference:

> For **every question you ask**, it runs a **fresh, full literature search and analysis** — live, current, and fully traceable — then lays the evidence out clearly and **lets the human draw the final conclusion.** It never states the final call for you.

OpenEvidence answers fast because it reads from a pre-built library and trusts you to accept its summary. Our product is more rigorous: it searches the live literature for each question, can show you exactly which studies it used and the math behind every number, and — by design — stops short of the conclusion so a human stays responsible for the decision.

Most of the machinery for this already exists and is tested (live search → screen → extract → pool → summarize, plus the human-sign-off lock built in Phase 1). This design is the **packaging layer** that turns those capabilities into a clean, safe, usable product. It is a **later phase**, built *after* the current capability work (the cloud/local dial is done; the new statistics and the smart-ranking helper come next).

## 2. The one principle that runs through everything

**Everything graduates on evidence.** Nothing is trusted by default; each part earns more autonomy by passing validation. This single idea shows up in three places:

- A **tool** earns the right to be run automatically by a future auto-pilot once it passes its tests and benchmarks.
- An **AI tool's "brain"** earns the switch from the cloud (Claude) to a free local AI model once the local version passes the same benchmark.
- **Quick Look** (the fast mode) earns the right to give a provisional answer once its fast summaries are shown to agree with the full reviews.

Every graduation is recorded on a **scorecard** so you can always see how mature each part is.

## 3. The three layers

The product is built in three layers, from simplest to most autonomous:

1. **Tools** — the individual, validated building blocks (read abstracts, pull out numbers, run a meta-analysis). You can run one by hand.
2. **Recipes** — trusted preset pipelines that run tools in a fixed, known order. **Quick Look** and **Full Review** are recipes. One click, no improvising. This is how a user asks one question and gets a result without hand-running ten tools — and without handing control to an AI that decides things on its own.
3. **Agent** *(much later)* — improvises the order of tools per question, but only ever using tools that have already graduated.

We build layer 1 and 2 now (with a human in charge); layer 3 waits until the pieces underneath are proven.

## 4. The tools

Tools come in two kinds. **This distinction is the heart of the cost story:**

- **Math tools** are pure calculators (the meta-analysis, number-needed-to-treat, pooling). They run on your own computer with the R statistics software — **no AI, no internet, no per-use cost, same answer every time.** These are *already* fully local and free.
- **AI tools** need an "AI brain" to read and write (decide include/exclude, pull numbers from text, write a summary). The brain is Claude (cloud, costs money per use) today; it can become a free local AI model later, **one tool at a time, once each is proven.**

So "use local instead of cloud" only ever applies to the AI tools — the handful (about eight) that read and write. The catalog:

**Finding & preparing studies**
| Tool | AI? | Reads → Writes |
|---|---|---|
| `search` | no | the question's search → list of studies |
| `verify` | no | checks every study ID is real and live |
| `dedup` | no | merges duplicate reports of the same trial |
| `deid` | no | strips patient identifiers (safety, fail-closed) |

**Reading & pulling data** (the high-volume AI tools)
| Tool | AI? | Default brain | Reads → Writes |
|---|---|---|---|
| `screen` | **yes** | cloud | studies → include/exclude decisions |
| `fulltext` | no | — | fetches full papers where legally available |
| `extract` | **yes** | cloud | included studies → the data table |

**Judging quality**
| Tool | AI? | Reads → Writes |
|---|---|---|
| `risk-of-bias` | **yes** (cloud) | studies → bias judgments |
| `grade` | no (rules) | data + bias → certainty rating — **a conclusion; locked until human sign-off** |

**The statistics** (fine-grained — each is its own separately-benchmarked tool, all math, all local)
| Tool | What it answers |
|---|---|
| `pool-proportions` | combined response/event rate across trials |
| `pool-hazard-ratios` | combined time-to-event effect (e.g. survival) |
| `metabin` | treatment-vs-control: relative risk, odds ratio, and **number-needed-to-treat** |
| `nma` | indirect comparison across treatments (when feasible) |
| `dose-response` | does more of X change the outcome |

**Writing up** (conclusion-bearing → cloud by default, and locked)
| Tool | AI? | Reads → Writes |
|---|---|---|
| `narrative` | **yes** (cloud) | the evidence → a plain summary draft |
| `claim-check` | **yes** (cloud-only) | double-checks every claim is supported; the safety net |
| `manuscript` | no (assembly) | everything → a formatted report (Word/PDF) |

*(The single-patient "case report" path adds three more AI tools — `structure`, `synthesize`, `conflict` — that follow the same rules.)*

## 5. How the tools are defined: the registry

There is **one place** that defines every tool — the **registry**. Each tool's entry says: its name, a one-line description, what file it reads and what file it writes, what settings it takes, which brain it uses by default, and its scorecard. Everything else in the system (the command-line, the future web buttons, the future auto-pilot) just reads this one registry. Define a tool once; every surface gets it for free.

**State lives in a run folder.** Each question gets a folder. A tool reads the previous tool's file from that folder and writes its own. This is what makes the human-in-the-loop natural: you can run a tool, **open and check (or hand-edit) the file**, then run the next — and pick up where you left off anytime.

The registry sits *on top of* the cloud/local dial already built — it decides "cloud or local for this tool" and hands that down to the existing machinery. It is a thin, organizing layer over code that already exists.

## 6. Cloud vs local: the maturity dial

One global setting — the **posture** — expresses where the whole product is on its journey to local:

- **`cloud`** ← where we are now. Every AI tool uses Claude. Safe, validated, highest quality.
- **`prefer-local`** ← a tool runs local *only if it has earned it* (passed its local benchmark); everything else stays cloud.
- **`local`** ← the end state: everything local except the safety tools that are still cloud-only.

Plus a **per-tool override** (`--local` / `--cloud`) for experiments — except the safety tools (`claim-check`, and anything that states a final conclusion), which refuse to be forced local so the safety net can't be switched off by accident.

**Today, with posture = `cloud`, every AI tool runs on Claude.** As each tool's local version passes its benchmark, its scorecard flips to "local-ready," and switching the posture to `prefer-local` lets it start running locally — automatically, one tool at a time.

> **Benchmark finding (2026-06-09):** local screening looks promising, but local *extraction* is not ready — neither gemma4 nor qwen2.5:7b could pull the response-rate number reliably (cloud pulled 29 of 43; local pulled 0). gemma4 was the more reliable of the two and is the local preference for when tools do graduate. This is the maturity dial working as intended: extraction stays cloud until a better model or stronger hardware passes the test.

## 7. Letting the human draw the conclusion (the lock)

Built in Phase 1, and it stays central. Any tool that produces a **final conclusion, certainty rating, or recommendation** (`grade`, `narrative`, `manuscript`, the case-report `synthesize`) is **locked**: the system computes the answer, keeps it in the data, but renders it as **"DRAFT — awaiting human reviewer"** until a person records their sign-off (`--approve-conclusions "<name>"`). The human is always the one who finalizes the call.

## 8. Two speeds (because the user is both a clinician and a researcher)

Same tools, two recipes:

| | **Quick Look** (rapid) | **Full Review** (deep) |
|---|---|---|
| Search | the ~50–100 most relevant/recent | the entire literature |
| Reading | just that narrow set | every result |
| Analysis | a fast summary | full data + meta-analysis + quality appraisal |
| Time | ~1–3 minutes | minutes to hours (runs in the background) |
| Traceable? | yes, partial | yes, complete + auditable |
| Feels like | OpenEvidence | a publishable evidence report |

**The flow:** ask a question → get a **Quick Look in ~a minute** → if it matters, press "**run Full Review**," which works in the background and notifies you when done. The Full Review then replaces the quick one — same question, deeper answer.

**Quick Look stays honest (A → B as it earns trust):**
- **Now (A — triage only):** Quick Look is explicitly a *first scan*, not an answer. It shows the signal and the key studies but says "this is incomplete — run a Full Review before relying on it," and it can **never** present a final conclusion.
- **Later (B — provisional, loudly caveated):** once we've validated that Quick Look's summaries reliably agree with the Full Reviews, it may give a provisional summary, always stamped "preliminary — based on the top N of M studies," and still never "signed off" as final. Only a Full Review can be finalized.

The thing that makes Quick Look genuinely fast — reading the best ~50 studies instead of all 2,000 — is the **smart-ranking helper** on the roadmap, and the thing that makes Full Review cheap is **local AI**. So speed and the cost goal pull the same way.

## 9. How a tool earns trust (validation)

Every tool's scorecard tracks two checks:

- **Internal — "does it do what it claims, reliably?"** Automated tests: does `screen` include the real trials and exclude the animal studies; does `extract` pull the right number; does the meta-analysis match a hand calculation.
- **External — "does it match reality?"** A blind benchmark: run it on a question whose answer was already published and confirm our result matches (e.g., our pooled response rate ≈ the published 54%).

The scorecard drives every graduation: human→auto-pilot, cloud→local, and Quick-Look triage→provisional.

## 10. What we build first (and what waits)

**First (this phase):**
1. The registry + the posture setting + a simple command-line that runs one tool at a time.
2. Register the tools that already exist (each just wraps current code and gets a scorecard).
3. Default every AI tool to cloud; add the per-tool override.
4. Wrap the two recipes (Quick Look, Full Review) over the tools.
5. Mark each tool validated as it passes its benchmark; mark local-ready as each local benchmark passes.

**Deferred (later, on purpose):**
- The auto-pilot agent (layer 3).
- The web-app buttons and the external/other-apps interface (both are thin add-ons over the same registry).
- Actually fine-tuning a local AI model — far down the road, only after lots of validated examples exist.

**How it fits the current plan:** the phases in flight (the cloud/local dial — done; the new statistics; the smart-ranking helper) build the *capabilities*. This Tool Platform is the step that **packages those finished, validated capabilities** into the product described here. It is not a pivot — it's the natural next phase once the capabilities are in.

## 11. Non-goals (to keep this focused)

- We are **not** building a pre-indexed library / always-on database of all papers (that's the OpenEvidence approach we're deliberately *not* taking — live search per question is the point).
- We are **not** building the auto-pilot agent yet.
- We are **not** training a custom AI model yet.
- Quick Look does **not** get to state a final conclusion, ever, until it has earned provisional status — and even then a human still signs off.
