#!/usr/bin/env python3
"""
Transform raw aix.studio datasets into a unified, typed AIGC content catalog.

Output: src/data/catalog/*.json — consumed by the Next.js catalog browser.

Content types (commercial noise deliberately dropped: cloud GPU plans, ads,
member plans, site config, cs-bot, menus):
  generations    — AI generation records (media + full params/prompts/models)
  showcases      — published canvas works (video/image + tags + authors)
  workflows      — reusable workflow templates (recipes)
  style_materials — photography style reference images by category
  assets         — user source-image library
  creators       — public author profiles
"""
import datetime
import json, os, re, collections

import os
SRC = f"{os.environ.get("AIX_BASE", "/home/z/my-project")}/download/aixstudio"
DST = f'{os.environ.get("AIX_BASE", "/home/z/my-project")}/src/data/catalog'
os.makedirs(DST, exist_ok=True)

def load(name):
    return [json.loads(l) for l in open(f"{SRC}/{name}.jsonl")]

def num(v):
    try:
        if v is None or v == "": return None
        return int(float(v))
    except Exception:
        return None

def media_kind_from_url(u):
    u = str(u or "")
    if re.search(r"\.(mp4|mov|webm)(\?|$)", u, re.I): return "video"
    if re.search(r"\.(mp3|wav|ogg|m4a)(\?|$)", u, re.I): return "audio"
    if re.search(r"\.(png|jpe?g|webp|gif|bmp)(\?|$)", u, re.I): return "image"
    return None

# ---------- shared lookups ----------
aux = json.load(open(f"{SRC}/aux_public.json"))
aux2 = json.load(open(f"{SRC}/aux_public2.json"))
profiles = json.load(open(f"{SRC}/user_profiles.json"))

# prism tag id -> name
prism_tag = {str(t["id"]): t["tagName"] for t in (aux2.get("prism_tags", {}).get("data") or [])}

# flow tag tree id -> name (both levels)
flow_tag = {}
for t in (aux.get("flow_tags", {}).get("data") or []):
    flow_tag[str(t["id"])] = t.get("name")
    for c in (t.get("children") or []):
        flow_tag[str(c["id"])] = c.get("name")

flows_by_id = {r["id"]: r for r in load("flows")}

def creator_of(uid, name=None, avatar=None):
    uid = str(uid) if uid else None
    p = profiles.get(uid) if uid else None
    if p and not p.get("err"):
        return {"id": uid, "name": p.get("nickName") or name, "avatar": p.get("avatarUrl"),
                "followers": num(p.get("followerCount")) or 0,
                "likes": num(p.get("likeCount")) or 0}
    return {"id": uid, "name": name, "avatar": avatar}

# ---------- 1. GENERATIONS (task_outputs) ----------
PROMPT_COMPS = {"Positive", "MorePositive"}
SELECT_COMPS = {"AgentSelectOne", "AgentSelect", "CustomDropSelect", "AgentRatioSelect"}

def classify_select(field):
    f = str(field or "")
    if re.search(r"分辨率|resolution|quality", f, re.I): return "resolution"
    if re.search(r"比例|ratio|尺寸", f): return "ratio"
    if re.search(r"时长|秒|duration", f): return "duration"
    if re.search(r"模型|model|version|风格", f, re.I): return "model"
    return "setting"

