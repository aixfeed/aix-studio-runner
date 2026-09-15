#!/usr/bin/env python3
"""Deep analysis of raw datasets: what is actually IN the fields?"""
import json, base64, collections, re

OUT = "/home/z/my-project/download/aixstudio"

def load(name):
    return [json.loads(l) for l in open(f"{OUT}/{name}.jsonl")]

print("=" * 70)
print("TASK_OUTPUTS (223) — the generation records")
print("=" * 70)
tos = load("task_outputs")
# param field: JSON string?
param_shapes = collections.Counter()
for r in tos:
    p = r.get("param")
    if p is None: param_shapes["null"] += 1; continue
    try:
        j = json.loads(p)
        param_shapes["json:" + ",".join(sorted(j.keys()))[:120]] += 1
    except Exception:
        param_shapes["raw:" + str(p)[:40]] += 1
for k, v in param_shapes.most_common(10):
    print(f"  {v:4d}x {k}")

# decode outPutFileKey sample
print("\noutPutFileKey decode attempts:")
for r in tos[:3]:
    k = r.get("outPutFileKey", "")
    try:
        dec = base64.b64decode(k).decode("utf-8", "replace")
        print(f"  id={r['id']} -> {dec[:100]}")
    except Exception as e:
        print(f"  id={r['id']} -> b64 fail: {str(e)[:50]}")

# outPutType distribution + media fields
print("\nmedia fields present:")
for r in tos[:2]:
    print(f"  outPutType={r.get('outPutType')} findUrl={str(r.get('findUrl'))[:80]}")
    print(f"    thumb={str(r.get('thumbnailUrl'))[:80]} w={r.get('width')} h={r.get('height')} size={r.get('fileSize')}")

print()
print("=" * 70)
print("PRISM_MATERIAL (1,177)")
print("=" * 70)
pms = load("prism_material")
cw = collections.Counter()
for r in pms:
    c = r.get("cueWord")
    cw["has_cueWord" if c else "no_cueWord"] += 1
print(" cueWord (prompt) presence:", dict(cw))
print(" sample cueWords:")
for r in pms:
    if r.get("cueWord"):
        print(f"   - {r['cueWord'][:120]}")
        break
# file type from URL
exts = collections.Counter()
for r in pms:
    u = str(r.get("findUrl") or "")
    m = re.search(r"\.(png|jpe?g|webp|mp4|mov|webm|mp3|wav)(\?|$)", u, re.I)
    exts[m.group(1).lower() if m else "unknown"] += 1
print(" media extensions:", dict(exts))
print(" showType:", dict(collections.Counter(str(r.get("showType")) for r in pms)))
print(" sample rec:", json.dumps({k: v for k, v in pms[0].items() if v is not None}, ensure_ascii=False)[:300])

print()
print("=" * 70)
print("GALLERY (303)")
print("=" * 70)
gal = load("gallery")
exts = collections.Counter()
for r in gal:
    u = str(r.get("workFileKey") or "")
    m = re.search(r"\.(png|jpe?g|webp|mp4|mov|webm|mp3|wav)(\?|$)", u, re.I)
    exts[m.group(1).lower() if m else ("none" if not u else "unknown")] += 1
print(" work file types:", dict(exts))
lens = [len(str(r.get("des") or "")) for r in gal]
print(f" description lengths: min={min(lens)} avg={sum(lens)//len(lens)} max={max(lens)}")
print(f" with des: {sum(1 for l in lens if l > 0)}")

print()
print("=" * 70)
print("FLOWS (3,022)")
print("=" * 70)
fl = load("flows")
for t in ["1", "2", "3", "4", "None"]:
    sub = [r for r in fl if str(r.get("type")) == t]
    if not sub: continue
    with_prev = sum(1 for r in sub if r.get("findUrl"))
    print(f" type {t}: {len(sub)} recs, {with_prev} with preview media")
    print(f"   sample: name={sub[0].get('name','?')[:40]} media={str(sub[0].get('findUrl'))[:70]}")
    print(f"   des sample: {str([r.get('des') for r in sub if r.get('des')][:1])[:120]}")

print()
print("=" * 70)
print("ASSETS (5,993)")
print("=" * 70)
as_ = load("assets")
print(" keys:", list(as_[0].keys()))
print(" sample:", json.dumps({k: v for k, v in as_[0].items() if v is not None}, ensure_ascii=False)[:400])
exts = collections.Counter()
for r in as_:
    u = str(r.get("findUrl") or r.get("fileKey") or "")
    m = re.search(r"\.(png|jpe?g|webp|mp4|mov|webm|glb|obj)(\?|$)", u, re.I)
    exts[m.group(1).lower() if m else "none/unknown"] += 1
print(" media extensions:", dict(exts))
