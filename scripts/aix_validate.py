#!/usr/bin/env python3
"""
Pre-push validation gate for the AIX Studio pipeline (used by GHA + locally).

Exit 0 = data healthy; exit 1 = defects found (blocks the push).

Checks:
  1. JSONL integrity   — every line of every .jsonl parses; parse failures = defect
  2. Catalog parity    — every catalog JSON parses; index counts == array lengths
  3. Regression floors — cumulative datasets must not crater (accumulate model:
                         shrinkage or near-empty = silent scrape breakage)
  4. Cross-refs        — every gallery canvasJsonInfoId has a graph (except the
                         4 known server-null ids); every type-2 flow has a
                         flow_details record
  5. Size guards       — no data file > 90 MB (GitHub 100 MB hard limit early warn)
  6. State sanity      — auth state done-set consistent with graph files
  7. Media hygiene     — sampled media URLs are absolute https URLs
"""
import glob
import json
import os
import sys

BASE = os.environ.get("AIX_BASE", "/home/z/my-project")
DATA = f"{BASE}/download/aixstudio"
AUTH = f"{DATA}/auth"
CAT = f"{BASE}/src/data/catalog"

FAILS, WARNS = [], []

# Known server-side-null gallery canvas ids (code 200, data=null; retried each run)
KNOWN_NULL_CANVAS = {"10337", "10652", "2875", "3621"}

# Cumulative floors: accumulate model means counts only go UP. A drop below
# floor = catastrophic silent failure (WAF block, schema drift, empty scrape).
FLOORS = {
    "gallery": 300, "flows": 3000, "assets": 7000, "prism_material": 1100,
    "task_outputs": 200, "user_prism": 30,
    "auth/character_lib": 5800, "auth/prop_lib": 4800, "auth/scene_lib": 1400,
    "auth/canvas_graphs": 300, "auth/flow_details": 315,
    "catalog/canvases": 620, "catalog/characters": 5800, "catalog/props": 4800,
}


def fail(msg):
    FAILS.append(msg)
    print(f"  FAIL  {msg}")


def warn(msg):
    WARNS.append(msg)
    print(f"  WARN  {msg}")


def ok(msg):
    print(f"  ok    {msg}")


print("== 1. JSONL integrity ==")
jsonl_files = sorted(glob.glob(f"{DATA}/*.jsonl")) + sorted(glob.glob(f"{AUTH}/*.jsonl"))
counts = {}
for path in jsonl_files:
    rel = os.path.relpath(path, DATA)
    n, bad = 0, 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                json.loads(line)
                n += 1
            except Exception:
                bad += 1
    counts[rel] = n
    if bad:
        fail(f"{rel}: {bad} unparseable lines")
    else:
        ok(f"{rel}: {n} records")

print("== 2. Catalog parity ==")
idx = json.load(open(f"{CAT}/index.json"))
parity_bad = False
for name, expected in idx["counts"].items():
    p = f"{CAT}/{name}.json"
    if not os.path.exists(p):
        fail(f"catalog/{name}.json missing")
        parity_bad = True
        continue
    arr = json.load(open(p))
    if len(arr) != expected:
        fail(f"catalog/{name}: index says {expected}, file has {len(arr)}")
        parity_bad = True
if not parity_bad:
    ok(f"all {len(idx['counts'])} catalog files match index counts")

print("== 3. Regression floors ==")
for key, floor in FLOORS.items():
    if key.startswith("catalog/"):
        name = key.split("/", 1)[1]
        actual = len(json.load(open(f"{CAT}/{name}.json")))
    else:
        actual = counts.get(key, counts.get(f"{key}.jsonl", 0))
    if actual < floor:
        fail(f"{key}: {actual} < floor {floor}")
    else:
        ok(f"{key}: {actual} >= {floor}")

print("== 4. Cross-references ==")
graphs = set()
for l in open(f"{AUTH}/canvas_graphs.jsonl"):
    r = json.loads(l)
    graphs.add(str(r["canvasJsonInfoId"]))
gal_refs = set()
for l in open(f"{DATA}/gallery.jsonl"):
    r = json.loads(l)
    cid = str(r.get("canvasJsonInfoId") or "")
    if cid:
        gal_refs.add(cid)
missing_graphs = (gal_refs - graphs) - KNOWN_NULL_CANVAS
if missing_graphs:
    fail(f"gallery canvases without graphs (beyond known-null): {sorted(missing_graphs)[:10]}")
else:
    ok(f"gallery->canvas graphs: {len(gal_refs & graphs)}/{len(gal_refs)} "
       f"({len(KNOWN_NULL_CANVAS)} known-null excluded)")

flow_graphs = set()
for l in open(f"{AUTH}/flow_details.jsonl"):
    r = json.loads(l)
    fid = str(r.get("__flowId") or "")
    if fid:
        flow_graphs.add(fid)
t2 = set()
for l in open(f"{DATA}/flows.jsonl"):
    r = json.loads(l)
    if str(r.get("type")) == "2":
        t2.add(str(r["id"]))
missing_flows = t2 - flow_graphs
if missing_flows:
    fail(f"type-2 flows without details: {sorted(missing_flows)[:10]}")
else:
    ok(f"canvas-flows->details: {len(flow_graphs)}/{len(t2)}")

print("== 5. Size guards ==")
for path in jsonl_files + sorted(glob.glob(f"{CAT}/*.json")):
    sz = os.path.getsize(path)
    if sz > 90 * 1024 * 1024:
        fail(f"{os.path.relpath(path, BASE)}: {sz/1e6:.1f} MB > 90 MB GitHub guard")
    elif sz > 50 * 1024 * 1024:
        warn(f"{os.path.relpath(path, BASE)}: {sz/1e6:.1f} MB > 50 MB (GitHub warning zone)")

print("== 6. State sanity ==")
st = json.load(open(f"{BASE}/scripts/aix_auth_state.json"))
done_graphs = set(st["done"].get("canvas_graphs", []))
if (done_graphs - gal_refs) - KNOWN_NULL_CANVAS:
    warn("state done(canvas_graphs) contains ids not in gallery")
else:
    ok(f"auth state consistent (canvas_graphs done={len(done_graphs)})")

print("== 7. Media hygiene (sampled) ==")
import random
random.seed(42)
urls = []
for name in ("gallery", "flows", "assets"):
    lines = open(f"{DATA}/{name}.jsonl").readlines()
    for l in random.sample(lines, min(15, len(lines))):
        r = json.loads(l)
        for k in ("findUrl", "thumbnailUrl", "coverUrl"):
            if r.get(k):
                urls.append(r[k])
bad_urls = [u for u in urls if not (u.startswith("https://") or u.startswith("http://")) or " " in u]
if bad_urls:
    warn(f"{len(bad_urls)}/{len(urls)} sampled media URLs malformed: {bad_urls[:3]}")
else:
    ok(f"{len(urls)} sampled media URLs well-formed")

print()
total = sum(idx["counts"].values())
print(f"catalog total: {total} records across {len(idx['counts'])} types")
print(f"result: {len(FAILS)} defects, {len(WARNS)} warnings")
if FAILS:
    print("VALIDATION FAILED — blocking push")
    sys.exit(1)
print("VALIDATION PASSED")
sys.exit(0)
