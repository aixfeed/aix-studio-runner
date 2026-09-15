#!/usr/bin/env python3
"""Deep dive: paramList structure in task_outputs + assets structure."""
import json, collections

OUT = "/home/z/my-project/download/aixstudio"

def load(name):
    return [json.loads(l) for l in open(f"{OUT}/{name}.jsonl")]

tos = load("task_outputs")

print("=== paramList anatomy (task_outputs) ===")
# find records with paramList
shown = 0
for r in tos:
    p = r.get("param")
    if not p: continue
    try: j = json.loads(p)
    except: continue
    pl = j.get("paramList")
    if pl and shown < 3:
        print(f"\n--- id={r['id']} outPutType={r.get('outPutType')} paramList ({len(pl)} items):")
        for item in pl[:4]:
            print("   ", json.dumps(item, ensure_ascii=False)[:220])
        shown += 1

# What keys appear across all paramLists?
keycount = collections.Counter()
n_with_pl = 0
for r in tos:
    p = r.get("param")
    if not p: continue
    try:
        j = json.loads(p)
        pl = j.get("paramList") or []
        if pl:
            n_with_pl += 1
            for item in pl:
                for k in item.keys(): keycount[k] += 1
    except: pass
print(f"\nrecords with paramList: {n_with_pl}")
print("paramList item keys freq:", dict(keycount.most_common(15)))

# paramStr?
print("\n=== paramStr samples ===")
shown = 0
for r in tos:
    p = r.get("param")
    if not p: continue
    try:
        j = json.loads(p)
        ps = j.get("paramStr")
        if ps and shown < 3:
            if isinstance(ps, str):
                try: ps2 = json.loads(ps)
                except: ps2 = ps
            else: ps2 = ps
            print(f"id={r['id']}: {json.dumps(ps2, ensure_ascii=False)[:400]}")
            shown += 1
    except: pass

# top-level param keys across all
topkeys = collections.Counter()
for r in tos:
    p = r.get("param")
    if not p: continue
    try:
        j = json.loads(p)
        for k in j.keys(): topkeys[k] += 1
    except: pass
print("\ntop-level param keys:", dict(topkeys.most_common(20)))

# speedNum / spendNum / runTime => cost info?
print("\n=== cost fields ===")
for r in tos[:5]:
    p = r.get("param") or "{}"
    try: j = json.loads(p)
    except: j = {}
    print(f"id={r['id']} speed={j.get('speedNum')} spend={j.get('spendNum')} runTime={j.get('runTime')} taskGroup={j.get('taskGroupInfoId')}")

print("\n\n=== ASSETS anatomy ===")
as_ = load("assets")
print("keys:", sorted(as_[0].keys()))
# distinct values for interesting fields
for f in ["type", "fileType", "assetType", "classify", "category"]:
    vals = collections.Counter(str(r.get(f)) for r in as_)
    if len(vals) > 1 or next(iter(vals)) != "None":
        print(f"{f}: {dict(vals.most_common(8))}")
# sample non-null records
for r in as_[:3]:
    print("sample:", json.dumps({k: v for k, v in r.items() if v is not None}, ensure_ascii=False)[:350])
