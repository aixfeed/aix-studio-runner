#!/usr/bin/env python3
"""Probe for lib detail endpoints (scene/character/prop) beyond listPage."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aix_transport

TOKEN = open("/home/z/my-project/scripts/.aix_token").read().strip()
t = aix_transport.pick() if hasattr(aix_transport, "pick") else aix_transport.AgentBrowserTransport()

def probe(path):
    js = f"""(async()=>{{
      const H={{token:'{TOKEN}'}};
      try{{
        const r=await fetch('{path}',{{headers:H}});
        const txt=await r.text();
        return JSON.stringify({{status:r.status, body:txt.slice(0,700)}});
      }}catch(e){{return JSON.stringify({{status:-1, body:String(e)}})}}
    }})()"""
    try:
        raw = t.eval_js(js)
        return json.loads(aix_transport.unquote(raw))
    except Exception as e:
        return {"status": -2, "body": str(e)[:300]}

CANDIDATES = [
    # scene detail patterns
    "/apinew/comfy/scene-lib/detail?id=164",
    "/apinew/comfy/scene-lib/getById?id=164",
    "/apinew/comfy/scene-lib/info?id=164",
    "/apinew/comfy/scene-lib/getInfo?id=164",
    "/apinew/comfy/scene-lib/detail/164",
    "/apinew/comfy/scene-lib?id=164",
    # character detail
    "/apinew/comfy/character-lib/detail?id=134",
    "/apinew/comfy/character-lib/getById?id=134",
    # prop detail
    "/apinew/comfy/prop-lib/detail?id=458",
    "/apinew/comfy/prop-lib/getById?id=458",
]

for c in CANDIDATES:
    r = probe(c)
    body_head = (r.get("body") or "")[:160].replace("\n", " ")
    print(f"{r.get('status'):>4}  {c}\n      {body_head}\n")
