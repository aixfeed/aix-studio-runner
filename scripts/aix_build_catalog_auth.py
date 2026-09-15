#!/usr/bin/env python3
"""Build authed collections into the parsed catalog (v3):
canvases (graph summaries), characters, props, scenes, style_prompts."""
import json, os, os, collections

SRC = os.environ.get("AIX_BASE", "/home/z/my-project") + "/download/aixstudio"
AUTH = f"{SRC}/auth"
DST = os.environ.get("AIX_BASE", "/home/z/my-project") + "/src/data/catalog"
os.makedirs(DST, exist_ok=True)

def num(v):
    try:
        if v is None or v == "": return None
        return int(float(v))
    except Exception:
        return None

singles = json.load(open(f"{AUTH}/singles.json"))
cat_names = {}
for coll in ["character", "prop", "scene"]:
    for c in (singles.get(f"{coll}_categories", {}).get("data") or []):
        cat_names[str(c["id"])] = c["name"]

# ---------- CANVASES ----------
def node_media(node):
    """best media url from a node's target"""
    t = node.get("target") or {}
    infos = t.get("targetInfoList") or []
    for i in infos:
        u = i.get("findUrl") or i.get("showUrl")
        if u: return {"url": u, "thumb": i.get("thumbnailUrl") or u}
    return None

def extract_prompts(nodes):
    """text content from text/input nodes, longest first"""
    out = []
    for n in nodes:
        if n.get("type") in ("text", "input"):
            d = n.get("data") or {}
            for k in ("text", "value", "content", "defValue", "label"):
                v = d.get(k)
                if isinstance(v, str) and len(v) > 25:
                    out.append({"field": n.get("label") or k, "text": v})
                    break
            t = n.get("target") or {}
            ti = (t.get("targetInfoList") or [])
            for i in ti:
                v = i.get("des") or i.get("text")
                if isinstance(v, str) and len(v) > 25:
                    out.append({"field": n.get("label") or "des", "text": v})
    out.sort(key=lambda p: -len(p["text"]))
    return out[:5]

def canvas_record(cid, origin, title=None, gallery_id=None, author=None,
                  name=None, create_user=None, created=None, updated=None, content=None):
    try:
        g = json.loads(content) if content else {}
    except Exception:
        g = {}
    nodes = g.get("nodes") or []
    conns = g.get("connections") or []
    types = collections.Counter(n.get("type") for n in nodes)
    # representative media: prefer video node, then image
    rep = None
    for n in nodes:
        if n.get("type") == "video":
            rep = node_media(n)
            if rep: rep["kind"] = "video"; break
    if not rep:
        for n in nodes:
            if n.get("type") in ("image", "upload"):
                rep = node_media(n)
                if rep: rep["kind"] = "image"; break
    media_files = []
    for n in nodes:
        m = node_media(n)
        if m and len(media_files) < 8: media_files.append(m["url"])
    return {
        "id": f"canvas_{cid}", "sourceId": str(cid), "type": "canvas",
        "title": title or name or f"Canvas {cid}",
        "origin": origin,  # gallery | flow
        "galleryId": gallery_id,
        "creator": {"id": str(create_user) if create_user else None, "name": author},
        "media": {
            "kind": "canvas", "url": rep["url"] if rep else None,
            "thumb": rep["thumb"] if rep else None,
            "displayKind": rep["kind"] if rep else None,
            "width": None, "height": None, "bytes": None,
        },
        "graph": {
            "nodeCount": len(nodes), "edgeCount": len(conns),
            "nodeTypes": dict(types),
            "mediaFiles": media_files,
        },
        "prompt": extract_prompts(nodes) or None,
        "timestamps": {"created": created, "updated": updated},
        "graphAvailable": bool(nodes),
    }

canvases = []
seen_gallery = set()   # gallery canvasJsonInfoId namespace
for l in open(f"{AUTH}/canvas_graphs.jsonl"):
    r = json.loads(l)
    cid = str(r["canvasJsonInfoId"])
    if cid in seen_gallery: continue
    seen_gallery.add(cid)
    canvases.append(canvas_record(
        cid, "gallery", title=r.get("galleryTitle"), gallery_id=r.get("galleryId"),
        author=r.get("galleryAuthor"), name=r.get("name"),
        create_user=r.get("createUserId"), created=r.get("createTime"),
        updated=r.get("updateTime"), content=r.get("canvasContent")))

