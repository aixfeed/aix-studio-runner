#!/usr/bin/env python3
"""Extract full generation metadata: component census, prompts, models, sizes."""
import json, collections, re

OUT = "/home/z/my-project/download/aixstudio"
tos = [json.loads(l) for l in open(f"{OUT}/task_outputs.jsonl")]

comp_census = collections.Counter()
prompt_samples = []
param_strs = collections.Counter()
text_values = collections.Counter()

for r in tos:
    p = r.get("param")
    if not p: continue
    try: j = json.loads(p)
    except: continue
    ps = j.get("paramStr")
    if ps: param_strs[str(ps)[:60]] += 1
    for item in (j.get("paramList") or []):
        comp_census[item.get("component")] += 1
        dv = str(item.get("defValue") or "")
        # text-ish components with substantial content = prompts
        c = item.get("component") or ""
        if any(k in c.lower() for k in ["text", "prompt", "textarea", "script", "word"]) and len(dv) > 20:
            if len(prompt_samples) < 8:
                prompt_samples.append({"id": r["id"], "component": c, "name": item.get("name"), "value": dv[:300]})

print("=== component census (all paramList items) ===")
for k, v in comp_census.most_common(40):
    print(f"  {v:4d}  {k}")

print("\n=== paramStr values (generation settings summary) ===")
for k, v in param_strs.most_common(30):
    print(f"  {v:3d}  {k}")

print("\n=== prompt-like text samples ===")
for s in prompt_samples:
    print(f"\n  [{s['id']}] {s['component']} / {s['name']}:")
    print(f"    {s['value'][:250]}")
