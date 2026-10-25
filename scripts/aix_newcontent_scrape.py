#!/usr/bin/env python3
"""
AIX Studio "new content" scraper — round-11 adoptions (audit/round11/
I2_endpoint_rediscovery.md + I4_ui_section_scan.md).

Small, MUTABLE collections whose endpoints return plain arrays or tiny
listPages. Unlike the accumulate-model JSONL catalogs, these are written as
SNAPSHOT JSONs (array state replaces previous run — records here get edited
in place by the site: skill updateTime churns, contest remainingTimes
ticks). Atomic writes keep the previous snapshot on failure; any exhausted
endpoint fails the run (rc=1, loud) — same guard philosophy as aix_aux.py.

Endpoints:
  skills          GET /comfy/team/user-skill/square/list   (anon OK; 20 recs)
  skill_cats      GET /comfy/team/user-skill/square/categories (anon; 7)
  material_tags   GET /comfy/material-tag/listAll          (anon OK; 32)
  contests        GET /comfy/contest/listPage              (TOKEN required;
                  anon -> 333; 6 recs; listPage already carries the full
                  record incl. description + questionnaireJson — no /detail
                  calls needed, probe 2026-09-29)
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aix_transport

BASE = os.environ.get("AIX_BASE", "/home/z/my-project")
OUT = f"{BASE}/download/aixstudio"
TOKEN_FILE = f"{BASE}/scripts/.aix_token"
os.makedirs(OUT, exist_ok=True)

# (output name, endpoint, needs_token)
TARGETS = [
    ("skills",         "/apinew/comfy/team/user-skill/square/list?categoryCode=&keyword=&sort=latest", False),
    ("skill_categories", "/apinew/comfy/team/user-skill/square/categories", False),
    ("material_tags",  "/apinew/comfy/material-tag/listAll", False),
    ("contests",       "/apinew/comfy/contest/listPage?pageNum=1&current=1&size=50", True),
]


def token():
    try:
        return open(TOKEN_FILE).read().strip()
    except Exception:
        return ""


def main():
    tok = token()
    if not tok:
        print("[newcontent] WARNING: no token — contests will fail (skills/tags are anon)")
    plist = json.dumps([t[:2] + (t[2],) for t in TARGETS])
    tjs = json.dumps(tok)
    js = f"""(async()=>{{
      const targets = {plist};
      const TOK = {tjs};
      const out = {{}};
      async function one(t) {{
        const [name, path, needTok] = t;
        try {{
          const h = needTok && TOK ? {{token: TOK}} : {{}};
          const r = await fetch(path, {{headers: h, signal: AbortSignal.timeout(120000)}});
          let j; try {{ j = await r.json(); }} catch(e) {{ out[name] = {{err: 'non-json http='+r.status}}; return; }}
          if (j.code !== 200) {{ out[name] = {{err: j.code+' '+(j.message||'').slice(0,40)}}; return; }}
          const d = j.data;
          out[name] = (Array.isArray(d)) ? {{array: d}}
                   : (d && d.records) ? {{array: d.records, total: d.total}}
                   : {{err: 'unexpected data shape: '+typeof d}};
        }} catch(e) {{ out[name] = {{err: String(e).slice(0,80)}}; }}
      }}
      const q = targets.slice();
      async function w() {{ while(q.length) {{ await one(q.shift()); }} }}
      await Promise.all([w(),w()]);
      return JSON.stringify(out);
    }})()"""
    res = aix_transport.eval_json(js, timeout=240)

    failures = []
    auth_dead_skips = []
    for name, path, need_tok in TARGETS:
        v = res.get(name) or {"err": "missing from eval result"}
        if "err" in v:
            err = str(v["err"])
            # R25 (audit r25-a P2-8): EXACT code match — err is built as
            # f"{code} {message}" (e.g. "333 请先登录"), so match the first
            # token, not a prefix ("333" would also match a hypothetical 3330).
            if need_tok and err.split(" ", 1)[0] == "333":
                print(f"[{name}] SKIPPED (auth dead — keeping last-good "
                      f"snapshot; resumes on token refresh)")
                auth_dead_skips.append(name)
                continue
            print(f"[{name}] FAILED: {err}")
            failures.append(name)
            continue
        recs = v["array"]
        snap = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "count": len(recs), "data": recs}
        if "total" in v:
            snap["site_total"] = v["total"]
        tmp = f"{OUT}/{name}.json.tmp"
        with open(tmp, "w") as f:
            json.dump(snap, f, ensure_ascii=False)
        os.replace(tmp, f"{OUT}/{name}.json")
        print(f"[{name}] snapshot: {len(recs)} records"
              + (f" (site_total={v['total']})" if "total" in v else ""))

    if failures:
        print(f"[newcontent] DONE_WITH_ERRORS: {failures}")
        return 1
    print("[newcontent] DONE" +
          (f" (auth-dead skips: {auth_dead_skips} — stale snapshots retained)"
           if auth_dead_skips else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
