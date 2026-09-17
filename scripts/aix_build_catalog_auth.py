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

# attr code -> Chinese label maps (from the site's own filter metadata), so
# the UI can render 男/青年/东方脸 instead of male/young/east_asian.
label_maps = {}
for coll in ["character", "prop", "scene"]:
    lm = {}
    for f in (singles.get(f"{coll}_filters", {}).get("data") or []):
        code = f.get("code")
        if not code: continue
        lm[code] = {str(o.get("code")): o.get("label") for o in (f.get("options") or []) if o.get("code")}
    if lm: label_maps[coll] = lm

# Gallery (showcase) records — joined into canvas records by galleryId so the
# Canvases tab carries the showcase's tags/stats/creator/description.
gallery_by_id = {}
for r in json.load(open(f"{SRC}/gallery.deduped.json")):
    gallery_by_id[str(r.get("id"))] = r

# Public user profiles (userId -> profile) for creator name/avatar/follower joins.
profiles = json.load(open(f"{SRC}/user_profiles.json"))

def creator_of(uid, name=None, avatar=None):
    """Creator block joined with the public profile when available."""
    uid = str(uid) if uid else None
    if not uid: return None
    p = profiles.get(uid)
    if p and not p.get("err"):
        return {"id": uid, "name": p.get("nickName") or name, "avatar": p.get("avatarUrl") or avatar,
                "followers": num(p.get("followerCount")) or 0}
    return {"id": uid, "name": name, "avatar": avatar}

# flows.jsonl (list view) carries createUserId on 100% of records while
# flow_details strips it — needed for flow-origin canvas creators.
flow_owner = {}
for l in open(f"{SRC}/flows.jsonl"):
    r = json.loads(l)
    if r.get("createUserId"):
        flow_owner[str(r["id"])] = str(r["createUserId"])

# ---------- CANVASES ----------
def node_media(node):
    """best media url from a node's target"""
    t = node.get("target") or {}
    infos = t.get("targetInfoList") or []
    for i in infos:
        u = i.get("findUrl") or i.get("showUrl")
        if u: return {"url": u, "thumb": i.get("thumbnailUrl") or u}
    return None

def _story_prompts(nodes):
    """Consolidated storyboard/screenplay corpus (audit round2-c P1):
    scriptGen + shot-bearing nodes carry shotPrompt/sceneDesc/dialogue/
    sound/light text + full screenplays — previously never surfaced, so
    story canvases showed prompt:null. One consolidated entry per node."""
    out = []
    for n in nodes:
        d = n.get("data") or {}
        if not isinstance(d, dict):
            continue
        parts = []
        for k, name in (("storyboardUserPrompt", "剧本"), ("storyboardParsedDes", "分镜解析")):
            v = d.get(k)
            if isinstance(v, str) and len(v) > 60:
                parts.append(f"【{name}】\n{v}")
        for ep in (d.get("episodes") or []):
            if isinstance(ep, dict):
                st = ep.get("scriptText")
                if isinstance(st, str) and len(st) > 60:
                    parts.append(f"【{ep.get('name') or '剧集'}】\n{st}")
        shot_lines = []
        for sh in (d.get("shots") or []):
            if not isinstance(sh, dict):
                continue
            no = sh.get("shotNo") or "?"
            bits = []
            for k, name in (("sceneDesc", "场景"), ("sceneType", "景别"), ("characterAction", "动作"),
                            ("emotion", "情绪"), ("dialogue", "台词"), ("soundEffect", "音效"),
                            ("lightMood", "光影"), ("shotPrompt", "画面提示词"),
                            ("videoMotionPrompt", "运镜"), ("duration", "时长")):
                v = sh.get(k)
                if isinstance(v, str) and v.strip() and v != "无":
                    bits.append(f"{name}: {v}")
            for ch in (sh.get("characters") or []):
                if isinstance(ch, dict) and ch.get("name"):
                    cd_ = ch.get("desc")
                    bits.append(f"人物 {ch['name']}: {cd_}" if cd_ else f"人物 {ch['name']}")
            if bits:
                shot_lines.append(f"— 分镜{no} —\n" + "\n".join(bits))
        if shot_lines:
            parts.append(f"【分镜脚本 · {len(shot_lines)} shots】\n" + "\n\n".join(shot_lines))
        if parts:
            text = "\n\n".join(parts)
            if len(text) > 8000:
                text = text[:8000] + f" … [{len(text)-8000} chars truncated]"
            label = n.get("label") or d.get("label") or "剧本分镜"
            out.append({"field": f"{label} · story", "text": text})
    return out

