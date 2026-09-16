# aix-studio-runner

CI runner for the AIX Studio (aix.studio) AIGC data pipeline.

**Architecture** (two repos):

| repo | visibility | role |
|---|---|---|
| `wesisad5/aix-studio-scraper` | private | **data** — raw JSONL, parsed catalog, resume state |
| `aixfeed/aix-studio-runner` (this) | public | **CI bootstrap** — weekly GHA workflow; pipeline code is synced FROM the data repo at run start |

The workflow checks this repo out for the workflow + bootstrap, clones the
private data repo with a PAT, **syncs pipeline code from the data repo's main**
(the data repo is the single source of truth — script fixes land there with
their tests), runs the full pipeline against the live site, validates, and
pushes the data repo. Public runner = free unlimited Actions minutes; the
private data repo never needs to run Actions.

> Direction hardening (2026-09-16): the sync used to copy this repo's
> `scripts/` INTO the data repo. A run dispatched before a script fix landed
> here reverted the fixed builders in the data repo (458e842). The sync now
> runs data->runner only; this repo's `scripts/` is kept in sync as a
> convenience snapshot and is never the authority.

## Pipeline (what the workflow does)

1. **Transport smoke test** — headless Chromium/Playwright in-page fetch on
   aix.studio (the site's Tencent WAF resets non-browser TLS; real browser TLS
   passes). Fails fast if the site is down or the WAF changes behavior.
2. **Public sweep** — gallery, flows, assets, style materials, task outputs,
   user prisms. Full page sweep + ID-dedup append (accumulate model: idempotent,
   page-shift-proof, deletions on the site don't lose archived data).
3. **Authed sweep** (needs `AIX_TOKEN`) — canvas node-graphs (the main prize:
   per-node params/prompts/media), gated libraries (characters/props/scenes),
   one-shot datasets.
4. **Aux + enrich** — gallery comments, creator profiles.
5. **Finalize + catalog rebuild** — dedupe, stats, typed 11-collection catalog.
6. **Validation gate** — JSONL integrity, catalog parity, regression floors,
   cross-references, GitHub file-size guards. Blocks the push on any defect.
7. **Commit + push** the data repo (github-actions[bot]).

Weekly schedule: Mondays 03:00 UTC. Failures open an issue here with the run link.

## Secrets

| secret | what |
|---|---|
| `AIX_TOKEN` | aix.studio auth token (sent as `token:` request header; Bearer is rejected). Refresh from browser devtools when it expires — code 333 responses fail the run with an explanatory issue. |
| `DATA_REPO_PAT` | classic PAT with `repo` scope — push access to `wesisad5/aix-studio-scraper`. |

## Local parity run

```bash
pip install playwright && python -m playwright install chromium
AIX_TRANSPORT=playwright ./run.sh gha     # exact CI sequence
```

In sandbox environments with the `agent-browser` CLI, the same scripts
auto-detect and use it instead (no env var needed).

## Layout

```
scripts/    pipeline (stdlib-only Python; playwright only for transport)
run.sh      orchestration (all|scrape|auth|aux|enrich|finalize|catalog|validate|gha)
.github/    the weekly workflow
```

Data dictionary, endpoint access map, and operational runbook live in the data repo.