def parse_generation(r):
    p = r.get("param")
    pj = {}
    try: pj = json.loads(p) if p else {}
    except Exception: pj = {}

    prompts, instructions, refs, seeds, sizes = [], [], [], [], []
    settings = collections.defaultdict(list)
    for item in (pj.get("paramList") or []):
        c = item.get("component") or ""
        dv = str(item.get("defValue") or "").strip()
        field = item.get("name")
        if c in PROMPT_COMPS and len(dv) > 1:
            prompts.append({"field": field, "text": dv})
        elif c == "CustomTextInput" and len(dv) > 8:
            instructions.append({"field": field, "text": dv})
        elif c in SELECT_COMPS and dv:
            settings[classify_select(field)].append({"field": field, "value": dv})
        elif c == "SeedNoShow" and dv:
            seeds.append(dv)
        elif c in ("DrawSize", "Width", "Height") and dv:
            sizes.append({"field": field, "value": num(dv)})
        elif c == "ImageUploadAuto" and dv.startswith("http"):
            refs.append(dv)
        for d in (item.get("desList") or []):
            if isinstance(d, str) and len(d) > 8:
                instructions.append({"field": (field or "") + " · des", "text": d})

    # model / quality from paramStr e.g. "9:16 | veo3.1-fast", "10 s | 竖版高清"
    param_str = pj.get("paramStr")
    if param_str:
        for part in re.split(r"\s*\|\s*", str(param_str)):
            part = part.strip()
            if not part: continue
            if re.match(r"^\d+[:x]\d+$", part) or part == "auto": settings["ratio"].append({"field": "paramStr", "value": part})
            elif re.match(r"^\d+k$", part, re.I): settings["resolution"].append({"field": "paramStr", "value": part})
            elif re.match(r"^\d+\s*s$", part, re.I) or re.match(r"^\d+$", part): settings["duration"].append({"field": "paramStr", "value": part})
            elif part != "auto": settings["model"].append({"field": "paramStr", "value": part})

    flow = flows_by_id.get(str(r.get("cworkFlowInfoId") or ""))
    kind = {"1": "image", "2": "video", "4": "audio"}.get(str(r.get("outPutType")))
    if not kind: kind = media_kind_from_url(r.get("findUrl")) or "image"

    def first(cat):
        v = settings.get(cat) or []
        return v[0]["value"] if v else None

    return {
        "id": f"gen_{r['id']}",
        "sourceId": r["id"],
        "type": "generation",
        "title": r.get("des") or (flow.get("name") if flow else None) or f"Generation {r['id']}",
        "media": {
            "kind": kind,
            "url": r.get("findUrl"),
            "thumb": r.get("thumbnailUrl") or r.get("findUrl"),
            "width": num(r.get("width")), "height": num(r.get("height")),
            "bytes": num(r.get("fileSize")),
        },
        "prompt": prompts or None,
        "instructions": instructions or None,
        "params": {
            "model": first("model"), "resolution": first("resolution"),
            "ratio": first("ratio"), "duration": first("duration"),
            "seeds": seeds or None,
            "all": {k: [x["value"] for x in v] for k, v in settings.items()} if settings else None,
            "summary": param_str,
        },
        "referenceImages": refs or None,
        "workflow": {"id": r.get("cworkFlowInfoId"), "name": (flow or {}).get("name"),
                     "kind": "canvas" if str((flow or {}).get("type")) == "2" else "comfy"} if flow else None,
        "creator": creator_of(r.get("userId") or r.get("createUserId"), r.get("nickName"), r.get("avatarUrl")),
        "stats": {"likes": num(r.get("goodNum")) or 0, "collects": num(r.get("collectNum")) or 0},
        "cost": {"runtimeSec": num(pj.get("runTime")), "spend": num(pj.get("spendNum"))},
        "timestamps": {"created": r.get("createTime"), "updated": r.get("updateTime")},
        "canvasRef": r.get("canvasJsonInfoId") or None,
    }

generations = [parse_generation(r) for r in load("task_outputs")]

# ---------- 2. SHOWCASES (gallery) ----------
def parse_showcase(r):
    kind = media_kind_from_url(r.get("workFileKey")) or ("canvas" if not r.get("workFileKey") else "image")
    return {
        "id": f"show_{r['id']}", "sourceId": r["id"], "type": "showcase",
        "title": r.get("name"), "description": r.get("des"),
        "media": {
            "kind": kind, "url": r.get("workFileKey") or r.get("coverFileKey"),
            "thumb": r.get("coverFileKey") or r.get("thumbnailUrl"),
            "width": num(r.get("width")), "height": num(r.get("height")), "bytes": num(r.get("fileSize")),
        },
        "tags": [t for t in (r.get("parentTagNames") or []) if t] + [t for t in (r.get("sonTagNames") or []) if t],
        "creator": creator_of(r.get("userId"), r.get("nickName"), r.get("avatarUrl")),
        "stats": {"likes": num(r.get("goodNum")) or 0, "collects": num(r.get("collectNum")) or 0},
        "timestamps": {"created": r.get("createTime"), "updated": r.get("updateTime")},
        "canvasRef": r.get("canvasJsonInfoId") or None,  # JWT-phase join key
    }

showcases = [parse_showcase(r) for r in load("gallery")]

# ---------- 3. WORKFLOWS (flows) ----------
FLOW_TYPE = {"1": "comfy", "2": "canvas", "3": "draft", "4": "tool", "None": "other"}
def parse_flow(r):
    tags = []
    tid = str(r.get("canvasFlowTagInfoId") or "")
    if tid and tid in flow_tag: tags.append(flow_tag[tid])
    sid = str(r.get("sonTagId") or "")
    if sid and sid in flow_tag: tags.append(flow_tag[sid])
    return {
        "id": f"flow_{r['id']}", "sourceId": r["id"], "type": "workflow",
        "title": r.get("name") or f"Workflow {r['id']}",
        "description": r.get("des"),
        "workflowKind": FLOW_TYPE.get(str(r.get("type")), "other"),
        "media": {
            "kind": media_kind_from_url(r.get("findUrl")) or "image",
            "url": r.get("findUrl"), "thumb": r.get("thumbnailUrl") or r.get("findUrl"),
            "width": num(r.get("width")), "height": num(r.get("height")), "bytes": num(r.get("fileSize")),
        },
        "tags": tags,
        "creator": creator_of(r.get("createUserId"), r.get("nickName")),
        "timestamps": {"created": r.get("createTime"), "updated": r.get("updateTime")},
        "canvasRef": str(r["id"]) if str(r.get("type")) == "2" else None,
    }

workflows = [parse_flow(r) for r in flows_by_id.values()]

