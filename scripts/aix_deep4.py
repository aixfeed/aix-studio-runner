#!/usr/bin/env python3
"""Extract prompts, models, seeds, sizes from task_outputs -> understand AIGC value."""
import json, collections

OUT = "/home/z/my-project/download/aixstudio"
tos = [json.loads(l) for l in open(f"{OUT}/task_outputs.jsonl")]

PROMPT_COMPS = {"Positive", "MorePositive"}
SELECT_COMPS = {"AgentSelectOne", "AgentSelect", "CustomDropSelect", "AgentRatioSelect"}

n_prompt, n_sel, n_seed, n_size = 0, 0, 0, 0
prompt_lens = []
agent_vals = collections.Counter()
sel_names = collections.Counter()
samples = []

for r in tos:
    p = r.get("param")
    if not p: continue
    try: j = json.loads(p)
    except: continue
    rec = {"id": r["id"], "prompts": [], "models": [], "seeds": [], "sizes": []}
    for item in (j.get("paramList") or []):
        c = item.get("component")
        dv = str(item.get("defValue") or "")
        if c in PROMPT_COMPS and dv.strip():
            rec["prompts"].append({"field": item.get("name"), "text": dv})
            prompt_lens.append(len(dv))
        elif c in SELECT_COMPS and dv.strip():
            rec["models"].append({"field": item.get("name"), "value": dv})
            agent_vals[dv] += 1
            sel_names[str(item.get("name"))] += 1
        elif c == "SeedNoShow" and dv:
            rec["seeds"].append(dv)
        elif c in ("DrawSize", "Width", "Height", "CustomNumberSlider") and dv:
            rec["sizes"].append({"field": item.get("name"), "value": dv})
    if rec["prompts"] or rec["models"]:
        samples.append(rec)

print(f"records with prompts: {sum(1 for s in samples if s['prompts'])}")
print(f"records with model selections: {sum(1 for s in samples if s['models'])}")
print(f"prompt lengths: avg={sum(prompt_lens)//max(1,len(prompt_lens))} max={max(prompt_lens) if prompt_lens else 0}")

print("\n=== agent/model selection values (the actual model taxonomy) ===")
for k, v in agent_vals.most_common(25):
    print(f"  {v:3d}  {k[:70]}")

print("\n=== selection field names ===")
for k, v in sel_names.most_common(15):
    print(f"  {v:3d}  {k}")

print("\n=== full parsed examples (3) ===")
for s in samples[:3]:
    print(json.dumps(s, ensure_ascii=False, indent=1)[:900])
    print("---")
