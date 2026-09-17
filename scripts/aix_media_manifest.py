#!/usr/bin/env python3
"""Phase D-3: media archival manifest.

All canvas/flow graph JSON + catalog records reference media on
oss.aix.studio (fully open, range-request capable). A full mirror is
multi-GB — too big for the git-backed data repo (GitHub 1GB soft limit;
repo already ~350MB with the flow-detail chunks). Decision (2026-09-16):

  MIRROR = deferred; MANIFEST = now.

The manifest is a deduped, counted list of every referenced media URL so a
future mirror (git-lfs / object store / plain wget) is one command away:

  python3 scripts/aix_media_manifest.py            # writes the manifest
  wget -i <(jq -r '.urls[].url' download/aixstudio/media_manifest.json)

Sources scanned: auth/canvas_graphs.jsonl, auth/flow_details*.jsonl (the two
graph corpora — regex over raw lines catches every URL form), plus every
media-bearing field in the parsed catalog (covers/avatars/reference images).
"""
import glob
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

BASE = os.environ.get("AIX_BASE", "/home/z/my-project")
DATA = f"{BASE}/download/aixstudio"
AUTH = f"{DATA}/auth"
CAT = f"{BASE}/src/data/catalog"
OUT = f"{DATA}/media_manifest.json"

URL_RE = re.compile(r"https://oss\.aix\.studio/[A-Za-z0-9\-._~/=%?&]+")


def urls_from_line(line: str):
    return URL_RE.findall(line)


def main():
    urls = Counter()
    # 1. graph corpora (regex over raw JSON lines — catches findUrl/showUrl/
    #    icon/coverFileKey-rendered forms anywhere in the payload)
    corpora = [(f"auth/{os.path.basename(p)}", p) for p in
               sorted(glob.glob(f"{AUTH}/canvas_graphs.jsonl")) +
               sorted(glob.glob(f"{AUTH}/flow_details*.jsonl"))]
    for label, path in corpora:
        n = 0
        with open(path) as f:
            for line in f:
                if not line.strip():
                    continue
                for u in urls_from_line(line):
                    urls[u] += 1
                    n += 1
        print(f"[graphs] {label}: {n} references")

    # 1b. legacy captures (*.jsonl.v1bak, Aug-23 vintage): "site-memory" urls
    # for records the site later deleted — still resolve (permlink probe
    # 2026-09-16: vanished-record urls 40/40 OK), so they belong in the
    # archival reference set (audit wave 1: 652 urls were missing).
    legacy_n = 0
    for p in sorted(glob.glob(f"{DATA}/*.v1bak")):
        with open(p) as f:
            for line in f:
                for u in urls_from_line(line):
                    urls[u] += 1
                    legacy_n += 1
    print(f"[legacy] v1bak captures: {legacy_n} references")

    # 2. parsed catalog (covers, avatars, reference images, media files)
    cat_n = 0
    for p in sorted(glob.glob(f"{CAT}/*.json")):
        if p.endswith("index.json"):
            continue
        with open(p) as f:
            for line in f:
                for u in urls_from_line(line):
                    urls[u] += 1
                    cat_n += 1
    print(f"[catalog] {cat_n} references")

    # bucket by extension for the summary
    ext = Counter()
    for u in urls:
        e = os.path.splitext(u.split("?")[0])[1].lower() or "(none)"
        ext[e] += 1

    manifest = {
        # data-derived stable date (max OSS Last-Modified epoch in key names is
        # impractical — use the catalog's own generatedAt when present)
        "generatedAt": (json.load(open(f"{CAT}/index.json")).get("generatedAt")
                        if os.path.exists(f"{CAT}/index.json") else
                        datetime.now(timezone.utc).isoformat()),
        "totalReferences": sum(urls.values()),
        "uniqueUrls": len(urls),
        "byExtension": dict(ext.most_common()),
        "permlink": {
            "probeDate": "2026-09-16",
            "signedUrls": 0,
            "resolutionAfter3Weeks": "40/40",
            "vanishedRecordUrlsStillLive": "40/40",
            "cacheControl": "public, max-age=31536000",
            "conclusion": "urls are permanent — no bulk cache needed; manifest "
                          "is the archival instrument (see audit/permlink_report.json)",
        },
        "decision": (
            "Full mirror deferred (multi-GB, exceeds git-backed repo budget). "
            "This manifest is the complete reference set; mirror on demand with "
            "wget -i / any bulk fetcher. OSS is public + range-capable."
        ),
        "urls": [{"url": u, "refs": c} for u, c in urls.most_common()],
    }
    with open(OUT, "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"\nmanifest: {OUT}")
    print(f"  unique media urls: {len(urls):,}")
    print(f"  total references:  {sum(urls.values()):,}")
    print(f"  by extension:      {dict(ext.most_common(6))}")


if __name__ == "__main__":
    sys.exit(main())