seen_flow = set()       # flow-id namespace (may legitimately collide with gallery ids)
for l in open(f"{AUTH}/flow_details.jsonl"):
    r = json.loads(l)
    fid = str(r.get("__flowId") or "")
    if not fid or fid in seen_flow: continue
    seen_flow.add(fid)
    canvases.append(canvas_record(
        fid, "flow", title=r.get("name"), name=r.get("name"),
        create_user=r.get("createUserId"), created=r.get("createTime"),
        updated=r.get("updateTime"), content=r.get("canvasJson")))

# ---------- CHARACTERS / PROPS / SCENES ----------
def lib_record(kind, r, img_keys):
    attrs = {}
    try: attrs = json.loads(r.get("attrs") or "{}")
    except Exception: pass
    img = r.get(img_keys[0])
    return {
        "id": f"{kind}_{r['id']}", "sourceId": str(r["id"]), "type": kind,
        "title": r.get("name"),
        "media": {"kind": "image", "url": img, "thumb": img,
                  "width": None, "height": None, "bytes": None,
                  "extra": {k: r.get(k) for k in img_keys[1:] if r.get(k)}},
        "category": cat_names.get(str(r.get("categoryId"))),
        "attrs": attrs or None,
        "timestamps": {"created": r.get("createTime")},
    }

characters = [lib_record("character", json.loads(l), ["imgPortrait", "imgExprGrid", "imgFull"])
              for l in open(f"{AUTH}/character_lib.jsonl")]
props = [lib_record("prop", json.loads(l), ["imgHero", "imgGrid6view"])
         for l in open(f"{AUTH}/prop_lib.jsonl")]
scenes = [lib_record("scene", json.loads(l), ["imgHero", "imgGrid4view"])
          for l in open(f"{AUTH}/scene_lib.jsonl")]

# ---------- STYLE PROMPTS ----------
style_prompts = []
for r in (singles.get("style_prompts", {}).get("data") or []):
    style_prompts.append({
        "id": f"styleprompt_{r['id']}", "sourceId": str(r["id"]), "type": "style_prompt",
        "title": r.get("name"),
        "media": {"kind": "image", "url": r.get("coverUrl"),
                  "thumb": r.get("coverUrl"), "width": None, "height": None, "bytes": None},
        "tags": [t for t in (r.get("tagList") or []) if t] or None,
        "prompt": [{"field": "style prompt", "text": r["prompt"]}] if r.get("prompt") else None,
        "timestamps": {"created": r.get("createTime")},
    })

# ---------- write ----------
def w(name, data):
    with open(f"{DST}/{name}.json", "w") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"{name:16s} {len(data):6d} records")

w("canvases", canvases)
w("characters", characters)
w("props", props)
w("scenes", scenes)
w("style_prompts", style_prompts)

# ---------- merge into index ----------
idx_path = f"{DST}/index.json"
idx = json.load(open(idx_path))
idx["counts"]["canvases"] = len(canvases)
idx["counts"]["characters"] = len(characters)
idx["counts"]["props"] = len(props)
idx["counts"]["scenes"] = len(scenes)
idx["counts"]["style_prompts"] = len(style_prompts)

def facet(coll, fn):
    return dict(collections.Counter(fn(r) for r in coll if fn(r)).most_common(20))

idx["facets"]["canvases"] = {
    "origin": facet(canvases, lambda r: r["origin"]),
    "nodeCount": None,  # numeric — computed client-side
}
idx["facets"]["characters"] = {"category": facet(characters, lambda r: r["category"])}
idx["facets"]["props"] = {"category": facet(props, lambda r: r["category"])}
idx["facets"]["scenes"] = {"category": facet(scenes, lambda r: r["category"])}
idx["canvasQueue"] = {
    "showcases": sum(1 for r in canvases if r["origin"] == "gallery"),
    "workflows": sum(1 for r in canvases if r["origin"] == "flow"),
    "note": "canvas graphs EXTRACTED (authed phase) — viewer live",
    "totalNodes": sum(r["graph"]["nodeCount"] for r in canvases),
    "totalEdges": sum(r["graph"]["edgeCount"] for r in canvases),
}
json.dump(idx, open(idx_path, "w"), ensure_ascii=False, indent=1)

print("\ncanvas totals:", idx["canvasQueue"])
print("top canvas nodeTypes:",
      dict(collections.Counter(t for r in canvases for t in r["graph"]["nodeTypes"]).most_common(10)))
print("canvases with prompts:", sum(1 for r in canvases if r["prompt"]))
