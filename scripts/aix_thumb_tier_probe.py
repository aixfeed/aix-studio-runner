#!/usr/bin/env python3
"""Pick the thumb tier: sample real manifest URLs, measure w/480 vs w/640 webp."""
import json
import random
import re
import urllib.request

MANIFEST = "/home/z/my-project/download/aixstudio/media_manifest.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

m = json.load(open(MANIFEST))
urls = [u["url"] for u in m["urls"]]

# qlogo / non-OSS presence check
non_oss = [u for u in urls if "oss.aix.studio" not in u]
print(f"non-OSS URLs in manifest: {len(non_oss)}")
for u in non_oss[:5]:
    print("  ", u[:100])

imgs = [u for u in urls if re.search(r"\.(jpg|jpeg|png|webp|jfif)(\?|$)", u, re.I)]
bases = []
for u in imgs:
    base = u.split("?")[0]
    if base not in bases:
        bases.append(base)
print(f"image URLs: {len(imgs)}, unique bases: {len(bases)}")

random.seed(7)
sample = random.sample(bases, 30)
total480 = total640 = 0
ok = 0
for base in sample:
    try:
        s480 = s640 = 0
        for w, acc in ((480, "480"), (640, "640")):
            req = urllib.request.Request(f"{base}?imageView2/2/w/{w}/q/75/format/webp", headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                b = r.read()
                if w == 480:
                    s480 = len(b)
                else:
                    s640 = len(b)
        total480 += s480
        total640 += s640
        ok += 1
    except Exception as e:
        print("  fail:", base[-40:], e)
print(f"\nsampled {ok}/30 OK")
print(f"w/480 avg: {total480/ok/1024:.1f}KB -> est repo: {total480/ok*len(bases)/1e9:.2f}GB")
print(f"w/640 avg: {total640/ok/1024:.1f}KB -> est repo: {total640/ok*len(bases)/1e9:.2f}GB")
