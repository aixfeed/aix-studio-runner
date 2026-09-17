#!/usr/bin/env python3
"""Thumb sync for CI — pushes new WebP thumbs to the GH thumbs repo via the
Git data API (no checkout of the 2.6GB repo needed).

Why not git: cloning wesisad5/aix-media-thumbs costs ~2.6GB + minutes every
run. The Git data API (blobs -> tree -> commit -> ref) uploads only the NEW
files in one commit: ~2 requests per file + 4 fixed calls.

State: index.json in the thumbs repo {hashes: {hash: bytes}} is the done-set
(local-mode syncs use the filesystem, so this script regenerates/merges it).
Failures retry next run; 3-strike permanent skips mirror state.json.

  GH_TOKEN=... python3 aix_thumbs_push_ghapi.py --manifest <path> [--catalog <dir>] [--max-items 800]

Exit codes: 0 = nothing to do / complete, 3 = more remain (budget hit), 1 = error.
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aix_thumbs_sync import fnv1a64, load_manifest, load_priorities, fetch_thumb  # noqa: E402

REPO = "wesisad5/aix-media-thumbs"
BRANCH = "main"
RAW = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
API = f"https://api.github.com/repos/{REPO}"
TOKEN = os.environ.get("GH_TOKEN", "")
UA = {"User-Agent": "aix-thumbs-sync", "Accept": "application/vnd.github+json", "Authorization": f"Bearer {TOKEN}"}


def gh(method, path, body=None, raw=False, retries=3):
    url = path if path.startswith("http") else API + path
    data = None
    headers = dict(UA)
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=60) as r:
                payload = r.read()
                if raw:
                    return r.status, payload
                return r.status, (json.loads(payload) if payload else {})
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and attempt < retries - 1:  # rate limit / transient
                time.sleep(10 * (attempt + 1))
                continue
            raise
    raise RuntimeError("unreachable")


def fetch_raw(path, default=b""):
    try:
        # repo is PRIVATE: raw.githubusercontent.com needs the PAT header
        req = urllib.request.Request(
            f"{RAW}/{path}",
            headers={"User-Agent": "aix-thumbs-sync", "Authorization": f"Bearer {TOKEN}"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except Exception:
        return default


def push_index(idx, message):
    """Persist index.json alone (failure bookkeeping when nothing to upload)."""
    _, ref = gh("GET", f"/git/ref/heads/{BRANCH}")
    head = ref["object"]["sha"]
    _, commit = gh("GET", f"/git/commits/{head}")
    base_tree = commit["tree"]["sha"]
    idx_blob = json.dumps(idx).encode()
    _, blob = gh("POST", "/git/blobs", {"content": base64.b64encode(idx_blob).decode(), "encoding": "base64"})
    _, new_tree = gh("POST", "/git/trees", {"base_tree": base_tree, "tree": [{"path": "index.json", "mode": "100644", "type": "blob", "sha": blob["sha"]}]})
    _, new_commit = gh("POST", "/git/commits", {"message": message, "tree": new_tree["sha"], "parents": [head]})
    gh("PATCH", f"/git/refs/heads/{BRANCH}", {"sha": new_commit["sha"]})
    return new_commit["sha"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--catalog", default="")
    ap.add_argument("--max-items", type=int, default=800)
    args = ap.parse_args()
    if not TOKEN:
        print("GH_TOKEN env required", file=sys.stderr)
        sys.exit(1)

    bases = load_manifest(args.manifest)
    p0, p1 = load_priorities(args.catalog) if args.catalog else (set(), set())

    idx = {"v": 2, "hashes": {}, "fail_counts": {}, "permanent_skips": []}
    raw = fetch_raw("index.json")
    if raw:
        try:
            idx = json.loads(raw)
            idx.setdefault("fail_counts", {})
            idx.setdefault("permanent_skips", [])
        except Exception:
            pass
    done = set(idx.get("hashes", {}).keys())
    fails = idx.get("fail_counts", {})
    skips = set(idx.get("permanent_skips", []))

    todo = []
    for b, refs in bases.items():
        h = fnv1a64(b)
        if h in done or h in skips:
            continue
        todo.append((b, refs, 1 if b in p0 else 2 if b in p1 else 3, h))
    todo.sort(key=lambda x: (x[2], -x[1]))
    print(f"thumbs: {len(bases):,} bases | done: {len(done):,} | skips: {len(skips):,} | todo: {len(todo):,}")
    if not todo:
        sys.exit(0)

    todo = todo[: args.max_items]

    # fetch + prepare uploads
    uploads = []
    failed = {}
    for i, (b, _refs, _p, h) in enumerate(todo):
        data, ext, w = fetch_thumb(b, 640, 75)
        if data is None:
            failed[h] = b
            continue
        uploads.append((f"thumbs/{h[:2]}/{h}.{ext}", data, h, len(data)))
        if (i + 1) % 100 == 0:
            print(f"  fetched {i + 1}/{len(todo)} ({len(uploads)} ok)", flush=True)
    print(f"fetched: {len(uploads)} ok, {len(failed)} failed")

    # 3-strike permanent skip (self-contained in index.json — state.json is
    # local-mode only and raw.githubusercontent lags up to 5 min)
    for h, b in failed.items():
        fails[h] = fails.get(h, 0) + 1
        if fails[h] >= 3:
            skips.add(h)
    idx["fail_counts"] = {k: v for k, v in fails.items() if k not in skips}
    idx["permanent_skips"] = sorted(skips)

    if not uploads:
        # still persist the failure/skip bookkeeping
        if failed:
            push_index(idx, message=f"thumbs: {len(failed)} failed (bookkeeping)")
        sys.exit(3)

    # git data API: ref -> commit -> base tree -> blobs -> tree -> commit -> ref
    _, ref = gh("GET", f"/git/ref/heads/{BRANCH}")
    head = ref["object"]["sha"]
    _, commit = gh("GET", f"/git/commits/{head}")
    base_tree = commit["tree"]["sha"]

    tree = []
    total_bytes = 0
    for path, data, h, n in uploads:
        _, blob = gh("POST", "/git/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"})
        tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        idx["hashes"][h] = n
        total_bytes += n

    # fold failures with 3-strike semantics into index.json
    idx_blob = json.dumps(idx).encode()
    _, blob = gh("POST", "/git/blobs", {"content": base64.b64encode(idx_blob).decode(), "encoding": "base64"})
    tree.append({"path": "index.json", "mode": "100644", "type": "blob", "sha": blob["sha"]})

    _, new_tree = gh("POST", "/git/trees", {"base_tree": base_tree, "tree": tree})
    _, new_commit = gh("POST", "/git/commits", {
        "message": f"thumbs: sync {len(uploads)} files ({total_bytes / 1e6:.0f}MB) via API",
        "tree": new_tree["sha"],
        "parents": [head],
    })
    gh("PATCH", f"/git/refs/heads/{BRANCH}", {"sha": new_commit["sha"]})
    print(f"PUSHED {len(uploads)} thumbs + index.json -> commit {new_commit['sha'][:10]}")

    remaining = len(bases) - len(done) - len(uploads)
    print(f"remaining after this run: ~{remaining:,}")
    sys.exit(3 if remaining > 0 else 0)


if __name__ == "__main__":
    main()
