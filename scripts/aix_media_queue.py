#!/usr/bin/env python3
"""Priority queue for the Netlify blob media sync.

Order (user directive 2026-09-17: images are the priority — videos are
gallery-only and the CDN has NO working low-res transcode (avthumb 400,
video/resize + videoMogr2 are byte-identical no-ops), so videos are
EXCLUDED by default; opt back in with --include-videos):
  P0 covers   — every record's DetailSheet hero (originals, not thumbs)
  P1 gallery  — character multi-view / scene hero+grid originals
                (the ~90GB library-image class — the biggest block)
  P2 canvas   — node media originals + everything else image
  P3 audio    — 0.4GB total, cheap tail
  P4 avatars  — thumbs cover the UI need; full-res avatar adds little
  (P9 videos  — only with --include-videos)

Output: JSON array [{url, hash, kind, ext, prio}] — consumed by the
netlify-vault build.js (QUEUE_FILE) which slices + dedups vs the merged
shard index.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aix_thumbs_sync import fnv1a64  # noqa: E402

VIDEO_RE = re.compile(r"\.(mp4|mov)(\?|$)", re.I)
AUDIO_RE = re.compile(r"\.(mp3|flac|wav|m4a)(\?|$)", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="/home/z/my-project/download/aixstudio/media_manifest.json")
    ap.add_argument("--catalog", default="/home/z/my-project/src/data/catalog")
    ap.add_argument("--out", default="/tmp/media-queue.json")
    ap.add_argument("--include-videos", action="store_true",
                    help="queue videos too (P9, after everything else)")
    args = ap.parse_args()

    m = json.load(open(args.manifest))
    entries = m["urls"] if isinstance(m, dict) else m

    # base -> refs (dedup param variants, keep max refs)
    bases = {}
    for e in entries:
        u = e["url"] if isinstance(e, dict) else e
        base = u.split("?")[0]
        refs = e.get("refs", 1) if isinstance(e, dict) else 1
        if base not in bases or refs > bases[base]:
            bases[base] = refs

    covers, gallery = set(), set()
    for fn in os.listdir(args.catalog):
        if not fn.endswith(".json") or fn == "index.json":
            continue
        try:
            recs = json.load(open(os.path.join(args.catalog, fn)))
        except Exception:
            continue
        if isinstance(recs, dict):
            recs = list(recs.values())
        for r in recs:
            if not isinstance(r, dict):
                continue
            media = r.get("media")
            if isinstance(media, dict):
                u = media.get("url")
                if isinstance(u, str):
                    covers.add(u.split("?")[0])
                for g in media.get("gallery") or []:
                    gu = g.get("url") if isinstance(g, dict) else g
                    if isinstance(gu, str):
                        gallery.add(gu.split("?")[0])

    out = []
    for base, refs in bases.items():
        if VIDEO_RE.search(base):
            if not args.include_videos:
                continue
            prio, kind = 9, "video"
        elif AUDIO_RE.search(base):
            prio, kind = 3, "audio"
        elif base in covers:
            prio, kind = 0, "image"
        elif base in gallery:
            prio, kind = 1, "image"
        elif "/user-avatar/" in base:
            prio, kind = 4, "image"
        else:
            prio, kind = 2, "image"
        out.append({
            "url": base,
            "hash": fnv1a64(base),
            "kind": kind,
            "ext": base.rsplit(".", 1)[-1].lower() if "." in base.split("/")[-1] else "bin",
            "prio": prio,
            "refs": refs,
        })
    out.sort(key=lambda x: (x["prio"], -x["refs"]))

    with open(args.out, "w") as f:
        json.dump(out, f)
    from collections import Counter
    c = Counter((x["prio"], x["kind"]) for x in out)
    print(f"queue: {len(out):,} items -> {args.out}")
    for (p, k), n in sorted(c.items()):
        print(f"  P{p} {k:6s}: {n:,}")
    vids = [x for x in out if x["kind"] == "video"]
    if vids:
        print(f"  (videos queued: {len(vids):,} — CDN has no low-res transcode)")


if __name__ == "__main__":
    main()
