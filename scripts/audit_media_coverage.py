#!/usr/bin/env python3
"""Media coverage per node type: targetInfoList presence, fileType mix,
poster availability, and where upload-node media lives."""
import json, collections, glob

AUTH = "/home/z/my-project/download/aixstudio/auth"

def get_canvas_json(rec, keys):
    d = rec.get("data") if isinstance(rec.get("data"), dict) else rec
    for k in keys:
        v = d.get(k) if isinstance(d, dict) else None
        if isinstance(v, str) and v.strip():
            try: return json.loads(v)
            except Exception: pass
        if isinstance(v, dict): return v
    return None

cov = collections.defaultdict(lambda: collections.Counter())
filetypes = collections.Counter()
multi_media = collections.Counter()
video_with_poster = collections.Counter()
img_with_video = collections.Counter()  # image-type nodes carrying a video entry
empty_target_examples = []

def scan(source, g):
    for nd in (g.get("nodes") or []):
        nt = str(nd.get("type") or "?")
        cov[source][f"{nt}.total"] += 1
        tgt = nd.get("target") or {}
        til = tgt.get("targetInfoList") if isinstance(tgt, dict) else None
        if isinstance(til, list) and til:
            cov[source][f"{nt}.hasTIL"] += 1
            has_url = any(isinstance(e.get("findUrl"), str) and e["findUrl"].startswith("http") for e in til)
            has_thumb = any(isinstance(e.get("thumbnailUrl"), str) and e["thumbnailUrl"].startswith("http") for e in til)
            if has_url: cov[source][f"{nt}.hasUrl"] += 1
            if has_thumb: cov[source][f"{nt}.hasThumb"] += 1
            if len(til) > 1: multi_media[f"{source}|{nt}|{len(til)}"] += 1
            for e in til:
                filetypes[f"{source}|{nt}|ft{e.get('fileType')}"] += 1
            if nt == "video":
                video_with_poster["total"] += 1
                if has_thumb: video_with_poster["withPoster"] += 1
            # image/upload nodes carrying video entries (hover-video candidates)
            if nt in ("image", "upload"):
                if any((e.get("findUrl") or "").lower().split("?")[0].endswith(".mp4") for e in til):
                    img_with_video[f"{source}|{nt}"] += 1
        else:
            cov[source][f"{nt}.noTIL"] += 1
            if len(empty_target_examples) < 6 and nt in ("image", "video"):
                empty_target_examples.append(json.dumps({k: v for k, v in nd.items() if k in ("id","type","status","data")}, ensure_ascii=False)[:300])

for line in open(f"{AUTH}/canvas_graphs.jsonl"):
    line = line.strip()
    if not line: continue
    r = json.loads(line)
    g = get_canvas_json(r, ["canvasContent", "canvasJson"])
    if isinstance(g, dict): scan("gallery", g)

for path in sorted(glob.glob(f"{AUTH}/flow_details*.jsonl")):
    for line in open(path):
        line = line.strip()
        if not line: continue
        r = json.loads(line)
        g = get_canvas_json(r, ["canvasJson"])
        if isinstance(g, dict): scan("flow", g)

for src in ("gallery", "flow"):
    print(f"===== {src} =====")
    types = sorted({k.rsplit(".", 1)[0] for k in cov[src]})
    for t in types:
        c = cov[src]
        tot = c.get(f"{t}.total", 0)
        print(f"  {t:<16} total={tot:<6} hasTIL={c.get(f'{t}.hasTIL',0):<6} hasUrl={c.get(f'{t}.hasUrl',0):<6} hasThumb={c.get(f'{t}.hasThumb',0):<6} noTIL={c.get(f'{t}.noTIL',0)}")

print("\nfileType distribution (top 20):")
for k, v in filetypes.most_common(20): print(f"  {v:>7}  {k}")
print("\nmulti-media nodes (TIL > 1):", dict(multi_media))
print("\nvideo nodes with poster:", dict(video_with_poster))
print("image/upload nodes carrying mp4:", dict(img_with_video))
print("\nempty-target examples:")
for e in empty_target_examples: print(" ", e)
