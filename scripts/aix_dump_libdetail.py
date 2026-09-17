#!/usr/bin/env python3
"""Dump full lib detail payloads to compare with listPage fields."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aix_transport

TOKEN = open("/home/z/my-project/scripts/.aix_token").read().strip()
t = aix_transport.AgentBrowserTransport()

def fetch(path):
    js = f"""(async()=>{{
      const H={{token:'{TOKEN}'}};
      const r=await fetch('{path}',{{headers:H}});
      const j=await r.json();
      return JSON.stringify(j);
    }})()"""
    raw = t.eval_js(js)
    return json.loads(aix_transport.unquote(raw))

out = {}
for name, path in [
    ("scene_164", "/apinew/comfy/scene-lib/detail?id=164"),
    ("scene_1", "/apinew/comfy/scene-lib/detail?id=1"),
    ("char_134", "/apinew/comfy/character-lib/detail?id=134"),
    ("prop_458", "/apinew/comfy/prop-lib/detail?id=458"),
]:
    try:
        j = fetch(path)
        out[name] = j.get("data") if isinstance(j, dict) else j
        print(f"=== {name} ===")
        print(json.dumps(out[name], ensure_ascii=False, indent=1)[:2500])
        print()
    except Exception as e:
        print(name, "ERR", str(e)[:200])

json.dump(out, open("/home/z/my-project/recon/lib_detail_samples.json", "w"), ensure_ascii=False, indent=1)
print("saved to recon/lib_detail_samples.json")