def extract_prompts(nodes):
    """text content from text/input nodes, longest first (top 12 — the old
    top-5 cap silently dropped ~46% of des-node prompt text)"""
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
            # FLAT target.des (audit round2-e P1) — same rationale as the
            # public builder: input nodes carry prompts ONLY on the flat field
            fd = t.get("des")
            if isinstance(fd, str) and len(fd) > 25:
                out.append({"field": n.get("label") or "des", "text": fd})
    out.sort(key=lambda p: -len(p["text"]))
    story = _story_prompts(nodes)
    for p in out:   # cap individual texts (consistency with workflow prompts)
        if len(p["text"]) > 4000:
            p["text"] = p["text"][:4000] + f" … [{len(p['text'])-4000} chars truncated]"
    _seen = set()
    out = [p for p in out if p["text"] not in _seen and not _seen.add(p["text"])]
    # story corpus rides FIRST (its entries carry their own 8000-char cap);
    # fresh dedup set — the old one already holds every text-node prompt,
    # which zeroed them out of the merged list
    _seen2 = set()
    merged = story + out
    merged = [p for p in merged if p["text"] not in _seen2 and not _seen2.add(p["text"])]
    return merged[:16]

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
        # order-preserving dedupe (raw tag lists carry duplicates)
        for t in [t for t in (show.get("parentTagNames") or []) if t] \
                 + [t for t in (show.get("sonTagNames") or []) if t]:
            if t not in tags: tags.append(t)
    # Creator: the GALLERY userId is the real author. The canvas record's
    # createUserId is the uploader (a shared system account on 75/313 works —
    # linking by it misattributed works across 42 different authors).
    if show and show.get("userId"):
        creator = creator_of(show.get("userId"), author or show.get("nickName"), show.get("avatarUrl"))
    elif create_user:
        creator = creator_of(create_user, author)
    else:
        creator = None
    # stats: only real numbers (null coerced to 0 displayed fake zeros)
    stats = None
    if show:
        stats = {}
        if show.get("goodNum") is not None: stats["likes"] = num(show.get("goodNum"))
        if show.get("collectNum") is not None: stats["collects"] = num(show.get("collectNum"))
        if not stats: stats = None
    # id namespaces are DISJOINT by origin: gallery canvases keep canvas_<cid>
    # (canvasJsonInfoId), flow-origin canvases take canvasf_<fid>. A shared
    # canvas_<id> scheme let 18 colliding ids shadow flow records in
    # getRecord()/api/detail (the 2026-09-16 audit's P0).
    rid = f"canvasf_{cid}" if origin == "flow" else f"canvas_{cid}"
    rec = {
        "id": rid, "sourceId": str(cid), "type": "canvas",
        "title": title or name or (show.get("name") if show else None) or f"Canvas {cid}",
        "origin": origin,  # gallery | flow
        "galleryId": gallery_id,
        "description": (show.get("des") if show else None),
        "creator": creator,
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
        "stats": stats,
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

seen_flow = set()       # flow-id namespace (collides with gallery ids — that's
                       # WHY record ids are origin-prefixed: canvasf_ vs canvas_)
# ALL flow types now carry graphs (comfy type-1 probed 2026-09-16), stored in
# ~40MB chunks flow_details*.jsonl. Only type-2 (canvas-kind) flows become
# CANVAS records; comfy workflows surface their graphs on their own workflow
# records via the public builder's graph join.
import glob as _glob, re as _re
def _chunk_key(p):
    m = _re.search(r"_(\d+)\.jsonl$", p)
    return (int(m.group(1)) if m else 0, p)   # numeric order: _2 < _10
for fp in sorted(_glob.glob(f"{AUTH}/flow_details*.jsonl"), key=_chunk_key):
  for l in open(fp):
    r = json.loads(l)
    fid = str(r.get("__flowId") or "")
    if not fid or fid in seen_flow: continue
    seen_flow.add(fid)
    if str(r.get("type")) != "2": continue   # canvas collection = type-2 only
    # flow_details strips createUserId — resolve the real owner via the list
    # view join, then enrich name/avatar from public profiles.
    owner = flow_owner.get(fid) or r.get("createUserId")
    canvases.append(canvas_record(
        fid, "flow", title=r.get("name"), name=r.get("name"),
        create_user=owner, created=r.get("createTime"),
        updated=r.get("updateTime"), content=r.get("canvasJson")))

# ---------- CHARACTERS / PROPS / SCENES ----------
WORLDVIEW_LABELS = {}
for coll in ("prop", "scene"):
    for f in (singles.get(f"{coll}_filters", {}).get("data") or []):
        if f.get("code") == "worldview":
            WORLDVIEW_LABELS.update({str(o.get("code")): o.get("label")
                                     for o in (f.get("options") or [])})

def lib_worldview(r, det=None):
    """worldview 题材: props carry worldviewCodes (JSON array), scenes a
    singular worldviewCode — normalized to a list of Chinese labels.
    Detail records carry authoritative worldviewLabels (props) — used when
    present, with the filter-metadata map as fallback."""
    codes = []
    if r.get("worldviewCode"):
        codes = [str(r["worldviewCode"])]
    elif r.get("worldviewCodes"):
        try:
            codes = [str(c) for c in json.loads(r["worldviewCodes"]) if c]
        except Exception:
            codes = []
    if not codes:
        return None
    det_labels = {}
    for wl in ((det or {}).get("worldviewLabels") or []):
        if wl.get("code") is not None:
            det_labels[str(wl["code"])] = wl.get("label")
    return [det_labels.get(c) or WORLDVIEW_LABELS.get(c, c) for c in codes]

def strip_oss_param(u):
    """Strip the ?imageView2/... resize param — the base key IS the
    full-res original (verified: scene 164 thumb 12.5KB vs base 3MB)."""
    if not isinstance(u, str) or not u.startswith("http"):
        return u
    return u.split("?")[0]

def lib_record(kind, r, img_keys, det=None):
    attrs = {}
    try: attrs = json.loads(r.get("attrs") or "{}")
    except Exception: pass
    img = r.get(img_keys[0])
    # /detail returns the SAME image keys (full-res, no resize param) plus
    # detail-only extras (character imgThreeView). Merge what it has.
    full = strip_oss_param((det or {}).get(img_keys[0]) or img)
    gallery = []
    for k in img_keys:
        u = (det or {}).get(k) or r.get(k)
        if u:
            u = strip_oss_param(u)
            if u not in gallery:
                gallery.append(u)
    rec = {
        "id": f"{kind}_{r['id']}", "sourceId": str(r["id"]), "type": kind,
        "title": r.get("name"),
        "media": {"kind": "image", "url": full, "thumb": img,
                  "width": None, "height": None, "bytes": None,
                  "extra": {k: r.get(k) for k in img_keys[1:] if r.get(k)}},
        "category": cat_maps[kind].get(str(r.get("categoryId"))),
        "attrs": attrs or None,
        "worldview": lib_worldview(r, det) if kind in ("prop", "scene") else None,
        "timestamps": {"created": r.get("createTime")},
    }
    if len(gallery) > 1:
        rec["media"]["gallery"] = gallery
    if det:
        if det.get("desc"):
            rec["description"] = det["desc"]
        if det.get("featureText"):
            rec["featureText"] = det["featureText"]
        if det.get("outfitText"):
            rec["outfitText"] = det["outfitText"]
        if isinstance(det.get("context"), dict):
            ctx = {k: v for k, v in det["context"].items() if v}
            if ctx:
                rec["context"] = ctx
    return rec

def load_details(kind):
    """{id: detail-record} from the /detail enrichment scrape."""
    out = {}
    path = f"{AUTH}/{kind}_details.jsonl"
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for l in f:
            l = l.strip()
            if not l: continue
            try:
                d = json.loads(l)
                if d.get("id") is not None:
                    out[str(d["id"])] = d
            except Exception:
                pass
    return out

char_details = load_details("character")
prop_details = load_details("prop")
scene_details = load_details("scene")

def lib_rows(kind, img_keys, details):
    rows = []
    with open(f"{AUTH}/{kind}_lib.jsonl") as f:
        for l in f:
            l = l.strip()
            if not l: continue
            r = json.loads(l)
            rows.append(lib_record(kind, r, img_keys, det=details.get(str(r.get("id")))))
    return rows

characters = lib_rows("character", ["imgPortrait", "imgFull", "imgExprGrid", "imgThreeView"], char_details)
props = lib_rows("prop", ["imgHero", "imgGrid6view"], prop_details)
scenes = lib_rows("scene", ["imgHero", "imgGrid4view"], scene_details)

# ---------- STYLE PROMPTS ----------
# style tag ids -> names (2D/3D/写实风格/影视风格) — the tags facet showed
# raw ids ('2','3',...) before this join (audit round2-e P2)
STYLE_TAG_NAMES = {str(t["id"]): t["name"]
                   for t in (singles.get("style_tags", {}).get("data") or [])}
style_prompts = []
for r in (singles.get("style_prompts", {}).get("data") or []):
    style_prompts.append({
        "id": f"styleprompt_{r['id']}", "sourceId": str(r["id"]), "type": "style_prompt",
        "title": r.get("name"),
        "media": {"kind": "image", "url": r.get("coverUrl"),
                  "thumb": r.get("coverUrl"), "width": None, "height": None, "bytes": None},
        "tags": [STYLE_TAG_NAMES.get(str(t), t) for t in (r.get("tagList") or []) if t] or None,
        "prompt": [{"field": "style prompt", "text": r["prompt"]}] if r.get("prompt") else None,
        "timestamps": {"created": r.get("createTime")},
    })

# ---------- PROMPT COMPONENTS (cinematography prompt kit) ----------
# prompt-info/listAll returns a code->list map (126 templates: 镜头/相机/焦距/
# 光圈/快门/灯光/光向/光色/色调/运镜/速度) — each with Chinese prompt text
# + an icon. Flattened into typed records with a group label for faceting.
PC_GROUPS = {
    "LENS": "镜头", "CAM": "相机", "FOC": "焦距", "APT": "光圈",
    "SH": "快门", "SPD": "速度", "LIT": "灯光", "LDIR": "光向",
    "LCOL": "光色", "COL": "色调", "DIR": "运镜",
}
prompt_components = []
for code, items in (singles.get("prompt_components", {}).get("data") or {}).items():
    group = PC_GROUPS.get(code.rstrip("0123456789"), code)
    for r in items:
        prompt_components.append({
            "id": f"pcomp_{r['id']}", "sourceId": str(r["id"]), "type": "prompt_component",
            "title": r.get("name"),
            "media": {"kind": "image", "url": r.get("icon"), "thumb": r.get("icon"),
                      "width": None, "height": None, "bytes": None},
            "category": group,
            "code": code,
            "prompt": [{"field": group, "text": r["prompt"]}] if r.get("prompt") else None,
            "timestamps": {"created": r.get("createTime")},
        })
prompt_components.sort(key=lambda r: (r["category"], r["code"], r["title"] or ""))

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
w("prompt_components", prompt_components)

# ---------- merge into index ----------
idx_path = f"{DST}/index.json"
idx = json.load(open(idx_path))
idx["counts"]["canvases"] = len(canvases)
idx["counts"]["characters"] = len(characters)
idx["counts"]["props"] = len(props)
idx["counts"]["scenes"] = len(scenes)
idx["counts"]["style_prompts"] = len(style_prompts)
idx["counts"]["prompt_components"] = len(prompt_components)

def facet(coll, fn):
    return dict(collections.Counter(fn(r) for r in coll if fn(r)).most_common(30))

def attr_facets(coll, keys):
    out = {}
    for k in keys:
        f = facet(coll, lambda r: (r.get("attrs") or {}).get(k))
        if f: out[k] = f
    return out

CHAR_ATTR_KEYS = ["sex", "age", "vibe", "region", "race", "skin", "build", "height", "hairLen", "hairColor"]
PROP_ATTR_KEYS = ["size", "usage", "material", "rarity", "worldview"]
SCENE_ATTR_KEYS = ["indoor", "daytime", "scale", "style", "weather", "lighting", "worldview"]

idx["facets"]["canvases"] = {
    "origin": facet(canvases, lambda r: r["origin"]),
}
idx["facets"]["characters"] = {"category": facet(characters, lambda r: r["category"]), **attr_facets(characters, CHAR_ATTR_KEYS)}
# 题材 facet from the /detail context.major (audit round2-c P2 — the natural
# story-writing axis, 8 values across 5,926 records)
idx["facets"]["characters"]["context"] = facet(
    [r for r in characters if (r.get("context") or {}).get("major")],
    lambda r: r["context"]["major"])
idx["facets"]["props"] = {"category": facet(props, lambda r: r["category"]), **attr_facets(props, PROP_ATTR_KEYS)}
idx["facets"]["scenes"] = {"category": facet(scenes, lambda r: r["category"]), **attr_facets(scenes, SCENE_ATTR_KEYS)}
# worldview is a LIST on each record — facet it manually (attr_facets reads attrs)
idx["facets"]["props"]["worldview"] = dict(collections.Counter(
    w for r in props for w in (r.get("worldview") or [])).most_common(20))
idx["facets"]["scenes"]["worldview"] = dict(collections.Counter(
    w for r in scenes for w in (r.get("worldview") or [])).most_common(20))
idx["facets"]["prompt_components"] = {"category": facet(prompt_components, lambda r: r["category"])}

# Chinese labels for the attr codes (from the site's own filter metadata).
# label_maps: {collection: {attrCode: {valueCode: Chinese label}}} — flattened
# for the catalog as filterLabels["<attrCode>"] = attr name and
# filterLabels["<attrCode>:<valueCode>"] = value label.
# MERGE with the public builder's filterLabels (modelCode product names) —
# the old code replaced the dict and would have wiped them.
labels = dict(idx.get("filterLabels") or {})
# Scene dims the site's own metadata never defines (filter defs carry each
# dim twice, neither variant labels these 5; detail attrsLabels serve raw
# codes) — audit round2-a P2: 203/1416 scenes rendered raw English codes.
SCENE_LABEL_OVERRIDES = {
    "scale:medium": "中景", "scale:large": "大空间", "scale:vast": "辽阔全景",
    "scale:intimate": "私密近景", "indoor:semi_open": "半开放",
}
labels.update(SCENE_LABEL_OVERRIDES)

for coll, keys in [("character", CHAR_ATTR_KEYS), ("prop", PROP_ATTR_KEYS), ("scene", SCENE_ATTR_KEYS)]:
    for attr_code, value_map in (label_maps.get(coll) or {}).items():
        if attr_code not in keys: continue
        # attr display name lives in the RAW filter defs (label_maps drops it)
        raw = next((f for f in (singles.get(f"{coll}_filters", {}).get("data") or [])
                    if f.get("code") == attr_code), None)
        labels[attr_code] = (raw or {}).get("name") or attr_code
        for value_code, value_label in value_map.items():
            labels[f"{attr_code}:{value_code}"] = value_label or value_code

# AUTHORITATIVE labels from the /detail enrichment (attrsLabels carry the
# server's own dimName + label + labelEn per record) — these override the
# filter-metadata guesses for any code the site actually serves.
det_label_sources = {
    "character": char_details, "prop": prop_details, "scene": scene_details,
}
for coll, details in det_label_sources.items():
    for det in details.values():
        for al in (det.get("attrsLabels") or []):
            dim = str(al.get("dim") or al.get("code") or "")
            code = str(al.get("code") or "")
            label = al.get("label")
            dim_name = al.get("dimName")
            if dim and dim_name:
                labels.setdefault(dim, dim_name)
            if dim and code and label:
                labels[f"{dim}:{code}"] = label
# Re-apply the scene overrides LAST — the attrsLabels loop above serves raw
# codes for these 5 dims (scale:medium->"medium") and last-write-wins
# clobbered the curated Chinese labels (audit round2-d fix-verification FAIL).
labels.update(SCENE_LABEL_OVERRIDES)
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