# ---------- 4. STYLE MATERIALS (prism_material) ----------
def parse_prism(r):
    return {
        "id": f"style_{r['id']}", "sourceId": r["id"], "type": "style_material",
        "title": r.get("name"),
        "media": {
            "kind": "image", "url": r.get("findUrl"), "thumb": r.get("thumbnailUrl") or r.get("findUrl"),
            "width": num(r.get("width")), "height": num(r.get("height")), "bytes": num(r.get("fileSize")),
        },
        "category": prism_tag.get(str(r.get("prismTagInfoId"))) or "uncategorized",
        "stats": {"likes": num(r.get("goodNum")) or 0, "collects": num(r.get("collectNum")) or 0},
        "timestamps": {"created": r.get("createTime")},
    }

style_materials = [parse_prism(r) for r in load("prism_material")]

# ---------- 5. ASSETS ----------
def parse_asset(r):
    return {
        "id": f"asset_{r['id']}", "sourceId": r["id"], "type": "asset",
        "title": r.get("name") or f"Asset {r['id']}",
        "media": {
            "kind": "image", "url": r.get("findUrl"), "thumb": r.get("thumbnailUrl") or r.get("findUrl"),
            "width": num(r.get("width")), "height": num(r.get("height")), "bytes": num(r.get("fileSize")),
        },
        "creator": creator_of(r.get("createUserId") or r.get("userId"), r.get("nickName")),
        "timestamps": {"created": r.get("createTime")},
    }

assets = [parse_asset(r) for r in load("assets")]

# ---------- 6. USER PRISM (39) — folded into showcases as "community" works ----------
user_prism = [{
    "id": f"ushow_{r['id']}", "sourceId": r["id"], "type": "showcase",
    "title": r.get("name"),
    "media": {"kind": "image", "url": r.get("findUrl"), "thumb": r.get("thumbnailUrl") or r.get("findUrl"),
              "width": num(r.get("width")), "height": num(r.get("height")), "bytes": num(r.get("fileSize"))},
    "tags": ["community"],
    "stats": {"likes": num(r.get("goodNum")) or 0, "collects": num(r.get("collectNum")) or 0},
    "timestamps": {"created": r.get("createTime")},
} for r in load("user_prism")]
showcases.extend(user_prism)

# ---------- 7. CREATORS ----------
creators = []
for uid, p in profiles.items():
    if p.get("err") or not p.get("nickName"): continue
    creators.append({
        "id": f"user_{uid}", "sourceId": uid, "type": "creator",
        "name": p.get("nickName"), "avatar": p.get("avatarUrl"), "bio": p.get("bio") or None,
        "stats": {"followers": num(p.get("followerCount")) or 0, "following": num(p.get("followingCount")) or 0,
                  "likes": num(p.get("likeCount")) or 0, "collects": num(p.get("collectCount")) or 0},
    })

# ---------- write ----------
def w(name, data):
    with open(f"{DST}/{name}.json", "w") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"{name:18s} {len(data):6d} records")

w("generations", generations)
w("showcases", showcases)
w("workflows", workflows)
w("style_materials", style_materials)
w("assets", assets)
w("creators", creators)

# ---------- index with facets ----------
def facet(coll, fn):
    return dict(collections.Counter(fn(r) for r in coll).most_common(30))

index = {
    "counts": {k: len(v) for k, v in {
        "generations": generations, "showcases": showcases, "workflows": workflows,
        "style_materials": style_materials, "assets": assets,
        "creators": creators}.items()},
    "facets": {
        "generations": {
            "mediaKind": facet(generations, lambda r: r["media"]["kind"]),
            "model": facet([r for r in generations if r["params"]["model"]], lambda r: r["params"]["model"]),
            "resolution": facet([r for r in generations if r["params"]["resolution"]], lambda r: r["params"]["resolution"]),
            "ratio": facet([r for r in generations if r["params"]["ratio"]], lambda r: r["params"]["ratio"]),
            "hasPrompt": {"yes": sum(1 for r in generations if r["prompt"]), "no": sum(1 for r in generations if not r["prompt"])},
        },
        "showcases": {
            "mediaKind": facet(showcases, lambda r: r["media"]["kind"]),
            "tags": facet(showcases, lambda r: ", ".join(r["tags"][:1]) if r["tags"] else "untagged"),
        },
        "workflows": {
            "kind": facet(workflows, lambda r: r["workflowKind"]),
            "tags": facet([r for r in workflows if r["tags"]], lambda r: r["tags"][0]),
        },
        "style_materials": {"category": facet(style_materials, lambda r: r["category"])},
    },
    "canvasQueue": {
        "showcases": sum(1 for r in showcases if r.get("canvasRef")),
        "workflows": sum(1 for r in workflows if r.get("canvasRef")),
        "note": "canvas graph JSON requires JWT (phase 4)",
    },
    "generatedAt": datetime.date.today().isoformat(),
    "sourceSite": "aix.studio",
}
json.dump(index, open(f"{DST}/index.json", "w"), ensure_ascii=False, indent=1)
print("\nindex:", json.dumps(index["counts"], indent=1))
print("generation facets:", json.dumps(index["facets"]["generations"], ensure_ascii=False, indent=1)[:600])
