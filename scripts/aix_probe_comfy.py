#!/usr/bin/env python3
"""Phase D probe: does canvas-flow/details return graph JSON for type-1
(ComfyUI) flows? Only type-2 (canvas) flows were captured before.

Probes the top-liked comfy flows + a random sample; reports the shape of the
response: canvasJson presence/format (ComfyUI nodes+links vs aix nodes+conns),
prompt fields, file fields.
"""
import json, os, sys, random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aix_transport import eval_json

TOKEN = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".aix_token")).read().strip()

flows = json.load(open(os.environ.get("AIX_BASE", "/home/z/my-project") + "/download/aixstudio/flows.deduped.json"))
t1 = [f for f in flows if f.get("type") == 1]
t1.sort(key=lambda f: f.get("goodNum") or 0, reverse=True)
sample = t1[:3] + random.Random(42).sample(t1, 5)

JS = """
(async () => {
  const r = await fetch('/apinew/comfy/canvas-flow/details?id=%s', {
    headers: {token: %s}
  });
  return JSON.stringify(await r.json());
})()
"""

for f in sample:
    fid = f["id"]
    try:
        resp = eval_json(JS % (fid, json.dumps(TOKEN)), timeout=60)
    except Exception as e:
        print(f"flow {fid}: TRANSPORT ERROR {e}")
        continue
    code = resp.get("code")
    data = resp.get("data") or {}
    cj = data.get("canvasJson")
    print(f"flow {fid} code={code} name={str(data.get('name') or f.get('name'))[:24]!r}")
    print(f"  keys: {sorted(data.keys())[:18]}")
    if cj:
        if isinstance(cj, str):
            print(f"  canvasJson: STRING len={len(cj)} head={cj[:120]!r}")
        else:
            print(f"  canvasJson: {type(cj).__name__} keys={sorted(cj.keys())[:18] if isinstance(cj, dict) else 'n/a'}")
            if isinstance(cj, dict):
                for k in ("nodes", "links", "connections", "groups"):
                    if k in cj:
                        v = cj[k]
                        print(f"    {k}: {len(v) if isinstance(v, list) else type(v).__name__}")
    else:
        print(f"  canvasJson: {cj!r}")
    print()
