#!/usr/bin/env python3
"""
Authenticated scrape of aix.studio gated data — v2 (accumulate model).
Transport: pluggable (agent-browser / Playwright) via aix_transport.py,
in-page fetch with `token` header (Bearer does NOT work).

Targets:
  canvas_graphs  — gallery canvases /comfy/canvas-json-copy-info/detail?id=<canvasJsonInfoId>
  flow_details   — ALL workflow graphs /comfy/canvas-flow/details?id=<flowId>
                  (type-1 "ComfyUI" flows carry aix-format canvasJson too —
                  probed 2026-09-16; written in ~40MB chunks flow_details*.jsonl)
  character_lib / prop_lib / scene_lib — gated listPage libraries (ID-dedup accumulate)
  singles        — one-shot endpoints incl. tag-info/listPage + partner-info

Resume: ID-based done-sets in scripts/aix_auth_state.json (canvas graphs and
flow details never change once captured; libs use ID-dedup append so a full
re-sweep only appends new records).
"""
import json, math, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aix_transport

BASE = os.environ.get("AIX_BASE", "/home/z/my-project")
OUT = f"{BASE}/download/aixstudio/auth"
ST = f"{BASE}/scripts/aix_auth_state.json"
TOKEN = open(f"{BASE}/scripts/.aix_token").read().strip()
os.makedirs(OUT, exist_ok=True)

PAGED_LIBS = {
    "character_lib": ("/apinew/comfy/character-lib/listPage", 50),
    "prop_lib":      ("/apinew/comfy/prop-lib/listPage", 50),
    "scene_lib":     ("/apinew/comfy/scene-lib/listPage", 50),
}


def load_state():
    if os.path.exists(ST):
        try:
            return json.load(open(ST))
        except Exception:
            pass
    return {"done": {}}


def save_state(st):
    json.dump(st, open(ST, "w"), indent=1)


def js_hdr():
    return f"const H={{token:'{TOKEN}'}};"


def existing_ids(path, id_key="id"):
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
                if r.get(id_key) is not None:
                    ids.add(str(r[id_key]))
            except Exception:
                pass
    return ids


# ---------- generic paged fetch (token auth) ----------
def fetch_pages(path, size, pages):
    plist = json.dumps(pages)
    js = f"""(async()=>{{
      {js_hdr()}
      const pages={plist}; const res={{}};
      async function one(p){{
        try{{
          const r=await fetch('{path}?pageNum='+p+'&current='+p+'&size='+{size},{{headers:H}});
          const j=await r.json();
          if(j.code===333) return {{p,err:'AUTH333'}};
          if(j.code!==200) return {{p,err:j.code+' '+(j.message||'').slice(0,30)}};
          return {{p,records:j.data.records,total:j.data.total}};
        }}catch(e){{ return {{p,err:e.message.slice(0,40)}}; }}
      }}
      const q=pages.slice();
      async function w(){{ while(q.length){{ const p=q.shift(); res[p]=await one(p); }} }}
      await Promise.all([w(),w(),w(),w()]);
      return JSON.stringify(res);
    }})()"""
    return aix_transport.eval_json(js, timeout=240)


