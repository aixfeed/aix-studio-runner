#!/usr/bin/env python3
"""Deep audit of canvas/flow node structures: ALL node types + ALL fields,
across gallery canvases AND flow graphs. Output: recon/node_types_audit.json
"""
import json, collections, glob, os

AUTH = "/home/z/my-project/download/aixstudio/auth"
OUT = "/home/z/my-project/recon/node_types_audit.json"

def iter_graphs():
    """Yield (source, record_id, canvas_json_obj)."""
    # gallery canvases
    for line in open(f"{AUTH}/canvas_graphs.jsonl"):
        line = line.strip()
        if not line: continue
        try: r = json.loads(line)
        except Exception: continue
        cj = r.get("data") or r.get("canvasJson") or r
        yield ("gallery", str(r.get("id") or r.get("canvasJsonInfoId") or "?"), r)
    # flow details (chunked)
    for path in sorted(glob.glob(f"{AUTH}/flow_details*.jsonl")):
        for line in open(path):
            line = line.strip()
            if not line: continue
            try: r = json.loads(line)
            except Exception: continue
            yield ("flow", str(r.get("id") or "?"), r)

def get_canvas_json(rec):
    d = rec.get("data") if isinstance(rec.get("data"), dict) else rec
    for k in ("canvasJson", "flowJson", "json"):
        v = d.get(k) if isinstance(d, dict) else None
        if isinstance(v, str) and v.strip():
            try: return json.loads(v)
            except Exception: return None
        if isinstance(v, dict):
            return v
    return None

type_fields = collections.defaultdict(collections.Counter)   # (source,ntype) -> fields
type_counts = collections.Counter()
node_examples = {}
top_fields = collections.Counter()
edge_kinds = collections.Counter()
rec_fields_gallery = collections.Counter()
rec_fields_flow = collections.Counter()
other_payload_keys = collections.Counter()  # non-canvasJson payload fields in flow details

n_graphs = 0
n_nodes = 0
n_video = 0
n_image = 0
video_examples = []

for source, rid, rec in iter_graphs():
    if source == "gallery":
        rec_fields_gallery.update(rec.keys())
        d = rec.get("data") if isinstance(rec.get("data"), dict) else rec
    else:
        rec_fields_flow.update(rec.keys())
        d = rec.get("data") if isinstance(rec.get("data"), dict) else rec
        for k in d.keys():
            if k not in ("canvasJson",):
                v = d[k]
                if v not in (None, "", [], {}):
                    other_payload_keys[k] += 1
    g = get_canvas_json(rec)
    if not isinstance(g, dict):
        continue
    n_graphs += 1
    nodes = g.get("nodes") or []
    for nd in nodes:
        nt = nd.get("type") or "<none>"
        type_counts[(source, str(nt))] += 1
        type_fields[(source, str(nt))].update(nd.keys())
        top_fields.update(nd.keys())
        n_nodes += 1
        key = (source, str(nt))
        if key not in node_examples:
            node_examples[key] = {k: (str(v)[:120] if not isinstance(v,(dict,list)) else json.dumps(v,ensure_ascii=False)[:200]) for k,v in nd.items()}
        data = nd.get("data") or {}
        if isinstance(data, dict):
            for ik in data.keys():
                type_fields[(source,str(nt))].update([f"data.{ik}"])
        # scan for video-ish urls
        blob = json.dumps(nd, ensure_ascii=False)
        if ".mp4" in blob or "video" in str(nd.get("type","")).lower():
            n_video += 1
            if len(video_examples) < 12:
                video_examples.append({"source": source, "rid": rid, "ntype": nt,
                    "snippet": blob[:400]})
        if any(x in blob for x in ("oss.aix.studio", ".jpg", ".png", ".webp")):
            n_image += 1
    for e in (g.get("edges") or []):
        edge_kinds[str(e.get("type") or "<none>")] += 1

print(f"graphs parsed: {n_graphs}  nodes: {n_nodes}  nodes-with-image: {n_image}  nodes-with-video: {n_video}")
print("\n== node types (source, type) -> count ==")
for (src, nt), c in sorted(type_counts.items(), key=lambda kv: -kv[1]):
    print(f"{c:>7}  {src:<8} {nt}")

print("\n== gallery record fields ==")
print(dict(rec_fields_gallery))
print("\n== flow record fields (non-null) ==")
print(dict(other_payload_keys))

print("\n== fields per node type ==")
for (src, nt), fields in sorted(type_fields.items(), key=lambda kv: -type_counts[kv[0]]):
    print(f"--- {src} {nt} ({type_counts[(src,nt)]}): {dict(fields.most_common(30))}")

json.dump({
    "type_counts": {f"{s}|{t}": c for (s,t),c in type_counts.items()},
    "type_fields": {f"{s}|{t}": dict(f.most_common(40)) for (s,t),f in type_fields.items()},
    "node_examples": {f"{s}|{t}": e for (s,t),e in node_examples.items()},
    "gallery_rec_fields": dict(rec_fields_gallery),
    "flow_rec_fields": dict(rec_fields_flow),
    "flow_other_payload": dict(other_payload_keys),
    "video_examples": video_examples,
    "edge_kinds": dict(edge_kinds),
    "totals": {"graphs": n_graphs, "nodes": n_nodes, "with_image": n_image, "with_video": n_video},
}, open(OUT, "w"), ensure_ascii=False, indent=1)
print(f"\nsaved {OUT}")
