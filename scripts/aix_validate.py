#!/usr/bin/env python3
"""
Pre-push validation gate for the AIX Studio pipeline (used by GHA + locally).

Exit 0 = data healthy; exit 1 = defects found (blocks the push).

Checks:
  1. JSONL integrity   — every line of every .jsonl parses; parse failures = defect
  2. Catalog parity    — every catalog JSON parses; index counts == array lengths
  3. Regression floors — cumulative datasets must not crater (accumulate model:
                         shrinkage or near-empty = silent scrape breakage).
                         Explicit per-type floors + structural floors for types
                         without one (historical counts in catalog/.floors.json)
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
    # round-11 adoptions (explicit floors — .floors.json high-water covers
    # them after the first green run, but an empty new tab must FAIL LOUD
    # on day one, not pass at 0 == 0). skills/contests are MUTABLE snapshots
    # the site curates — floors sit below current counts so legitimate
    # delisting doesn't hard-block (a wipe still trips them).
    "materials": 30000, "catalog/materials": 30000,
    "catalog/skills": 10, "catalog/contests": 3,
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

# audit round 9 P3-3: a NEW collection type in index.json used to ship with
# zero floors — an empty new tab is green (parity passes at 0 == 0). Every
# type in the index now gets a floor: explicit if FLOORS has one, else a
# STRUCTURAL floor derived from the historical count this validator itself
# persists in src/data/catalog/.floors.json (written only on GREEN runs,
# committed with the catalog): count > 0 AND count >= 25% of the historical
# high-water mark. First sighting (no history yet) only records the count —
# no chicken-and-egg on run one.
FLOORS_STATE = f"{CAT}/.floors.json"


def _as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


try:
    _hist = json.load(open(FLOORS_STATE)) if os.path.exists(FLOORS_STATE) else {}
except Exception:
    _hist = {}
_unfloored = [n for n in idx["counts"] if f"catalog/{n}" not in FLOORS]
_hist_next = {}
for _name in _unfloored:
    _actual = len(json.load(open(f"{CAT}/{_name}.json")))
    _h = _as_int(_hist.get(_name))
    _hist_next[_name] = max(_actual, _h)  # high-water mark: never ratchets down
    if _actual == 0:
        fail(f"catalog/{_name}: 0 records (new collection type scraped empty)")
    elif _h and _actual < _h * 0.25:
        fail(f"catalog/{_name}: {_actual} < 25% of historical {_h} (structural floor)")
    else:
        _fl = f"25% of historical {_h}" if _h else "first sighting — history recorded"
        ok(f"catalog/{_name}: {_actual} records (structural floor: {_fl})")
if not _unfloored:
    ok(f"all {len(idx['counts'])} collection types have explicit floors")

print("== 4. Cross-references ==")
graphs = set()
# canvas_graphs is chunk-rotated (round-9, canvas_graphs_2.jsonl, …): read
# ALL chunks (order irrelevant — building a set)
for _cg in sorted(glob.glob(f"{AUTH}/canvas_graphs*.jsonl")):
    for l in open(_cg):
        r = json.loads(l)
        graphs.add(str(r["canvasJsonInfoId"]))
gal_refs = set()
for l in open(f"{DATA}/gallery.jsonl"):
    r = json.loads(l)
    cid = str(r.get("canvasJsonInfoId") or "")
    if cid:
        gal_refs.add(cid)
missing_graphs = (gal_refs - graphs) - KNOWN_NULL_CANVAS
# R25 (audit r25-a P1-3): split missing into CORRUPTION (graph was fetched
# per the auth-state done-set but the corpus line is gone — must ALWAYS
# fail, token state irrelevant) vs PENDING (never fetched — with a dead
# token that is expected; backfills automatically on token refresh via
# id-dedup accumulate; warn + carrier file instead of fail).
TOKEN_DEAD = os.environ.get("AIX_TOKEN_DEAD") == "1"
try:
    _st = json.load(open(f"{BASE}/scripts/aix_auth_state.json"))
    _cg_done = {str(x) for x in _st.get("done", {}).get("canvas_graphs", [])}
    _fd_done = {str(x) for x in _st.get("done", {}).get("flow_details", [])}
except Exception:
    _cg_done, _fd_done = set(), set()
corrupt_graphs = missing_graphs & _cg_done
pending_graphs = missing_graphs - _cg_done
if corrupt_graphs:
    fail(f"gallery canvases LOST graphs (fetched per done-state, missing "
         f"from corpus — corruption, not auth): {sorted(corrupt_graphs)[:10]}")
elif pending_graphs and not TOKEN_DEAD:
    fail(f"gallery canvases without graphs (beyond known-null): "
         f"{sorted(pending_graphs)[:10]}")
elif pending_graphs:
    warn(f"{len(pending_graphs)} canvases pending auth recovery (token dead; "
         f"backfill on refresh): {sorted(pending_graphs)[:10]}")
if not missing_graphs:
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
# R25 (audit r25-a P1-4): same corrupt-vs-pending split as gallery graphs —
# a dead auth token makes NEW type-2 flows legitimately unfetchable.
corrupt_flows = missing_flows & _fd_done
pending_flows = missing_flows - _fd_done
if corrupt_flows:
    fail(f"type-2 flows LOST details (fetched per done-state, missing from "
         f"corpus — corruption): {sorted(corrupt_flows)[:10]}")
elif pending_flows and not TOKEN_DEAD:
    fail(f"type-2 flows without details: {sorted(pending_flows)[:10]}")
elif pending_flows:
    warn(f"{len(pending_flows)} type-2 flows pending auth recovery (token "
         f"dead; backfill on refresh): {sorted(pending_flows)[:10]}")
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

# pinned spot-checks (audit round 9 P3-1: the previous check validated the
# category map against ITSELF — a swapped or stale map stayed green). Pins are
# hardcoded EXPECTED labels, from the original collision lesson recorded in
# .agents/SKILL.md: character id 3 = 商战总裁 while prop id 3 = 剑类 (same id,
# DIFFERENT collections — the cost of learning that was every character record
# mislabeled for two sessions). The map must carry the pinned literal, and raw
# lib records with a pinned categoryId must surface in the catalog under that
# label (validates the builder applied the right map end-to-end).
PINS = {
    "character": {"1": "现代都市", "3": "商战总裁", "9": "霸道总裁"},
    "prop": {"3": "剑类", "9": "唐刀"},
    "scene": {"10001": "豪宅客厅", "10050": "病房", "10232": "金銮殿/太和殿"},
}
for coll, fname in (("character", "characters"), ("prop", "props"), ("scene", "scenes")):
    raw_by_cat = {}
    for l in open(f"{AUTH}/{coll}_lib.jsonl"):
        r = json.loads(l)
        raw_by_cat.setdefault(str(r.get("categoryId")), []).append(str(r["id"]))
    cat_by_source = {str(r["sourceId"]): r for r in json.load(open(f"{CAT}/{fname}.json"))}
    for cid, expected in PINS[coll].items():
        got = cat_name_sets[coll].get(cid)
        if got != expected:
            fail(f"{fname}: category map[{cid}] = {got!r} != pinned {expected!r} "
                 f"(swapped or stale category map?)")
            continue
        srcs = raw_by_cat.get(cid, [])[:3]
        joined = [(sid, cat_by_source.get(sid)) for sid in srcs]
        wrong = [s for s, r in joined if r is not None and r.get("category") != expected]
        if wrong:
            fail(f"{fname}: raw categoryId={cid} records carry the wrong category "
                 f"in the catalog (builder applied the wrong map?): {wrong}")
        elif not joined:
            ok(f"pinned: {fname} map[{cid}] = {expected} (map pin; no raw "
               f"records currently use this categoryId)")
        elif not any(r for _, r in joined):
            warn(f"{fname}: no catalog records for pinned categoryId={cid} "
                 f"sample {srcs} (map ok, records missing?)")
        else:
            n_ok = sum(1 for _, r in joined if r)
            ok(f"pinned: {fname} map[{cid}] = {expected} "
               f"({n_ok}/{len(joined)} sampled raw->catalog records agree)")

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
# canvases: prompt-coverage tripwires (audit round 9 P3-2 — canvases were the
# only prompt-bearing collection without a floor; a graph-prompt extraction
# regression would otherwise ship green). Catastrophe tripwires, not precision
# instruments — the band leaves room for organic drift both ways: fail < 55%,
# warn < 60% (organic level ~63%).
c_prompt = sum(1 for r in canvases if r.get("prompt"))
c_cov = c_prompt / max(len(canvases), 1)
if c_cov < 0.55:
    fail(f"canvases: prompt coverage {c_cov:.1%} < 55% (graph prompt extraction regression?)")
elif c_cov < 0.60:
    warn(f"canvases: prompt coverage {c_cov:.1%} < 60% (drifting toward the floor — investigate)")
else:
    ok(f"canvases: prompt coverage {c_cov:.1%} ({c_prompt}/{len(canvases)})")
if w_ts / max(len(wfs), 1) < 0.9:
    fail(f"workflows: timestamp coverage {w_ts/max(len(wfs),1):.1%} < 90% (details join broken?)")
else:
    ok(f"workflows: timestamps {w_ts}/{len(wfs)}")
# story-project canvas schema (audit round9 B2 P2-1 / fix round9-F6): raw
# canvasContent that parses to {episodes/scripts…} with NO nodes was silently
# flattened to zero-node stubs. The builder now branches on schema (story text
# into the prompt corpus + nodeTypes["story_project"] marker). Tripwire: EVERY
# story-schema canvas in the raw corpus must surface in its catalog record
# with story content (prompt non-empty OR the marker) — the raw count can
# only grow, and the build must keep up or this fails.
story_raw, story_ok = 0, 0
cat_by_id = {r["id"]: r for r in canvases}
for _cg in sorted(glob.glob(f"{AUTH}/canvas_graphs*.jsonl")):
    for l in open(_cg):
        try:
            _r = json.loads(l)
            _g = json.loads(_r.get("canvasContent") or "")
        except Exception:
            continue
        if not (isinstance(_g, dict) and not _g.get("nodes")
                and ("episodes" in _g or "scripts" in _g)):
            continue
        story_raw += 1
        rec = cat_by_id.get(f"canvas_{_r.get('canvasJsonInfoId')}")
        if rec and (rec.get("prompt")
                    or (rec.get("graph") or {}).get("nodeTypes", {}).get("story_project")):
            story_ok += 1
if story_ok < story_raw:
    fail(f"canvases: story-schema canvases {story_ok}/{story_raw} carry story content "
         f"(builder flattened story-project payloads to stubs?)")
elif story_raw == 0:
    ok("canvases: 0 story-project canvases in raw corpus")
else:
    ok(f"canvases: story-schema canvases {story_ok}/{story_raw} carry story content")
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

# R25 (audit r25-a P2-5 + P3-13): pending-auth-backfill carrier — a
# COMMITTED, machine-readable marker of what the auth-dead window owes us.
# Consumers (and the next session's token-recovery check) read this instead
# of parsing log prose. Written on every run (empty lists = nothing owed).
import datetime as _dt
_snap_ages = {}
for _n in ("contests", "skills", "skill_categories", "material_tags"):
    try:
        _s = json.load(open(f"{DATA}/{_n}.json"))
        _fa = _s.get("fetched_at") or ""
        _age = None
        if _fa:
            try:
                _age = (_dt.datetime.now(_dt.timezone.utc)
                        - _dt.datetime.strptime(_fa.replace("Z", "+0000"),
                                                "%Y-%m-%dT%H:%M:%S%z")).days
            except Exception:
                pass
        _snap_ages[_n] = {"age_days": _age, "fetched_at": _fa, "count": _s.get("count")}
        if _age is not None and _age > 7:
            warn(f"newcontent/{_n} snapshot stale: {_age} days old "
                 f"(auth-dead window — resumes on token refresh)")
    except FileNotFoundError:
        pass

_backfill = {
    "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    "token_dead": TOKEN_DEAD,
    "pending_gallery_graphs": sorted(pending_graphs),
    "pending_type2_flow_details": sorted(pending_flows),
    "stale_newcontent_snapshots": {k: v for k, v in _snap_ages.items()
                                   if (v["age_days"] or 0) > 7},
    "note": "ids/entries here backfill automatically once AIX_TOKEN is "
            "refreshed (id-dedup accumulate, proven round-22)",
}
with open(f"{DATA}/pending_auth_backfill.json", "w") as f:
    json.dump(_backfill, f, ensure_ascii=False, indent=1, sort_keys=True)

print(f"result: {len(FAILS)} defects, {len(WARNS)} warnings")
if FAILS:
    print("VALIDATION FAILED — blocking push")
    sys.exit(1)
# audit round 9 P3-3: persist the structural-floor history on GREEN runs only
# (a red run must never ratchet a floor down); high-water mark per unfloored
# type — the accumulate model means counts only go up.
with open(FLOORS_STATE, "w") as f:
    json.dump(_hist_next, f, indent=1, sort_keys=True)
print("VALIDATION PASSED")
sys.exit(0)
