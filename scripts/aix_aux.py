#!/usr/bin/env python3
"""Auxiliary public data: tag trees, menus, prompts, comments for gallery items."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aix_transport

BASE = os.environ.get("AIX_BASE", "/home/z/my-project")
OUT = f"{BASE}/download/aixstudio"

def get_json(path):
    js = f"(async()=>JSON.stringify(await (await fetch('{path}')).json()))()"
    return aix_transport.eval_json(js)

# 1. simple endpoints -> one file each
simple = {
    "flow_tags": "/apinew/comfy/canvas-flow-tag-info/listTree",
    "asset_tags": "/apinew/comfy/canvas-asset-tag-info/listTree",
    "home_menu": "/apinew/comfy/home-menu-info/listAll?validFlag=1",
    "advertisements": "/apinew/comfy/top-des-info/advertisement",
    "user_tags": "/apinew/comfy/tag-info/listAll?tagCode=user",
}
aux = {}
for name, path in simple.items():
    for attempt in range(3):
        try:
            aux[name] = get_json(path)
            print(f"{name}: OK")
            break
        except Exception as e:
            print(f"{name}: retry ({e})"); time.sleep(2)

json.dump(aux, open(f"{OUT}/aux_public.json", "w"), ensure_ascii=False, indent=1)

# 2. comments for all gallery items (batched)
ids = [json.loads(l)["id"] for l in open(f"{OUT}/gallery.jsonl")]
print(f"gallery ids: {len(ids)}")
comments = {}
B = 40
for i in range(0, len(ids), B):
    batch = ids[i:i+B]
    bl = json.dumps(batch)
    js = f"""(async()=>{{
      const ids = {bl};
      const out = {{}};
      const q = ids.slice();
      async function w(){{
        while(q.length){{
          const id = q.shift();
          try{{
            const r = await fetch('/apinew/comfy/reply-info/getAllReplayById?outputFileId='+id+'&type=2');
            const j = await r.json();
            out[id] = (j.code===200 && j.data) ? {{total: j.data.total, comment: j.data.comment}} : {{err: j.code}};
          }}catch(e){{ out[id] = {{err: e.message}}; }}
        }}
      }}
      await Promise.all([w(),w(),w(),w(),w(),w()]);
      return JSON.stringify(out);
    }})()"""
    for attempt in range(3):
        try:
            d = aix_transport.eval_json(js)
            comments.update(d)
            break
        except Exception as e:
            print(f"comments batch {i}: retry ({str(e)[:100]})"); time.sleep(3)
    nz = sum(1 for v in d.values() if v.get("total", 0) > 0)
    print(f"  comments {i}-{i+len(batch)}: {nz} with comments")
    time.sleep(0.6)

json.dump(comments, open(f"{OUT}/gallery_comments.json", "w"), ensure_ascii=False, indent=1)
with_comments = {k: v for k, v in comments.items() if v.get("total", 0) > 0}
print(f"TOTAL items with comments: {len(with_comments)}")
print("DONE")
