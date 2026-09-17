#!/usr/bin/env python3
"""Permlink probe — decide whether/what to cache.

Questions answered:
 1. Temporal stability: do URLs captured Aug 23 still resolve (>=3 weeks old)?
 2. Deletion survival: URLs whose parent record vanished from the fresh
    listing — do they still resolve? (If yes, OSS outlives site deletion.)
 3. Header hygiene: Cache-Control / ETag / Last-Modified / content-length.
 4. URL signing: any signed/expiring params anywhere?

Usage: python3 scripts/audit_permlink.py [--sample N]
Writes: download/aixstudio/permlink_report.json
"""
import json, re, subprocess, sys, random, hashlib, os
from collections import Counter, defaultdict
from urllib.parse import urlparse, parse_qs

BASE = "/home/z/my-project/download/aixstudio"
OUT = f"{BASE}/permlink_report.json"
SAMPLE = int(sys.argv[sys.argv.index("--sample") + 1]) if "--sample" in sys.argv else 60

URL_RE = re.compile(r'https?://(?:oss\.aix\.studio|[\w.-]*aix\.studio)[^\s"\',\\\]\}]+')

def urls_from_file(path):
    """Stream a jsonl/json file, return set of urls found anywhere in text."""
    urls = set()
    try:
        with open(path, errors="replace") as f:
            for line in f:
                for m in URL_RE.findall(line):
                    urls.add(m.rstrip(').,'))
    except FileNotFoundError:
        pass
    return urls

def head(url, timeout=12):
    """HEAD via curl; fall back to ranged GET if HEAD denied."""
    for args in (["-I"], ["-r", "0-0"]):
        try:
            r = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w",
                 "%{http_code} %{content_type} %{size_download}",
                 "--max-time", str(timeout), *args, url],
                capture_output=True, text=True, timeout=timeout + 5)
            code = r.stdout.split()[0] if r.stdout.strip() else "000"
            if code not in ("403", "405") or args == ["-r", "0-0"]:
                return code
        except subprocess.TimeoutExpired:
            return "timeout"
    return "000"

def headers_of(url, timeout=12):
    r = subprocess.run(["curl", "-sI", "--max-time", str(timeout), url],
                       capture_output=True, text=True, timeout=timeout + 5)
    keep = {}
    for line in r.stdout.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            if k.strip().lower() in ("cache-control", "etag", "last-modified",
                                     "content-length", "x-oss-", "expires",
                                     "access-control-allow-origin"):
                keep[k.strip()] = v.strip()
    return keep

def bucket(url):
    p = urlparse(url)
    seg = p.path.split("/")
    return f"{p.netloc}/{seg[1] if len(seg) > 1 else ''}/{seg[2] if len(seg) > 2 else ''}"

def main():
    # ---- gather URL sets: old vs fresh ----
    old_files = ["gallery.jsonl.v1bak", "assets.jsonl.v1bak", "flows.jsonl.v1bak",
                 "auth/character_lib.jsonl", "auth/prop_lib.jsonl",
                 "auth/scene_lib.jsonl", "auth/singles.json"]
    fresh_files = ["gallery.jsonl", "assets.jsonl", "flows.jsonl",
                   "flows.deduped.json", "assets.deduped.json",
                   "gallery.deduped.json", "user_profiles.json",
                   "auth/canvas_graphs.jsonl", "auth/flow_details.jsonl",
                   "auth/flow_details_2.jsonl", "auth/flow_details_3.jsonl",
                   "auth/flow_details_4.jsonl", "auth/flow_details_5.jsonl",
                   "media_manifest.json"]

    old_urls, fresh_urls = set(), set()
    for f in old_files:
        old_urls |= urls_from_file(f"{BASE}/{f}")
    for f in fresh_files:
        fresh_urls |= urls_from_file(f"{BASE}/{f}")

    # exclude page/api urls (WAF'd, not media) — we only care about oss media
    def is_media(u):
        return "oss.aix.studio" in u and not u.rstrip("/").endswith((".html",))
    old_media = {u for u in old_urls if is_media(u)}
    fresh_media = {u for u in fresh_urls if is_media(u)}

    common = old_media & fresh_media
    vanished = old_media - fresh_media   # captured before, absent now
    new_only = fresh_media - old_media

    # ---- signing check ----
    signed = [u for u in (list(old_media) + list(fresh_media))[:5000]
              if any(k in parse_qs(urlparse(u).query) for k in
                     ("Signature", "signature", "Expires", "expires", "X-Amz", "OSSAccessKeyId"))]
    resize_params = sum(1 for u in list(fresh_media)[:5000] if "imageView2" in u or "imageMogr2" in u)

    # ---- temporal probe: sample from each cohort ----
    random.seed(42)
    def probe(cohort, n):
        sample = random.sample(sorted(cohort), min(n, len(cohort)))
        results = []
        for u in sample:
            code = head(u)
            results.append({"url": u, "status": code})
        return results

    res_common = probe(common, SAMPLE)
    res_vanished = probe(vanished, SAMPLE)
    res_new = probe(new_only, SAMPLE)

    def summarize(results):
        c = Counter(r["status"] for r in results)
        return {"sampled": len(results),
                "status_counts": dict(c),
                "ok_rate": round(c.get("200", 0) / max(len(results), 1), 3)}

    # header profile for a few OK urls
    hdr_samples = {}
    for r in [x for x in res_common + res_new if x["status"] == "200"][:6]:
        hdr_samples[r["url"]] = headers_of(r["url"])

    report = {
        "generated": "2026-09-16",
        "url_cohorts": {
            "old_media_total": len(old_media),
            "fresh_media_total": len(fresh_media),
            "common": len(common),
            "vanished_old_only": len(vanished),
            "new_only": len(new_only),
        },
        "signing": {
            "signed_urls_found": len(signed),
            "signed_examples": signed[:5],
            "with_resize_params": resize_params,
        },
        "temporal_probe": {
            "common_still_resolves": summarize(res_common),
            "vanished_still_resolves": summarize(res_vanished),
            "new_resolves": summarize(res_new),
        },
        "bucket_distribution": dict(Counter(bucket(u) for u in fresh_media).most_common(12)),
        "header_profiles": hdr_samples,
        "vanished_examples": [r for r in res_vanished][:10],
    }
    with open(OUT, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(json.dumps(report, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
