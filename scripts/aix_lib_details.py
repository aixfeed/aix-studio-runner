#!/usr/bin/env python3
"""
Lib /detail enrichment scraper — character/prop/scene libraries.

The listPage payloads are thin (name + 2-3 thumb URLs + attrs codes). The
/detail endpoints return far more (audited 2026-09-16, recon/lib_detail_samples.json):
  scene:    desc, attrsLabels, full-res URLs (no imageView2 param)
  character: imgThreeView, featureText, outfitText, context{major,era,scene},
             attrsLabels (label + labelEn), full-res URLs
  prop:     desc, worldviewLabels, attrsLabels, full-res URLs

ID-dedup accumulate: existing ids skipped; new ids appended. Details are
immutable so a record is fetched at most once, ever.
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aix_transport

BASE = os.environ.get("AIX_BASE", "/home/z/my-project")
OUT = f"{BASE}/download/aixstudio/auth"
ST = f"{BASE}/scripts/aix_lib_detail_state.json"
TOKEN = open(f"{BASE}/scripts/.aix_token").read().strip()
os.makedirs(OUT, exist_ok=True)

LIBS = {
    "character": ("/apinew/comfy/character-lib/listPage", "/apinew/comfy/character-lib/detail"),
    "prop":      ("/apinew/comfy/prop-lib/listPage",      "/apinew/comfy/prop-lib/detail"),
    "scene":     ("/apinew/comfy/scene-lib/listPage",     "/apinew/comfy/scene-lib/detail"),
}
BATCH = 12          # parallel requests per in-page Promise.all batch
PAUSE = 0.35        # seconds between batches


def load_state():
    if os.path.exists(ST):
        try:
            return json.load(open(ST))
        except Exception:
            pass
    return {"done": {}}


def save_state(st):
    json.dump(st, open(ST, "w"), indent=1)


def existing_ids(kind):
    ids = set()
    path = f"{OUT}/{kind}_details.jsonl"
    if not os.path.exists(path):
        return ids
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if r.get("id") is not None:
                    ids.add(str(r["id"]))
            except Exception:
                pass
    return ids


def fetch_batch(transport, endpoint, ids):
    """One in-page Promise.all batch of /detail calls. Returns list of dicts."""
    idlist = json.dumps([str(i) for i in ids])
    js = f"""(async()=>{{
      const H={{token:'{TOKEN}'}};
      const ids={idlist}; const out=[];
      await Promise.all(ids.map(async(id)=>{{
        try{{
          const r=await fetch('{endpoint}?id='+id,{{headers:H}});
          const j=await r.json();
          if(j && j.code===200 && j.data) out.push(j.data); else out.push({{__id:id,__err:(j&&j.code)||r.status}});
        }}catch(e){{out.push({{__id:id,__err:String(e).slice(0,80)}})}}
      }}));
      return JSON.stringify(out);
    }})()"""
    raw = transport.eval_js(js, timeout=180)
    out = json.loads(aix_transport.unquote(raw))
    return out if isinstance(out, list) else []


def run(kind, list_endpoint, detail_endpoint, transport, limit=None):
    ids = existing_ids(kind)
    st = load_state()
    done = st["done"].setdefault(kind, [])

    # candidate ids = listPage corpus minus already-fetched
    list_path = f"{OUT}/{kind}_lib.jsonl"
    cands = []
    with open(list_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if r.get("id") is not None and str(r["id"]) not in ids:
                    cands.append(str(r["id"]))
            except Exception:
                pass
    if limit:
        cands = cands[:limit]
    print(f"[{kind}] {len(ids)} already captured, {len(cands)} to fetch")

    path = f"{OUT}/{kind}_details.jsonl"
    err_ids = []
    n_ok = 0
    with open(path, "a") as out_f:
        for i in range(0, len(cands), BATCH):
            batch = cands[i:i + BATCH]
            try:
                results = fetch_batch(transport, detail_endpoint, batch)
            except Exception as e:
                print(f"[{kind}] batch {i//BATCH} transport error: {str(e)[:120]} — retrying once")
                time.sleep(3)
                try:
                    results = fetch_batch(transport, detail_endpoint, batch)
                except Exception as e2:
                    print(f"[{kind}] batch {i//BATCH} FAILED twice: {str(e2)[:120]}")
                    err_ids.extend(batch)
                    continue
            for rec in results:
                if not isinstance(rec, dict):
                    continue
                if "__err" in rec:
                    err_ids.append(rec.get("__id"))
                    continue
                out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_ok += 1
            out_f.flush()
            if (i // BATCH) % 10 == 0:
                print(f"[{kind}] {i + len(batch)}/{len(cands)} (+{n_ok} records, {len(err_ids)} errors)")
            time.sleep(PAUSE)

    # persist done-set (records written)
    new_done = list(existing_ids(kind))
    st["done"][kind] = new_done
    save_state(st)
    print(f"[{kind}] DONE: +{n_ok} records, {len(err_ids)} error ids (retry next run): {err_ids[:8]}")
    return n_ok, len(err_ids)


def main():
    only = sys.argv[1:] if len(sys.argv) > 1 else None
    transport = aix_transport.AgentBrowserTransport()
    total_ok = total_err = 0
    for kind, (lep, dep) in LIBS.items():
        if only and kind not in only:
            continue
        ok, err = run(kind, lep, dep, transport)
        total_ok += ok
        total_err += err
    print(f"\nTOTAL: +{total_ok} records, {total_err} errors")


if __name__ == "__main__":
    main()
