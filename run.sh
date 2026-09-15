#!/bin/bash
# AIX Studio scraper — single entry point
# Usage: ./run.sh [all|scrape|auth|aux|enrich|finalize|catalog|validate|gha|audit|status|resume]
#
# Transport is pluggable (scripts/aix_transport.py):
#   sandbox  -> agent-browser (auto-detected on PATH)
#   CI/local -> Playwright    (AIX_TRANSPORT=playwright; needs `pip install
#                               playwright && python -m playwright install chromium`)
set -euo pipefail
cd "$(dirname "$0")"

CMD="${1:-all}"

banner(){ echo "==> $*"; }

ensure_browser(){
  # agent-browser backend: make sure a session is open on aix.studio
  if command -v agent-browser >/dev/null 2>&1; then
    if ! agent-browser get url >/dev/null 2>&1; then
      banner "starting browser session"
      agent-browser open "https://aix.studio/" >/dev/null
      agent-browser wait --load networkidle >/dev/null 2>&1 || true
      sleep 2
    fi
  fi
}

case "$CMD" in
  scrape)
    ensure_browser
    banner "phase 1: paginated catalogs"
    python3 scripts/aix_scraper.py
    ;;
  auth)
    ensure_browser
    banner "phase auth: canvas graphs + gated libraries"
    python3 scripts/aix_auth_scrape.py
    ;;
  aux)
    ensure_browser
    banner "phase 2: taxonomies + comments"
    python3 scripts/aix_aux.py
    ;;
  enrich)
    ensure_browser
    banner "phase 3: profiles + single-shot datasets"
    python3 scripts/aix_enrich.py
    ;;
  finalize)
    banner "dedupe + stats"
    python3 scripts/aix_finalize.py
    ;;
  catalog)
    banner "rebuild parsed catalog"
    python3 scripts/aix_build_catalog.py
    python3 scripts/aix_build_catalog_auth.py
    ;;
  validate)
    banner "pre-push validation gate"
    python3 scripts/aix_validate.py
    ;;
  gha)
    # Exact sequence the GitHub Actions workflow runs (weekly-scrape.yml).
    # Use locally to test CI parity: AIX_TRANSPORT=playwright ./run.sh gha
    banner "smoke test";         python3 scripts/aix_transport.py
    banner "public catalogs";    python3 scripts/aix_scraper.py
    banner "auth scrape";        python3 scripts/aix_auth_scrape.py
    banner "aux";                python3 scripts/aix_aux.py
    banner "enrich";             python3 scripts/aix_enrich.py
    banner "finalize";           python3 scripts/aix_finalize.py
    banner "catalog";            python3 scripts/aix_build_catalog.py
    banner "auth catalog";       python3 scripts/aix_build_catalog_auth.py
    banner "validate";           python3 scripts/aix_validate.py
    banner "gha sequence complete"
    ;;
  audit)
    banner "full route/network audit"
    bash scripts/audit_pages.sh
    ;;
  status)
    python3 - <<'PY'
import json, os
st = json.load(open("scripts/aix_state.json")) if os.path.exists("scripts/aix_state.json") else {}
sweeps = st.get("sweeps", {})
if sweeps:
    for name, s in sorted(sweeps.items()):
        print(f"{name:16s} api_total={s.get('total','?'):>6} have={s.get('have','?'):>6} "
              f"+new={s.get('added','?'):>4} at={s.get('at','?')}")
else:
    for k, pages in sorted(st.get("done_pages", {}).items()):
        name = k.split("|")[0]
        print(f"{name:16s} pages done: {len(pages):3d}   (api total: {st.get('totals',{}).get(name,'?')})")
PY
    ;;
  resume)
    ensure_browser
    banner "resuming all phases (state-aware)"
    python3 scripts/aix_scraper.py || true
    python3 scripts/aix_aux.py || true
    python3 scripts/aix_enrich.py || true
    python3 scripts/aix_finalize.py
    ;;
  all)
    ensure_browser
    banner "phase 1: catalogs";   python3 scripts/aix_scraper.py
    banner "phase auth";         python3 scripts/aix_auth_scrape.py
    banner "phase 2: aux";       python3 scripts/aix_aux.py
    banner "phase 3: enrich";    python3 scripts/aix_enrich.py
    banner "finalize";           python3 scripts/aix_finalize.py
    banner "catalog";            python3 scripts/aix_build_catalog.py
    banner "auth catalog";       python3 scripts/aix_build_catalog_auth.py
    banner "validate";           python3 scripts/aix_validate.py
    banner "done — see download/aixstudio/ and src/data/catalog/"
    ;;
  *)
    echo "usage: $0 [all|scrape|auth|aux|enrich|finalize|catalog|validate|gha|audit|status|resume]" >&2
    exit 1
    ;;
esac
