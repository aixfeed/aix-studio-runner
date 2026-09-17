#!/usr/bin/env python3
"""Re-scrape prism-material WITH prismTagInfoId filters (audit wave 1 P1 fix).

The unfiltered /prism-material/listPage projection strips prismTagInfoId
(null on all 1,177 captured records) even though the endpoint ACCEPTS a
prismTagInfoId param — so the 4-category taxonomy (写真/儿童/古风/婚纱) was
never captured and every style_material showed "uncategorized".

Sweep the endpoint once per tag; the sweep context (not the payload) is the
source of truth for the tag. Enrichment model:
  - existing records: set prismTagInfoId in-place (null → tag, never overwrite
    a non-null value) — accumulate-don't-lose preserved
  - new records (site additions): appended with the tag stamped

Idempotent + resumable (state file). Run: python3 scripts/aix_prism_tags.py
"""
import json, math, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aix_transport

BASE = os.environ.get("AIX_BASE", "/home/z/my-project")
OUT = f"{BASE}/download/aixstudio"
OUTL = f"{OUT}/prism_material.jsonl"
ST = f"{BASE}/scripts/aix_prism_state.json"
ENDPOINT = "/apinew/comfy/prism-material/listPage"
SIZE = 50

def fetch_tag_pages(tag, pages):
    plist = json.dumps(pages)
    js = f"""(async()=>{{
      const pages = {plist};
      const results = {{}};
      async function one(p){{
        try{{
          const u = '{ENDPOINT}?pageNum='+p+'&current='+p+'&size='+{SIZE}+'&prismTagInfoId={tag}';
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

def sweep_tag(tag):
    """Full pagination sweep for one tag; returns {id: record} stamped with tag."""
    d = fetch_tag_pages(tag, [1])
    p1 = d.get("1", d.get(1, {}))
    if "err" in p1:
        print(f"[tag {tag}] probe failed: {p1['err']}")
        return None
    total = p1["total"]
    npages = math.ceil(total / SIZE) if total else 1
    print(f"[tag {tag}] total={total} pages={npages}")
    recs = {}
    BATCH = 4
    for i in range(1, npages + 1, BATCH):
        batch = list(range(i, min(i + BATCH, npages + 1)))
        for attempt in range(4):
            try:
                d = fetch_tag_pages(tag, batch)
                break
            except Exception as e:
                print(f"  retry batch {batch} ({attempt+1}): {str(e)[:100]}")
                time.sleep(3 + attempt * 3)
        else:
            print(f"  SKIP batch {batch} after retries")
            continue
        for p in batch:
            v = d.get(str(p), d.get(p, {}))
            if "err" in v:
                print(f"  page {p} err: {v['err']}")
                continue
            for r in v.get("records") or []:
                if r.get("id") is not None:
                    r["prismTagInfoId"] = tag   # stamp from sweep context
                    recs[str(r["id"])] = r
    return recs

def main():
    tags = aix_transport.eval_json(
        "(async()=>JSON.stringify(await (await fetch('/apinew/comfy/prism-tag/listAll')).json()))()",
        timeout=60)["data"]
    tag_ids = [str(t["id"]) for t in tags]
    print("prism tags:", [(t["id"], t["tagName"]) for t in tags])

    stamped = {}   # id -> tag
    for tag in tag_ids:
        recs = sweep_tag(tag)
        if recs is None:
            print(f"[tag {tag}] SWEEP FAILED — keep state so next run retries")
            continue
        for rid, r in recs.items():
            stamped[rid] = r

    # ---- in-place enrichment + accumulate ----
    if not stamped:
        print("nothing fetched — aborting rewrite")
        return
    existing = []
    if os.path.exists(OUTL):
        with open(OUTL) as f:
            existing = [json.loads(l) for l in f if l.strip()]
    have = {str(r.get("id")) for r in existing}
    updated = appended = 0
    for r in existing:
        rid = str(r.get("id"))
        if rid in stamped and not r.get("prismTagInfoId"):
            r["prismTagInfoId"] = stamped[rid]["prismTagInfoId"]
            updated += 1
    new = [r for rid, r in stamped.items() if rid not in have]
    # write atomically: tmp + rename (never a torn corpus)
    tmp = OUTL + ".tmp"
    with open(tmp, "w") as f:
        for r in existing:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        for r in new:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, OUTL)
    appended = len(new)
    print(f"enriched {updated} existing records, appended {appended} new "
          f"({len(stamped)} tagged records seen across {len(tag_ids)} sweeps)")
    json.dump({"run": time.strftime("%Y-%m-%d %H:%M:%S"), "swept_tags": tag_ids,
               "tagged_records": len(stamped), "updated": updated, "appended": appended},
              open(ST, "w"), indent=1)

if __name__ == "__main__":
    main()
