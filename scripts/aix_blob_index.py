#!/usr/bin/env python3
"""Fetch (or init) the media vault blob index to a local file.

The `index` blob in store site:media on the Netlify site holds
{done: {hash: bytes}, failures: {}} — the resume state for the blob sync.

  python3 aix_blob_index.py fetch --out /tmp/blob-index.json
  python3 aix_blob_index.py stats
"""
import argparse
import json
import os
import sys
import urllib.request

PAT = os.environ.get("NETLIFY_AUTH_TOKEN", "")
SITE = os.environ.get("NETLIFY_SITE_ID", "9f93aa14-e45c-4d2e-a994-3da7a16e117d")
API = f"https://api.netlify.com/api/v1/blobs/{SITE}/site:media/index"


def fetch_index():
    req = urllib.request.Request(API, headers={"Authorization": f"Bearer {PAT}"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "stats"])
    ap.add_argument("--out", default="/tmp/blob-index.json")
    args = ap.parse_args()
    try:
        idx = fetch_index()
    except Exception as e:
        if "404" in str(e) or "HTTP Error 404" in str(e):
            idx = {"done": {}, "failures": {}}
        else:
            print(f"fetch failed: {e}", file=sys.stderr)
            sys.exit(1)
    if args.cmd == "fetch":
        json.dump(idx, open(args.out, "w"))
        print(f"index -> {args.out}: {len(idx.get('done', {})):,} done, {len(idx.get('failures', {}))} failures")
    else:
        done = idx.get("done", {})
        total_bytes = sum(done.values())
        print(f"done: {len(done):,} blobs, {total_bytes / 1e9:.2f} GB")
        print(f"failures tracked: {len(idx.get('failures', {}))}")


if __name__ == "__main__":
    main()
