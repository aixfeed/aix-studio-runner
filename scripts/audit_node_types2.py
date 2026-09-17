#!/usr/bin/env python3
"""Extended audit: gallery canvasContent node types + flow payload details
(type distribution, fileKey semantics, media in nodes)."""
import json, collections, glob

AUTH = "/home/z/my-project/download/aixstudio/auth"

def get_canvas_json(rec, keys):
    d = rec.get("data") if isinstance(rec.get("data"), dict) else rec
    for k in keys:
        v = d.get(k) if isinstance(d, dict) else None
        if isinstance(v, str) and v.strip():
            try: return json.loads(v)
            except Exception: pass
        if isinstance(v, dict):
            return v
    return None

# ---------- gallery canvases ----------
gal_types = collections.Counter()
gal_fields = collections.defaultdict(collections.Counter)
gal_param_sample = []
gal_from_sample = []
n_gal = 0
n_gal_nodes = 0
video_nodes = []
for line in open(f"{AUTH}/canvas_graphs.jsonl"):
    line = line.strip()
    if not line: continue
    r = json.loads(line)
    g = get_canvas_json(r, ["canvasContent", "canvasJson"])
    if not isinstance(g, dict): continue
    n_gal += 1
    for nd in (g.get("nodes") or []):
        nt = str(nd.get("type") or "<none>")
        gal_types[nt] += 1
        gal_fields[nt].update(nd.keys())
        d = nd.get("data") or {}
        if isinstance(d, dict):
            for ik in d.keys(): gal_fields[nt].update([f"data.{ik}"])
        n_gal_nodes += 1
        blob = json.dumps(nd, ensure_ascii=False)
        if ".mp4" in blob and len(video_nodes) < 8:
            video_nodes.append(blob[:500])
        if nd.get("param") and len(gal_param_sample) < 4:
            gal_param_sample.append(json.dumps({"type": nt, "param": nd.get("param")}, ensure_ascii=False)[:600])
        if nd.get("fromData") and len(gal_from_sample) < 4:
            gal_from_sample.append(json.dumps({"type": nt, "fromData": nd.get("fromData")}, ensure_ascii=False)[:600])

print(f"gallery graphs: {n_gal}, nodes: {n_gal_nodes}")
print("gallery node types:", dict(gal_types))
for nt, f in gal_fields.items():
    print(f"  --- {nt}: {dict(f.most_common(25))}")

print("\n== gallery param samples ==")
for s in gal_param_sample: print(" ", s, "\n")
print("== gallery fromData samples ==")
for s in gal_from_sample: print(" ", s, "\n")
print("== gallery video-node samples ==")
for s in video_nodes: print(" ", s, "\n")

# ---------- flow payload ----------
flow_type = collections.Counter()
file_keys = []
find_urls = []
thumb_urls = []
for path in sorted(glob.glob(f"{AUTH}/flow_details*.jsonl")):
    for line in open(path):
        line = line.strip()
        if not line: continue
        r = json.loads(line)
        d = r.get("data") if isinstance(r.get("data"), dict) else r
        flow_type[str(d.get("type"))] += 1
        if d.get("fileKey") and len(file_keys) < 8: file_keys.append(d.get("fileKey"))
        if d.get("findUrl") and len(find_urls) < 8: find_urls.append(d.get("findUrl"))
        if d.get("thumbnailUrl") and len(thumb_urls) < 8: thumb_urls.append(d.get("thumbnailUrl"))

print("\n== flow type distribution ==", dict(flow_type))
print("== fileKey samples ==", file_keys)
print("== findUrl samples ==", find_urls[:4])
print("== thumbnailUrl samples ==", thumb_urls[:4])
