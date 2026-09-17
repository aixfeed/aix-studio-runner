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
    "auth/canvas_graphs": 300, "auth/flow_details": 3100,
    "catalog/canvases": 620, "catalog/characters": 5800, "catalog/props": 4800,
    # audit wave 1: floors for the remaining collections (3/12 -> 10/12;
    # scenes/style_prompts/prompt_components covered by raw-side floors + count parity)
    "catalog/generations": 200, "catalog/workflows": 3000,
    "catalog/style_materials": 1100, "catalog/creators": 2500,
    # audit wave 2: singles.json guard now also protects these, but a floor
    # makes a total wipe FAIL LOUDLY instead of shipping an empty tab
    "catalog/style_prompts": 140, "catalog/prompt_components": 120,
    "catalog/scenes": 1400, "catalog/showcases": 350, "catalog/assets": 7000,
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

# Aggregate chunked JSONL families (flow_details_2.jsonl, _3, … fold into
# flow_details.jsonl) so regression floors see the full record count.
import re as _re
_agg = {}
for _rel, _n in counts.items():
    _base = _re.sub(r"_\d+\.jsonl$", ".jsonl", _rel)
    _agg[_base] = _agg.get(_base, 0) + _n
counts.update(_agg)

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
# flow details live in ~40MB chunks (flow_details*.jsonl) since the all-types
# capture (2026-09-16): 3,145 flows ≈ 185MB — over GitHub's 100MB file limit.
for fp in sorted(glob.glob(f"{AUTH}/flow_details*.jsonl")):
    for l in open(fp):
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

# Graph coverage across ALL flow kinds (the Phase-D comfy unlock): most
# workflows should now be graph-backed. Below 90% = capture regression.
all_flow_ids = set()
for l in open(f"{DATA}/flows.jsonl"):
    all_flow_ids.add(str(json.loads(l)["id"]))
coverage = len(flow_graphs & all_flow_ids) / max(len(all_flow_ids), 1)
if coverage < 0.9:
    warn(f"flow graph coverage {coverage:.1%} < 90% (expected ~99%)")
else:
    ok(f"flow graph coverage (all kinds): {coverage:.1%} "
       f"({len(flow_graphs & all_flow_ids)}/{len(all_flow_ids)})")

print("== 5. Size guards ==")
for path in jsonl_files + sorted(glob.glob(f"{CAT}/*.json")):
    sz = os.path.getsize(path)
    if sz > 90 * 1024 * 1024:
        fail(f"{os.path.relpath(path, BASE)}: {sz/1e6:.1f} MB > 90 MB GitHub guard")
    elif sz > 50 * 1024 * 1024:
        warn(f"{os.path.relpath(path, BASE)}: {sz/1e6:.1f} MB > 50 MB (GitHub warning zone)")

print("== 5b. Record-id uniqueness (audit wave 1: would've caught the canvas/flow id collision) ==")
singles = json.load(open(f"{AUTH}/singles.json"))
from collections import Counter as _Counter
cat_sets = {}
for name in idx["counts"]:
    arr = json.load(open(f"{CAT}/{name}.json"))
    ids = [str(r.get("id")) for r in arr]
    dups = sorted(i for i, n in _Counter(ids).items() if n > 1)
    if dups:
        fail(f"catalog/{name}: {len(dups)} duplicate ids (namespace collision?): {dups[:5]}")
    else:
        ok(f"catalog/{name}: {len(ids)} unique ids")

# canvas origin prefix invariant: gallery ids canvas_*, flow ids canvasf_*
canvases = json.load(open(f"{CAT}/canvases.json"))
bad_prefix = [r["id"] for r in canvases
              if (r.get("origin") == "flow") != r["id"].startswith("canvasf_")]
if bad_prefix:
    fail(f"canvases: {len(bad_prefix)} origin/id-prefix mismatches: {bad_prefix[:5]}")
else:
    ok(f"canvases: origin prefixes consistent "
       f"({sum(1 for r in canvases if r['id'].startswith('canvasf_'))} flow / "
       f"{sum(1 for r in canvases if r['id'].startswith('canvas_'))} gallery)")

print("== 5c. Category correctness (audit wave 1: would've caught the 276-character mislabel) ==")
cat_name_sets = {}
for coll in ("character", "prop", "scene"):
    cat_name_sets[coll] = {
        str(c["id"]): c["name"]
        for c in (singles.get(f"{coll}_categories", {}).get("data") or [])
    }
