#!/usr/bin/env python3
"""Netlify blob vault shard orchestration.

- Fetches every shard's `index` blob (PAT reads work; writes don't).
- Merges done-sets across shards (global dedup for the write path).
- Picks the upload target: least-filled shard under target_bytes_per_shard.

CLI: python3 aix_blob_shards.py [--registry path] [--out plan.json]
     [--merged-out merged.json]

Output plan.json: {target: {name, id}, shards: [...], merged_done: N}
merged.json: {"<hash>": bytes} — the global done-set for build.js dedup.
"""
import argparse
import json
import os
import sys
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = os.environ.get("NETLIFY_AUTH_TOKEN", "")
UA = {"User-Agent": "aix-blob-shards"}


def api(path, token=None):
    req = urllib.request.Request(
        f"https://api.netlify.com/api/v1{path}",
        headers={**UA, **({"Authorization": f"Bearer {token or TOKEN}"} if (token or TOKEN) else {})},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"{}")


def fetch_shard_indexes(registry):
    """Returns (merged: {hash: bytes}, per_shard: [{...,bytes,n}])."""
    merged = {}
    per_shard = []
    for i, s in enumerate(registry["shards"]):
        sid = s["id"]
        try:
            idx = api(f"/blobs/{sid}/site:media/index")
            done = idx.get("done", {})
        except Exception as e:
            print(f"  shard {s['name']}: index read failed ({e}) — treating as empty")
            done = {}
        # last writer wins is fine: a hash must live on exactly one shard
        merged.update(done)
        total = sum(v for v in done.values() if isinstance(v, (int, float)))
        per_shard.append({**s, "bytes": total, "n": len(done)})
        print(f"  shard {s['name']}: {len(done):,} blobs, {total / 1e9:.2f} GB")
    return merged, per_shard


def choose_target(per_shard, target_bytes):
    """Least-filled shard under target (never the one already over target
    unless ALL are over — then the absolute least-filled)."""
    under = [s for s in per_shard if s["bytes"] < target_bytes]
    pool = under or per_shard
    return min(pool, key=lambda s: s["bytes"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=os.path.join(REPO, "media_shards.json"))
    ap.add_argument("--out", default="/tmp/shard-plan.json")
    ap.add_argument("--merged-out", default="/tmp/blob-merged-index.json")
    args = ap.parse_args()

    registry = json.load(open(args.registry))
    print(f"registry: {len(registry['shards'])} shards, target "
          f"{registry['target_bytes_per_shard'] / 1e9:.0f} GB/shard")

    merged, per_shard = fetch_shard_indexes(registry)
    target = choose_target(per_shard, registry["target_bytes_per_shard"])

    plan = {
        "target": {"name": target["name"], "id": target["id"]},
        "merged_done": len(merged),
        "total_bytes": sum(s["bytes"] for s in per_shard),
        "shards": [
            {k: v for k, v in s.items() if k in ("name", "id", "bytes", "n", "role")}
            for s in per_shard
        ],
    }
    json.dump(plan, open(args.out, "w"), indent=1)
    # {done: {hash: bytes}} — the shape build.js expects (INDEX_FILE)
    json.dump({"done": merged}, open(args.merged_out, "w"))
    print(f"merged done-set: {len(merged):,} hashes")
    print(f"vault total: {plan['total_bytes'] / 1e9:.2f} GB across {len(per_shard)} shards")
    print(f"UPLOAD TARGET -> {target['name']} ({target['id'][:8]}, "
          f"{target['bytes'] / 1e9:.2f} GB filled)")
    print(f"plan -> {args.out}, merged index -> {args.merged_out}")


if __name__ == "__main__":
    main()
