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
# Per-collection category maps — ids collide across libraries (character id 3
# = 商战总裁, prop id 3 = 剑类), so a single merged map silently corrupted
# every character category (and any scene id < ~423).
cat_maps = {}
for coll in ["character", "prop", "scene"]:
    cat_maps[coll] = {
        str(c["id"]): c["name"]
        for c in (singles.get(f"{coll}_categories", {}).get("data") or [])
    }

# Gallery (showcase) records — joined into canvas records by galleryId so the
# Canvases tab carries the showcase's tags/stats/creator/description.
gallery_by_id = {}
for r in json.load(open(f"{SRC}/gallery.deduped.json")):
    gallery_by_id[str(r.get("id"))] = r

# Filter-label maps (code → Chinese label) from the site's own filter metadata.
filter_defs = {}
for coll in ["character", "prop", "scene"]:
    filter_defs[coll] = singles.get(f"{coll}_filters", {}).get("data") or []

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
    groups = g.get("groups") or []
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
    # showcase (gallery) metadata joined by galleryId
    show = gallery_by_id.get(str(gallery_id)) if gallery_id else None
    tags = []
    if show:
        tags = [t for t in (show.get("parentTagNames") or []) if t] \
             + [t for t in (show.get("sonTagNames") or []) if t]
    rec = {
        "id": f"canvas_{cid}", "sourceId": str(cid), "type": "canvas",
        "title": title or name or (show.get("name") if show else None) or f"Canvas {cid}",
        "origin": origin,  # gallery | flow
        "galleryId": gallery_id,
        "description": (show.get("des") if show else None),
        "creator": {
            "id": str(create_user) if create_user else (str(show.get("userId")) if show else None),
            "name": author or (show.get("nickName") if show else None),
            "avatar": (show.get("avatarUrl") if show else None),
        },
        "media": {
            "kind": "canvas", "url": rep["url"] if rep else None,
            "thumb": rep["thumb"] if rep else None,
            "displayKind": rep["kind"] if rep else None,
            "width": None, "height": None, "bytes": None,
        },
        "graph": {
            "nodeCount": len(nodes), "edgeCount": len(conns),
            "groupCount": len(groups),
            "nodeTypes": dict(types),
            "mediaFiles": media_files,
        },
        "prompt": extract_prompts(nodes) or None,
        "tags": tags or None,
        "stats": ({"likes": int(show.get("goodNum") or 0), "collects": int(show.get("collectNum") or 0)}
                  if show else None),
        "timestamps": {"created": created or (show.get("createTime") if show else None),
                        "updated": updated},
        "graphAvailable": bool(nodes),
    }
    return rec

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
        "category": cat_maps[kind].get(str(r.get("categoryId"))),
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
    return dict(collections.Counter(fn(r) for r in coll if fn(r)).most_common(30))

def attr_facets(coll, keys):
    out = {}
    for k in keys:
        f = facet(coll, lambda r: (r.get("attrs") or {}).get(k))
        if f: out[k] = f
    return out

CHAR_ATTR_KEYS = ["sex", "age", "vibe", "region", "race", "skin", "build", "height", "hairLen", "hairColor"]
PROP_ATTR_KEYS = ["size", "usage", "material", "rarity"]
SCENE_ATTR_KEYS = ["indoor", "daytime", "scale", "style", "weather", "lighting"]

idx["facets"]["canvases"] = {
    "origin": facet(canvases, lambda r: r["origin"]),
}
idx["facets"]["characters"] = {"category": facet(characters, lambda r: r["category"]), **attr_facets(characters, CHAR_ATTR_KEYS)}
idx["facets"]["props"] = {"category": facet(props, lambda r: r["category"]), **attr_facets(props, PROP_ATTR_KEYS)}
idx["facets"]["scenes"] = {"category": facet(scenes, lambda r: r["category"]), **attr_facets(scenes, SCENE_ATTR_KEYS)}

# Chinese labels for the attr codes (from the site's own filter metadata)
labels = {}
for coll, keys in [("character", CHAR_ATTR_KEYS), ("prop", PROP_ATTR_KEYS), ("scene", SCENE_ATTR_KEYS)]:
    for fd in filter_defs.get(coll, []):
        code = fd.get("code")
        if code in keys:
            labels[code] = fd.get("name")
            for opt in (fd.get("options") or []):
                labels.setdefault(f"{code}:{opt.get('code')}", opt.get("label") or opt.get("labelEn"))
idx["filterLabels"] = labels
idx["canvasQueue"] = {
    "showcases": sum(1 for r in canvases if r["origin"] == "gallery"),
    "workflows": sum(1 for r in canvases if r["origin"] == "flow"),
    "note": "canvas graphs EXTRACTED (authed phase) — React Flow viewer live",
    "totalNodes": sum(r["graph"]["nodeCount"] for r in canvases),
    "totalEdges": sum(r["graph"]["edgeCount"] for r in canvases),
    "totalGroups": sum(r["graph"].get("groupCount") or 0 for r in canvases),
}
json.dump(idx, open(idx_path, "w"), ensure_ascii=False, indent=1)

print("\ncanvas totals:", idx["canvasQueue"])
print("top canvas nodeTypes:",
      dict(collections.Counter(t for r in canvases for t in r["graph"]["nodeTypes"]).most_common(10)))
print("canvases with prompts:", sum(1 for r in canvases if r["prompt"]))