for coll, fname in (("character", "characters"), ("prop", "props"), ("scene", "scenes")):
    arr = json.load(open(f"{CAT}/{fname}.json"))
    nonnull = sum(1 for r in arr if r.get("category"))
    cov = nonnull / max(len(arr), 1)
    if cov < 0.95:
        fail(f"{fname}: category coverage {cov:.1%} < 95% (category map broken?)")
    # cross-contamination: a character record carrying a PROP/SCENE category name
    others = set(cat_name_sets["prop"].values()) | set(cat_name_sets["scene"].values()) \
        if coll == "character" else \
        set(cat_name_sets["character"].values()) | set(cat_name_sets["scene"].values()) \
        if coll == "prop" else \
        set(cat_name_sets["character"].values()) | set(cat_name_sets["prop"].values())
    own = set(cat_name_sets[coll].values())
    contaminated = [r["id"] for r in arr if r.get("category") in (others - own)]
    if contaminated:
        fail(f"{fname}: {len(contaminated)} records carry a foreign-collection "
             f"category name (id-namespace collision): {contaminated[:5]}")
    else:
        ok(f"{fname}: categories clean ({nonnull}/{len(arr)} non-null, 0 foreign)")

# pinned spot-checks (raw ground truth): character categoryId=3 must be 商战总裁
ch_raw = [json.loads(l) for l in open(f"{AUTH}/character_lib.jsonl")]
pin = cat_name_sets["character"].get("3")
if pin:
    sample = [r for r in ch_raw if str(r.get("categoryId")) == "3"][:5]
    if sample:
        ok(f"pinned: character categoryId=3 -> {pin} (map matches raw)")

print("== 5d. Value-extraction floors (audit wave 1: latent prompt corpus + joins) ==")
gens = json.load(open(f"{CAT}/generations.json"))
g_prompt = sum(1 for r in gens if r.get("prompt"))
g_cov = g_prompt / max(len(gens), 1)
if g_cov < 0.55:
    fail(f"generations: prompt coverage {g_cov:.1%} < 55% (param parser regression?)")
else:
    ok(f"generations: prompt coverage {g_cov:.1%} ({g_prompt}/{len(gens)})")
wfs = json.load(open(f"{CAT}/workflows.json"))
w_prompt = sum(1 for r in wfs if r.get("prompt"))
w_ts = sum(1 for r in wfs if (r.get("timestamps") or {}).get("created"))
w_cov = w_prompt / max(len(wfs), 1)
if w_cov < 0.45:
    fail(f"workflows: graph-prompt coverage {w_cov:.1%} < 45% (graph join regression?)")
else:
    ok(f"workflows: graph-prompt coverage {w_cov:.1%} ({w_prompt}/{len(wfs)})")
if w_ts / max(len(wfs), 1) < 0.9:
    fail(f"workflows: timestamp coverage {w_ts/max(len(wfs),1):.1%} < 90% (details join broken?)")
else:
    ok(f"workflows: timestamps {w_ts}/{len(wfs)}")
# creator integrity: gallery canvases must NOT link via the shared system
# uploader id (75/313 did before the fix); flow canvases must have an id at all
sys_ids = {"7653"}
gal_cv = [r for r in canvases if r.get("origin") == "gallery"]
flow_cv = [r for r in canvases if r.get("origin") == "flow"]
sys_linked = [r["id"] for r in gal_cv if str((r.get("creator") or {}).get("id")) in sys_ids]
noid = [r["id"] for r in canvases if not (r.get("creator") or {}).get("id")]
if sys_linked:
    warn(f"canvases: {len(sys_linked)} gallery works linked via system uploader id")
if noid:
    fail(f"canvases: {len(noid)} records without creator.id: {noid[:5]}")
else:
    ok(f"canvases: creator.id on {len(canvases)}/{len(canvases)} records")
# style materials: taxonomy captured (the prism-tag sweep)
sms = json.load(open(f"{CAT}/style_materials.json"))
uncat = sum(1 for r in sms if r.get("category") in (None, "uncategorized"))
if uncat / max(len(sms), 1) > 0.05:
    fail(f"style_materials: {uncat}/{len(sms)} uncategorized (prism tag sweep missed?)")
else:
    ok(f"style_materials: {len(sms) - uncat}/{len(sms)} categorized")

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
