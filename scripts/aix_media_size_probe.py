#!/usr/bin/env python3
"""Estimate total media size on oss.aix.studio by sampling HEAD requests.

Samples across URL classes (thumb param URLs, originals, thumbnailfile
pre-gens, videos) and scales up to the 127,818-URL manifest.
"""
import json
import random
import re
import ssl
import urllib.request
from collections import defaultdict

MANIFEST = "/home/z/my-project/download/aixstudio/media_manifest.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}

ctx = ssl.create_default_context()

m = json.load(open(MANIFEST))
urls = [u["url"] for u in m["urls"]]


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


classes = defaultdict(list)
for u in urls:
    classes[classify(u)].append(u)

print("class counts:")
total_est = 0.0
SAMPLE = {"video": 12, "original-image": 25, "resized-thumb": 15, "pre-gen-thumb": 15, "avatar": 8, "audio": 5}
sizes = {}
for cls, lst in sorted(classes.items()):
    random.seed(42)
    sample = random.sample(lst, min(SAMPLE.get(cls, 10), len(lst)))
    got = []
    for u in sample:
        try:
            req = urllib.request.Request(u, method="HEAD", headers=UA)
            with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
                got.append(int(r.headers.get("Content-Length", 0)))
        except Exception as e:
            got.append(0)
    ok = [g for g in got if g > 0]
    avg = sum(ok) / len(ok) if ok else 0
    est = avg * len(lst)
    total_est += est
    sizes[cls] = {"n": len(lst), "sampled": len(ok), "avg_bytes": round(avg), "est_total_gb": round(est / 1e9, 2)}
    print(f"  {cls:16s} n={len(lst):7d}  avg={avg/1024:10.1f}KB  est={est/1e9:6.2f}GB  (fail={len(got)-len(ok)})")

print(f"\nTOTAL estimate: {total_est/1e9:.1f} GB across {len(urls):,} URLs")
json.dump(sizes, open("/home/z/my-project/recon/media_size_estimate.json", "w"), indent=2)
