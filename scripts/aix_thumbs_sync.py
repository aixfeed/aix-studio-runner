#!/usr/bin/env python3
"""Thumbnail sync engine — OSS originals -> WebP thumbs in a git repo.

Design (2026-09-17, user-directed media caching round):
  * oss.aix.studio supports server-side resize + WebP re-encode:
      <base>?imageView2/2/w/640/q/75/format/webp   (~35KB avg vs 3.3MB orig)
    so "thumb processing" = plain HTTP fetch, no local transcoding.
  * One tier: w/640 q75 WebP. 93,253 unique image bases -> ~3.4GB repo
    (user budget <5GB). Grid cards, canvas node images AND the DetailSheet
    preview all use this tier; full-res goes to Netlify Blobs separately.
  * Key = FNV-1a-64 hex of the PARAM-STRIPPED base URL (identical impl in
    src/lib/media.ts so the browser can map any OSS URL to its thumb).
    Layout: thumbs/<2-char prefix>/<16-hex>.<ext>
  * Filesystem is the completion state (file exists = done); state.json only
    tracks failures + run stats. Idempotent + resumable + budget-bounded.

Exit codes: 0 = everything synced, 3 = budget exhausted (more remain),
1 = fatal error.
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import sys
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}
IMG_RE = re.compile(r"\.(jpg|jpeg|png|webp|jfif)(\?|$)", re.I)
MAGIC = {
    b"RIFF": "webp",       # RIFF....WEBP
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG": "png",
    b"GIF8": "gif",
}


def fnv1a64(s: str) -> str:
    """FNV-1a 64-bit — MUST match src/lib/media.ts thumbHash()."""
    h = 0xCBF29CE484222325
    for b in s.encode("utf-8"):
        h ^= b
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


def sniff(data: bytes) -> str:
    for magic, ext in MAGIC.items():
        if data[:4] == magic or data[:3] == magic[:3] and magic == b"\xff\xd8\xff":
            if magic == b"RIFF" and data[8:12] != b"WEBP":
                continue
            return ext
    return ""


def load_manifest(path: str):
    m = json.load(open(path))
    entries = m["urls"] if isinstance(m, dict) else m
    seen = {}
    for e in entries:
        u = e["url"] if isinstance(e, dict) else e
        base = u.split("?")[0]
        if not IMG_RE.search(base):
            continue
        refs = e.get("refs", 1) if isinstance(e, dict) else 1
        if base not in seen or refs > seen[base]:
            seen[base] = refs
    return seen  # base -> refs


def load_priorities(catalog_dir: str):
    """Cover/gallery URLs from the catalog -> fetch first (P0/P1)."""
    p0, p1 = set(), set()
    try:
        for fn in os.listdir(catalog_dir):
            if not fn.endswith(".json") or fn == "index.json":
                continue
            try:
                recs = json.load(open(os.path.join(catalog_dir, fn)))
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
                        p0.add(u.split("?")[0])
                    for g in media.get("gallery") or []:
                        gu = g.get("url") if isinstance(g, dict) else g
                        if isinstance(gu, str):
                            p1.add(gu.split("?")[0])
                for key in ("cover", "avatar", "url"):
                    u = r.get(key)
                    if isinstance(u, str) and u.startswith("http"):
                        (p0 if key != "avatar" else p1).add(u.split("?")[0])
    except FileNotFoundError:
        pass
    return p0, p1


def fetch_thumb(base: str, width: int, quality: int):
    """Fetch OSS server-side WebP thumb; fall back through widths."""
    for w in (width, 480, 360):
        url = f"{base}?imageView2/2/w/{w}/q/{quality}/format/webp"
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                data = r.read()
            if len(data) < 200:
                continue
            ext = sniff(data)
            if not ext:
                continue
            return data, ext, w
        except Exception:
            continue
    # giant-source fallback (~2% of bases are 20MB+ PNGs where the resize op
    # 400s): the site pre-generates `thumbnailfile<name>` siblings in the
    # same dir — thumb THAT and store under the original's hash.
    sib = re.sub(r"/([^/]+)$", r"/thumbnail\1", base)
    if sib != base:
        for w in (width, 480):
            try:
                req = urllib.request.Request(f"{sib}?imageView2/2/w/{w}/q/{quality}/format/webp", headers=UA)
                with urllib.request.urlopen(req, timeout=40) as r:
                    data = r.read()
                ext = sniff(data) if len(data) > 200 else ""
                if ext:
                    return data, ext, w
            except Exception:
                continue
        try:
            req = urllib.request.Request(sib, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                data = r.read()
            ext = sniff(data)
            if ext and len(data) < 8_000_000:
                return data, ext, 0
        except Exception:
            pass
    # last resort: original bytes (some files reject all params)
    try:
        req = urllib.request.Request(base, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        ext = sniff(data)
        return (data, ext, 0) if ext and len(data) < 8_000_000 else (None, None, None)
    except Exception:
        return None, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="/home/z/my-project/download/aixstudio/media_manifest.json")
    ap.add_argument("--catalog", default="/home/z/my-project/src/data/catalog")
    ap.add_argument("--out", required=True, help="thumbs repo checkout dir")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--quality", type=int, default=75)
    ap.add_argument("--max-files", type=int, default=0, help="0 = unlimited")
    ap.add_argument("--max-minutes", type=float, default=0, help="0 = unlimited")
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0, help="test mode: only first N candidates")
    args = ap.parse_args()

    thumbs_dir = os.path.join(args.out, "thumbs")
    os.makedirs(thumbs_dir, exist_ok=True)
    state_path = os.path.join(args.out, "state.json")
    state = {"runs": []}
    if os.path.exists(state_path):
        try:
            state = json.load(open(state_path))
        except Exception:
            pass
    permanent_skips = set(state.get("permanent_skips", []))
    fail_counts = state.get("fail_counts", {})

    bases = load_manifest(args.manifest)
    p0, p1 = load_priorities(args.catalog)

    def prio(item):
        base, refs = item
        if base in p0:
            return (0, -refs)
        if base in p1:
            return (1, -refs)
        return (2, -refs)

    candidates = [
        (b, refs) for b, refs in bases.items()
        if b not in permanent_skips
        and not os.path.exists(os.path.join(thumbs_dir, fnv1a64(b)[:2], fnv1a64(b) + ".webp"))
        and not os.path.exists(os.path.join(thumbs_dir, fnv1a64(b)[:2], fnv1a64(b) + ".jpg"))
        and not os.path.exists(os.path.join(thumbs_dir, fnv1a64(b)[:2], fnv1a64(b) + ".png"))
    ]
    candidates.sort(key=prio)
    if args.limit:
        candidates = candidates[: args.limit]

    total = len(bases)
    done_before = total - len(candidates) - len(permanent_skips)
    print(f"manifest bases: {total:,} | already synced: {done_before:,} | "
          f"permanent skips: {len(permanent_skips):,} | todo now: {len(candidates):,}", flush=True)

    t0 = time.time()
    written = failed = 0
    bytes_in = 0
    new_fails = {}
    stop = False

    def work(item):
        base, _refs = item
        data, ext, w = fetch_thumb(base, args.width, args.quality)
        return base, data, ext, w

    with cf.ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futures = {ex.submit(work, it): it for it in candidates}
        for fut in cf.as_completed(futures):
            if stop:
                fut.cancel()
                continue
            base, data, ext, w = fut.result()
            if data is None:
                failed += 1
                fail_counts[base] = fail_counts.get(base, 0) + 1
                new_fails[base] = "fetch/decode failed"
                if fail_counts[base] >= 3:
                    permanent_skips.add(base)
                continue
            h = fnv1a64(base)
            d = os.path.join(thumbs_dir, h[:2])
            os.makedirs(d, exist_ok=True)
            tmp = os.path.join(d, f".{h}.tmp")
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, os.path.join(d, f"{h}.{ext}"))
            written += 1
            bytes_in += len(data)
            if written % 500 == 0:
                el = time.time() - t0
                print(f"  {written} written | {bytes_in/1e6:.0f}MB | {el:.0f}s | {written/el:.1f} files/s", flush=True)
            if args.max_files and written >= args.max_files:
                stop = True
            if args.max_minutes and (time.time() - t0) / 60 > args.max_minutes:
                stop = True

    # clean one-time fail entries that succeeded are handled by file-existence
    state["fail_counts"] = {k: v for k, v in fail_counts.items() if k in new_fails}
    state["permanent_skips"] = sorted(permanent_skips)
    state["runs"].append({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "written": written,
        "failed": failed,
        "bytes_in": bytes_in,
        "elapsed_s": round(time.time() - t0, 1),
        "remaining": len(candidates) - written - failed,
    })
    state["runs"] = state["runs"][-30:]
    tmp = state_path + ".tmp"
    json.dump(state, open(tmp, "w"), indent=1)
    os.replace(tmp, state_path)

    remaining = len(candidates) - written - failed
    print(f"DONE written={written} failed={failed} bytes={bytes_in/1e6:.0f}MB "
          f"elapsed={time.time()-t0:.0f}s remaining={remaining}", flush=True)
    sys.exit(3 if remaining > 0 else 0)


if __name__ == "__main__":
    main()
