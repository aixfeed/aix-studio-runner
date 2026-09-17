#!/usr/bin/env python3
"""Attribute the 127,898 manifest media URLs to catalog sources.

Answers: of the ~225GB, how much is canvas-generation originals vs
library (scene/char/prop) images vs avatars vs videos — i.e. WHERE the
bytes actually live. Joins manifest URLs against src/data/catalog/*.json
text, then weights by per-class measured avg sizes.
"""
import json
import re
from collections import defaultdict

CAT_DIR = "/home/z/my-project/src/data/catalog"
MANIFEST = "/home/z/my-project/download/aixstudio/media_manifest.json"

# per-class measured avg bytes (recon/media_size_estimate.json, sampled)
AVG = {
    "original-image": 3344780,
    "pre-gen-thumb": 35209,
    "resized-thumb": 20545,
    "avatar": 1720694,
    "video": 4301851,
    "audio": 8343097,
}

def classify(u: str) -> str:
    if re.search(r"\.(mp4|mov)(\?|$)", u, re.I):
        return "video"
    if "imageView2" in u or "imageMogr2" in u or "videoMogr2" in u or "vframe" in u:
        return "resized-thumb"
    if "thumbnailfile" in u:
        return "pre-gen-thumb"
    if re.search(r"\.(mp3|flac|wav|m4a)(\?|$)", u, re.I):
        return "audio"
    if re.search(r"user-avatar", u):
        return "avatar"
    return "original-image"

m = json.load(open(MANIFEST))
urls = [u["url"] for u in m["urls"]]
url_set = set(urls)
cls_of = {u: classify(u) for u in urls}

# which sources reference each URL (regex over raw JSON text):
# catalog collections + raw JSONL (graphs, flow details, lib details)
URL_RE = re.compile(r"https?://[^\"\\\s]+")
src_of = defaultdict(set)
SOURCES = [
    ("graph-jsonl", "/home/z/my-project/download/aixstudio/auth/canvas_graphs.jsonl"),
    ("flow-details", "/home/z/my-project/download/aixstudio/auth/flow_details.jsonl"),
    ("catalog", None),  # handled below via CAT_DIR
]
import os
for col in ["canvases", "workflows", "generations", "assets", "showcases",
            "characters", "scenes", "props", "creators", "style_materials",
            "style_prompts"]:
    p = os.path.join(CAT_DIR, f"{col}.json")
    if not os.path.exists(p):
        continue
    text = open(p, encoding="utf-8").read()
    for mo in URL_RE.finditer(text):
        u = mo.group(0)
        if u in url_set:
            src_of[u].add("catalog:" + col)

# raw jsonl sources (attribution only; catalog files already carry most)
RAW = [
    ("raw:canvas-graphs", "/home/z/my-project/download/aixstudio/auth/canvas_graphs.jsonl"),
    ("raw:flow-details", "/home/z/my-project/download/aixstudio/auth/flow_details.jsonl"),
    ("raw:lib-details", "/home/z/my-project/download/aixstudio/auth/character_details.jsonl"),
    ("raw:lib-details", "/home/z/my-project/download/aixstudio/auth/scene_details.jsonl"),
    ("raw:lib-details", "/home/z/my-project/download/aixstudio/auth/prop_details.jsonl"),
]
for tag, p in RAW:
    if not os.path.exists(p):
        continue
    with open(p, encoding="utf-8") as f:
        for line in f:
            for mo in URL_RE.finditer(line):
                u = mo.group(0)
                if u in url_set:
                    src_of[u].add(tag)

# bucket source-sets into human-readable groups
def source_group(s: set) -> str:
    if not s:
        return "unreferenced-anywhere"
    tags = {t.split(":")[-1] if t.startswith("catalog:") else t for t in s}
    if tags <= {"canvas-graphs", "flow-details", "canvases", "workflows"}:
        return "canvas/workflow graphs"
    if "canvas-graphs" in tags or "flow-details" in tags:
        return "canvas/workflow graphs (+more)"
    libs = tags & {"characters", "scenes", "props", "lib-details"}
    if libs and tags <= libs | {"canvas-graphs", "flow-details", "canvases", "workflows"}:
        return "library char/scene/prop"
    if libs:
        return "library (+graph overlap)"
    if "creators" in tags and len(tags) == 1:
        return "creator avatars"
    if "creators" in tags:
        return "creator avatars (+more)"
    return "other(" + ",".join(sorted(tags)) + ")"

# count + estimate bytes; per-(class,group) HEAD sampling for the big groups
import random, ssl, urllib.request
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}
ctx = ssl.create_default_context()

groups = defaultdict(list)
for u in urls:
    groups[(cls_of[u], source_group(src_of[u]))].append(u)

def head_size(us):
    got = []
    for u in us:
        try:
            req = urllib.request.Request(u, method="HEAD", headers=UA)
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                got.append(int(r.headers.get("Content-Length", 0)))
        except Exception:
            pass
    ok = [g for g in got if g > 0]
    return (sum(ok) / len(ok), len(ok)) if ok else (None, 0)

print(f"{'class':<16} {'source group':<40} {'n':>7} {'smp':>4} {'avg MB':>8} {'est GB':>9}")
tot = 0.0
measured_log = {}
for (c, g), lst in sorted(groups.items(), key=lambda kv: -len(kv[1]) * AVG[kv[0][0]]):
    n = len(lst)
    if n >= 8 and AVG[c] > 100000:  # sample the byte-heavy groups only
        random.seed(7)
        avg, nok = head_size(random.sample(lst, min(12, n)))
    else:
        avg, nok = None, 0
    eff = avg if avg else AVG[c]
    est = eff * n
    tot += est
    measured_log[f"{c}|{g}"] = {"n": n, "sampled": nok,
                                 "avg_bytes": round(eff), "est_gb": round(est / 1e9, 2)}
    if est / 1e9 >= 0.05:
        print(f"{c:<16} {g:<40} {n:>7} {nok:>4} {eff/1e6:>8.2f} {est/1e9:>9.2f}")

print(f"{'TOTAL':<68} {tot/1e9:>9.2f}")
json.dump(measured_log, open("/home/z/my-project/recon/media_composition.json", "w"),
          indent=1, ensure_ascii=False)

print("\nURLs referenced by >1 source: ",
      sum(1 for u in urls if len(src_of[u]) > 1))
print("URLs not found anywhere:      ",
      sum(1 for u in urls if not src_of[u]))