def scrape_paged(name, path, size):
    """Full sweep + ID-dedup append (v2 accumulate model)."""
    outl = f"{OUT}/{name}.jsonl"
    have = existing_ids(outl)
    d = fetch_pages(path, size, [1])
    p1 = d.get("1", d.get(1, {}))
    if "err" in p1:
        print(f"[{name}] probe failed: {p1['err']}")
        if p1["err"] == "AUTH333":
            print("!! TOKEN EXPIRED (code 333) — refresh scripts/.aix_token and rerun")
            sys.exit(2)
        return
    total = p1["total"]
    npages = math.ceil(total / size) if total else 1
    print(f"[{name}] total={total} pages={npages} have={len(have)}")

    added = 0
    for i in range(0, npages, 4):
        batch = list(range(i + 1, min(i + 5, npages + 1)))
        for attempt in range(4):
            try:
                d = fetch_pages(path, size, batch)
                break
            except Exception as e:
                print(f"  retry {batch} ({attempt+1}): {str(e)[:100]}")
                time.sleep(2 + attempt * 2)
        else:
            print(f"  SKIP {batch}")
            continue
        new_recs, auth_fail = [], False
        for p in batch:
            v = d.get(str(p), d.get(p, {}))
            if "err" in v:
                if v["err"] == "AUTH333":
                    auth_fail = True
                print(f"  page {p} err: {v['err']}")
                continue
            for r in v.get("records") or []:
                rid = r.get("id")
                if rid is None or str(rid) in have:
                    continue
                have.add(str(rid))
                new_recs.append(r)
        if auth_fail:
            print("!! TOKEN EXPIRED (code 333) — refresh scripts/.aix_token and rerun")
            sys.exit(2)
        if new_recs:
            with open(outl, "a") as f:
                for r in new_recs:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            added += len(new_recs)
        if (i // 4) % 10 == 0:
            print(f"  pages {batch[0]}-{batch[-1]}: +{len(new_recs)} (run total +{added})")
        time.sleep(0.4)
    print(f"[{name}] sweep done: +{added} new (cumulative {len(have)})")


# ---------- canvas graphs (gallery works; large payloads, few per eval) ----------
def scrape_canvas_graphs():
    st = load_state()
    key = "canvas_graphs"
    done = set(st["done"].get(key, []))
    gal = {}
    for l in open(f"{BASE}/download/aixstudio/gallery.jsonl"):
        r = json.loads(l)
        cid = str(r.get("canvasJsonInfoId") or "")
        if cid:
            gal[cid] = {"galleryId": r["id"], "title": r.get("name"), "author": r.get("nickName")}
    todo = [c for c in gal if c not in done]
    print(f"[canvas_graphs] targets={len(gal)} done={len(done)} todo={len(todo)}")
    outl = f"{OUT}/canvas_graphs.jsonl"
    B = 2
    for i in range(0, len(todo), B):
        batch = todo[i:i + B]
        bl = json.dumps(batch)
        js = f"""(async()=>{{
          {js_hdr()}
          const ids={bl}; const out=[];
          for(const id of ids){{
            try{{
              const r=await fetch('/apinew/comfy/canvas-json-copy-info/detail?id='+id,{{headers:H}});
              const j=await r.json();
              if(j.code===333){{ out.push({{id,err:'AUTH333'}}); continue; }}
              if(j.code!==200||!j.data){{ out.push({{id,err:j.code+' '+(j.message||'').slice(0,25)}}); continue; }}
              out.push({{id, name:j.data.name, createUserId:j.data.createUserId,
                         createTime:j.data.createTime, updateTime:j.data.updateTime,
                         canvasContent:j.data.canvasContent}});
            }}catch(e){{ out.push({{id,err:e.message.slice(0,40)}}); }}
          }}
          return JSON.stringify(out);
        }})()"""
        for attempt in range(4):
            try:
                d = aix_transport.eval_json(js, timeout=300)
                break
            except Exception as e:
                print(f"  retry batch {i} ({attempt+1}): {str(e)[:100]}")
                time.sleep(3 + attempt * 2)
        else:
            print(f"  SKIP batch at {i}")
            continue
        auth_fail = False
        with open(outl, "a") as f:
            for rec in d:
                if rec.get("err"):
                    if rec["err"] == "AUTH333":
                        auth_fail = True
                    print(f"  {rec['id']}: {rec['err']}")
                    continue
                meta = gal.get(rec["id"], {})
                f.write(json.dumps({
                    "canvasJsonInfoId": rec["id"], "galleryId": meta.get("galleryId"),
                    "galleryTitle": meta.get("title"), "galleryAuthor": meta.get("author"),
                    "name": rec.get("name"), "createUserId": rec.get("createUserId"),
                    "createTime": rec.get("createTime"), "updateTime": rec.get("updateTime"),
                    "canvasContent": rec.get("canvasContent"),
                }, ensure_ascii=False) + "\n")
                done.add(rec["id"])
        if auth_fail:
            print("!! TOKEN EXPIRED — refresh and rerun")
            sys.exit(2)
        st["done"][key] = sorted(done)
        save_state(st)
        if (i // B) % 10 == 0:
            print(f"  progress {i+len(batch)}/{len(todo)} (total done {len(done)})")
        time.sleep(0.5)
    print(f"[canvas_graphs] complete: {len(done)}")


# ---------- flow details (canvas-type flows) ----------
def scrape_flow_details():
    st = load_state()
    key = "flow_details"
    done = set(st["done"].get(key, []))
    flows = [json.loads(l) for l in open(f"{BASE}/download/aixstudio/flows.jsonl")]
    seen, targets = set(), []
    for r in flows:
        fid = str(r["id"])
        if fid in seen or fid in done:
            continue
        seen.add(fid)
        targets.append(fid)   # ALL types: comfy(1) flows carry aix-format graphs too
    print(f"[flow_details] flows={len(seen)} done={len(done)} todo={len(targets)}")

    # ~40MB chunk rotation: GitHub warns >50MB/file, blocks >100MB. Full sweep
    # is ~110MB of canvasJson, so flow_details lives in flow_details*.jsonl.
    CHUNK = 40 * 1024 * 1024
    def chunk_files():
        import glob as _g
        fs = sorted(_g.glob(f"{OUT}/flow_details*.jsonl"))
        # keep the un-suffixed file first, then _2, _3 ... in numeric order
        def keyf(p):
            sfx = p[len(f"{OUT}/flow_details"):].replace(".jsonl", "")
            return (0, "") if sfx == "" else (1, sfx)
        return sorted(fs, key=keyf)
    files = chunk_files()
    outl = files[-1] if files else f"{OUT}/flow_details.jsonl"
    def rotate():
        nonlocal outl
        import glob as _g
        n = len(_g.glob(f"{OUT}/flow_details*.jsonl"))
        outl = f"{OUT}/flow_details_{n + 1}.jsonl"
        print(f"  rotating output -> {os.path.basename(outl)}")

    B = 6
    for i in range(0, len(targets), B):
        batch = targets[i:i + B]
        bl = json.dumps(batch)
        # Concurrent in-page fetches (same load profile as the paginated
        # scrapers' 4-worker pattern; the WAF has never objected). This is
        # ~6x faster than sequential awaits — the full 2.8k-flow sweep needs
        # it to finish in ~30min instead of ~3h.
        js = f"""(async()=>{{
          {js_hdr()}
          const ids={bl};
          const out = await Promise.all(ids.map(async (id)=>{{
            try{{
              const r=await fetch('/apinew/comfy/canvas-flow/details?id='+id,{{headers:H}});
              const j=await r.json();
              if(j.code===333){{ return {{id,err:'AUTH333'}}; }}
              if(j.code!==200||!j.data){{ return {{id,err:j.code+' '+(j.message||'').slice(0,25)}}; }}
              return Object.assign({{__flowId:id}}, j.data);
            }}catch(e){{ return {{id,err:e.message.slice(0,40)}}; }}
          }}));
          return JSON.stringify(out);
        }})()"""
        for attempt in range(4):
            try:
                d = aix_transport.eval_json(js, timeout=300)
                break
            except Exception as e:
                print(f"  retry batch {i} ({attempt+1}): {str(e)[:100]}")
                time.sleep(3 + attempt * 2)
        else:
            print(f"  SKIP batch at {i}")
            continue
        auth_fail = False
        wrote = False
        with open(outl, "a") as f:
            for rec in d:
                if rec.get("err"):
                    if rec["err"] == "AUTH333":
                        auth_fail = True
                    print(f"  {rec['id']}: {rec['err']}")
                    continue
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                done.add(str(rec.get("__flowId")))
                wrote = True
        if wrote and os.path.exists(outl) and os.path.getsize(outl) > CHUNK:
            rotate()
        if auth_fail:
            print("!! TOKEN EXPIRED — refresh and rerun")
            sys.exit(2)
        st["done"][key] = sorted(done)
        save_state(st)
        if (i // B) % 10 == 0:
            print(f"  progress {i+len(batch)}/{len(targets)} (done {len(done)})")
        time.sleep(0.4)
    print(f"[flow_details] complete: {len(done)}")


# ---------- singles (one-shot endpoints) ----------
def scrape_singles():
    singles = {
        "style_prompts": "/apinew/comfy/style-prompt/lists",
        "style_tags": "/apinew/comfy/style-tag/lists",
        "character_categories": "/apinew/comfy/character-lib/categories",
        "prop_categories": "/apinew/comfy/prop-lib/categories",
        "scene_categories": "/apinew/comfy/scene-lib/categories",
        "scene_filters": "/apinew/comfy/scene-lib/filters",
        "character_filters": "/apinew/comfy/character-lib/filters",
        "prop_filters": "/apinew/comfy/prop-lib/filters",
        # v3 additions (previously missing endpoints):
        "tag_info": "/apinew/comfy/tag-info/listPage?pageNum=1&current=1&size=100",
        "partner_info": "/apinew/comfy/partner-info/getPartnerToHome",
        "top_des": "/apinew/comfy/top-des-info/listAll",
        "material_tags": "/apinew/comfy/material-tag/listParent",
        "prompt_components": "/apinew/comfy/prompt-info/listAll",
    }
    out = {}
    for name, path in singles.items():
        js = (f"(async()=>{{ {js_hdr()} const r=await fetch('{path}',{{headers:H}}); "
              f"const j=await r.json(); return JSON.stringify({{code:j.code,data:j.data}}); }})()")
        for attempt in range(3):
            try:
                d = aix_transport.eval_json(js)
                out[name] = d
                n = 0
                dat = d.get("data")
                if isinstance(dat, list):
                    n = len(dat)
                elif isinstance(dat, dict):
                    n = sum(len(v) for v in dat.values() if isinstance(v, list)) or len(dat)
                print(f"{name}: code {d.get('code')} ({n} items)")
                break
            except Exception as e:
                print(f"{name}: retry ({str(e)[:60]})")
                time.sleep(2)
    # atomic write with critical-section guard: the builders' category maps /
    # filter labels come from singles.json. A partial fetch (code != 200 on a
    # critical section, or transport death above) must NEVER wipe the previous
    # good file — that would silently de-categorize every library record
    # (audit wave 1 P2).
    critical = ("character_categories", "prop_categories", "scene_categories",
                "character_filters", "prop_filters", "scene_filters",
                # audit wave 2: a transient failure on either of these wipes
                # whole collections from the catalog (0 records) and no floor
                # caught it (parity passes at 0 == 0)
                "style_prompts", "prompt_components")
    bad = [k for k in critical if not out.get(k, {}).get("data")]
    if bad:
        print(f"singles: CRITICAL sections failed ({bad}) — keeping previous file")
        return False
    tmp = f"{OUT}/singles.json.tmp"
    json.dump(out, open(tmp, "w"), ensure_ascii=False, indent=1)
    os.replace(tmp, f"{OUT}/singles.json")
    return True


if __name__ == "__main__":
    which = sys.argv[1:] or ["canvas_graphs", "flow_details", "character_lib", "prop_lib", "scene_lib", "singles"]
    ok = True
    if "canvas_graphs" in which:
        scrape_canvas_graphs()
    if "flow_details" in which:
        scrape_flow_details()
    for lib in ("character_lib", "prop_lib", "scene_lib"):
        if lib in which:
            scrape_paged(lib, PAGED_LIBS[lib][0], PAGED_LIBS[lib][1])
    if "singles" in which:
        ok = scrape_singles() and ok
    print("DONE" if ok else "DONE_WITH_ERRORS")
    sys.exit(0 if ok else 1)
