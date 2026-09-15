#!/usr/bin/env python3
"""
AIX Studio (aix.studio) public catalog scraper — v2 (accumulate model).
Transport: pluggable in-page fetch (agent-browser in sandbox / Playwright on CI)
via aix_transport.py — real browser TLS, no WAF resets. All endpoints PUBLIC.

v2 model (productization): every run performs a FULL sweep of all pages and
appends only records whose id is not yet in the output JSONL. This makes runs
idempotent + safe under page-shift (new content pushes old content across
pages, which broke the v1 page-based resume). state.json is informational
only (last sweep stats), never a gate.
"""
import json, math, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aix_transport

BASE = os.environ.get("AIX_BASE", "/home/z/my-project")
OUT = f"{BASE}/download/aixstudio"
ST = f"{BASE}/scripts/aix_state.json"
os.makedirs(OUT, exist_ok=True)

CATALOGS = {
    # name: (endpoint path, page size, id key)
    "gallery":        ("/apinew/comfy/canvas-open-info/listPage", 50, "id"),
    "flows":          ("/apinew/comfy/canvas-flow/listPage", 50, "id"),
    "assets":         ("/apinew/comfy/canvas-asset/listPage", 50, "id"),
    "prism_material": ("/apinew/comfy/prism-material/listPage", 50, "id"),
    "task_outputs":   ("/apinew/comfy/task-output-file/openListPage", 50, "id"),
    "user_prism":     ("/apinew/comfy/user-prism-material-info/openListPage", 50, "id"),
}


def load_state():
    if os.path.exists(ST):
        try:
            st = json.load(open(ST))
        except Exception:
            st = {}
    else:
        st = {}
    st.setdefault("sweeps", {})  # migrate old {done_pages,totals} format
    return st


def save_state(st):
    json.dump(st, open(ST, "w"), indent=1)


def existing_ids(path, id_key):
    """Stream the JSONL, return set of already-captured ids."""
    ids = set()
    if not os.path.exists(path):
        return ids
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                v = r.get(id_key)
                if v is not None:
                    ids.add(str(v))
            except Exception:
                pass  # tolerate a torn last line; finalize dedupes anyway
    return ids


def fetch_pages(path, size, pages):
    """Fetch several pages concurrently in-page; return {page: {records,total}|{err}}."""
    plist = json.dumps(pages)
    js = f"""(async()=>{{
      const pages = {plist};
      const results = {{}};
      async function one(p){{
        try{{
          const u = '{path}?pageNum='+p+'&current='+p+'&size='+{size};
          const r = await fetch(u);
          const j = await r.json();
          if(j.code !== 200) return {{p, err: j.code+' '+(j.message||'').slice(0,40)}};
          return {{p, records: j.data.records, total: j.data.total}};
        }}catch(e){{ return {{p, err: e.message}}; }}
      }}
      const queue = pages.slice();
      async function worker(){{
        while(queue.length){{ const p = queue.shift(); results[p] = await one(p); }}
      }}
      await Promise.all([worker(),worker(),worker(),worker()]);
      return JSON.stringify(results);
    }})()"""
    return aix_transport.eval_json(js, timeout=240)


def scrape_catalog(name, path, size, id_key="id"):
    st = load_state()
    outl = f"{OUT}/{name}.jsonl"
    have = existing_ids(outl, id_key)

    d = fetch_pages(path, size, [1])
    p1 = d.get("1", d.get(1, {}))
    if "err" in p1:
        print(f"[{name}] probe failed: {p1['err']}")
        return False
    total = p1["total"]
    npages = math.ceil(total / size) if total else 1
    print(f"[{name}] total={total} pages={npages} have={len(have)}")

    added = 0
    remaining = list(range(1, npages + 1))
    BATCH = 4  # pages per eval
    for i in range(0, len(remaining), BATCH):
        batch = remaining[i:i + BATCH]
        for attempt in range(4):
            try:
                d = fetch_pages(path, size, batch)
                break
            except Exception as e:
                print(f"  retry batch {batch} ({attempt+1}): {str(e)[:120]}")
                time.sleep(3 + attempt * 3)
        else:
            print(f"  SKIP batch {batch} after retries")
            continue
        new_recs = []
        for p in batch:
            v = d.get(str(p), d.get(p, {}))
            if "err" in v:
                print(f"  page {p} err: {v['err']}")
                continue
            for r in v.get("records") or []:
                rid = r.get(id_key)
                if rid is None or str(rid) in have:
                    continue
                have.add(str(rid))
                new_recs.append(r)
        if new_recs:
            with open(outl, "a") as f:
                for r in new_recs:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            added += len(new_recs)
        if (i // BATCH) % 10 == 0:
            print(f"  pages {batch[0]}-{batch[-1]}: +{len(new_recs)} (run total +{added})")
        time.sleep(0.6)

    st["sweeps"][name] = {
        "total": total, "have": len(have), "added": added,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    save_state(st)
    print(f"[{name}] sweep done: +{added} new (cumulative {len(have)})")
    return True


if __name__ == "__main__":
    only = sys.argv[1:] if len(sys.argv) > 1 else list(CATALOGS)
    ok = True
    for name in only:
        path, size, id_key = CATALOGS[name]
        ok = scrape_catalog(name, path, size, id_key) and ok
    print("DONE" if ok else "DONE_WITH_ERRORS")
    sys.exit(0 if ok else 1)
