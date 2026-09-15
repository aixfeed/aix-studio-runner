#!/usr/bin/env python3
"""Re-fetch the 8 previously-empty gallery canvases and replace their records
in canvas_graphs.jsonl + auth state (site now returns data for them)."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aix_transport

BASE = os.environ.get("AIX_BASE", "/home/z/my-project")
OUT = f"{BASE}/download/aixstudio/auth/canvas_graphs.jsonl"
ST = f"{BASE}/scripts/aix_auth_state.json"
TOKEN = open(f"{BASE}/scripts/.aix_token").read().strip()

REDO = [2231, 2204, 2132, 2223, 2208, 1845, 1834, 2222]

# 1. fetch fresh copies
js_hdr = f"const H={{token:'{TOKEN}'}};"
bl = json.dumps(REDO)
js = f"""(async()=>{{
  {js_hdr}
  const ids={bl}; const out=[];
  for(const id of ids){{
    try{{
      const r=await fetch('/apinew/comfy/canvas-json-copy-info/detail?id='+id,{{headers:H}});
      const j=await r.json();
      if(j.code!==200||!j.data){{ out.push({{id,err:(j.code)+' '+(j.message||'')}}); continue; }}
      out.push({{id, name:j.data.name, createUserId:j.data.createUserId,
                 createTime:j.data.createTime, updateTime:j.data.updateTime,
                 canvasContent:j.data.canvasContent}});
    }}catch(e){{ out.push({{id,err:e.message.slice(0,40)}}); }}
  }}
  return JSON.stringify(out);
}})()"""
recs = aix_transport.eval_json(js, timeout=180)

fresh = {}
for rec in recs:
    if rec.get("err"):
        print(f"{rec['id']}: STILL ERR {rec['err']}")
        continue
    content = rec.get("canvasContent")
    try:
        nodes = len(json.loads(content).get("nodes") or []) if content else 0
    except Exception:
        nodes = 0
    print(f"{rec['id']}: {nodes} nodes")
    if nodes:
        fresh[str(rec["id"])] = rec

# 2. rewrite jsonl replacing old lines for these ids (keep gallery meta from old lines)
lines_out, replaced = [], set()
gal_meta = {}
for l in open(f"{BASE}/download/aixstudio/gallery.jsonl"):
    r = json.loads(l)
    cid = str(r.get("canvasJsonInfoId") or "")
    if cid:
        gal_meta[cid] = {"galleryId": r["id"], "title": r.get("name"), "author": r.get("nickName")}

for l in open(OUT):
    try:
        r = json.loads(l)
    except Exception:
        continue
    cid = str(r.get("canvasJsonInfoId"))
    if cid in fresh:
        meta = gal_meta.get(cid, {})
        rec = fresh[cid]
        lines_out.append(json.dumps({
            "canvasJsonInfoId": rec["id"], "galleryId": r.get("galleryId") or meta.get("galleryId"),
            "galleryTitle": r.get("galleryTitle") or meta.get("title"),
            "galleryAuthor": r.get("galleryAuthor") or meta.get("author"),
            "name": rec.get("name"), "createUserId": rec.get("createUserId"),
            "createTime": rec.get("createTime"), "updateTime": rec.get("updateTime"),
            "canvasContent": rec.get("canvasContent"),
        }, ensure_ascii=False) + "\n")
        replaced.add(cid)
    else:
        lines_out.append(l)

with open(OUT, "w") as f:
    f.writelines(lines_out)
print(f"replaced {len(replaced)}/{len(REDO)} records in canvas_graphs.jsonl")

# 3. state: ensure these ids are in done
st = json.load(open(ST))
done = set(st["done"].get("canvas_graphs", [])) | {str(i) for i in REDO}
st["done"]["canvas_graphs"] = sorted(done)
json.dump(st, open(ST, "w"), indent=1)
print(f"state done(canvas_graphs)={len(done)}")
