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

# gallery comments (317 galleries) — joined onto showcase records
try:
    gallery_comments = json.load(open(f"{SRC}/gallery_comments.json"))
except FileNotFoundError:
    gallery_comments = {}

# flowCode -> product name (audit round2-c P2 — turns cryptic modelCode
# facets like tap_03 into Chinese product names like "AIX全能语言模型G3").
# Harvested RECURSIVELY from aux_public + aux_public2: card_classify
# flowList, workflow_tap03/workflow_main_image single-shots, cloud cards —
# 86 pairs total (1388/tap_04 remain unnamed: not in any captured metadata).
MODEL_NAME = {}
def _harvest_names(o):
    if isinstance(o, dict):
        fc, fn = o.get("flowCode"), o.get("flowName")
        if fc and fn and not isinstance(fc, (dict, list)):
            MODEL_NAME.setdefault(str(fc), str(fn))
        for v in o.values():
            _harvest_names(v)
    elif isinstance(o, list):
        for x in o:
            _harvest_names(x)
_harvest_names(aux)
_harvest_names(aux2)

# Flow graph summaries (from authed flow_details*.jsonl chunks — ALL flow
# types carry aix-format canvasJson since the 2026-09-16 probe). Joined onto
# workflow records so every graph-backed workflow opens the React Flow viewer.
# ALSO joined per-flow (flow_details is richer than the list view):
#   - createTime/updateTime (list view strips BOTH → 0/3,149 had timestamps)
#   - tag ids via flow_tag (637 flows tagged in details, 0 in list view)
#   - per-node prompts (targetInfoList.des + text-node payloads — the latent
#     ~7.3M-char prompt corpus surfaced as record-level `prompt`)
#   - model codes (node data.flowCode: gpt-image-2 / tap_03 / main_image …)
flow_graphs = {}
import glob as _glob, re as _re
def _chunk_key(p):
    m = _re.search(r"_(\d+)\.jsonl$", p)
    return (int(m.group(1)) if m else 0, p)   # numeric order: _2 < _10

def _story_prompts(nodes):
    """Consolidated storyboard/screenplay text (audit round2-c P1 — ~1MB of
    shotPrompt/sceneDesc/dialogue/sound/light text + full screenplays were
    captured in scriptGen + shot-bearing nodes but never surfaced). One
    consolidated entry per story node keeps record.prompt compact."""
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

def _graph_prompts(nodes):
    """per-node prompt texts (des-bearing targetInfoList + text-node fields),
    longest first. Individual texts capped at 4000 chars (pathological graphs
    carry whole screenplays); promptChars carries the true total."""
    out = []
    for n in nodes:
        label = n.get("label") or (n.get("data") or {}).get("label")
        d = n.get("data") or {}
        for k in ("text", "value", "content", "defValue"):
            v = d.get(k)
            if isinstance(v, str) and len(v) > 25:
                out.append({"field": label or k, "text": v}); break
        for ti in ((n.get("target") or {}).get("targetInfoList") or []):
            v = ti.get("des") or ti.get("text")
            if isinstance(v, str) and len(v) > 25:
                out.append({"field": label or "des", "text": v})
        # FLAT target.des (audit round2-e P1): 2,532 input/text nodes carry
        # ~2.2M chars of prompt text ONLY on the flat field (no TIL entry,
        # or a different text than the TIL one) — text-dedup absorbs the
        # 9,148 nodes where flat == TIL entry.
        fd = (n.get("target") or {}).get("des")
        if isinstance(fd, str) and len(fd) > 25:
            out.append({"field": label or "des", "text": fd})
    out.sort(key=lambda p: -len(p["text"]))
    total = sum(len(p["text"]) for p in out)
    # story corpus rides FIRST (before the per-prompt 4000 cap re-truncates)
    story = _story_prompts(nodes)
    for p in out:   # cap individual texts (screenplay-class nodes exist)
        if len(p["text"]) > 4000:
            p["text"] = p["text"][:4000] + f" … [{len(p['text'])-4000} chars truncated]"
    # dedupe by TEXT — same prompt in paramList AND oldParamList, or the
    # same des repeated across nodes (51 workflows carried duplicate entries)
    seen_pt = set()
    deduped = [p for p in out if p["text"] not in seen_pt and not seen_pt.add(p["text"])]
    # story entries ride first, then the longest per-node prompts
    merged = story + deduped
    return (merged[:12], len(merged), sum(len(p["text"]) for p in merged))

for _fp in sorted(_glob.glob(f"{SRC}/auth/flow_details*.jsonl"), key=_chunk_key):
    with open(_fp) as _f:
        for _l in _f:
            try:
                _r = json.loads(_l)
            except Exception:
                continue
            _fid = str(_r.get("__flowId") or "")
            if not _fid or _fid in flow_graphs: continue
            try:
                _g = json.loads(_r.get("canvasJson") or "null") or {}
            except Exception:
                continue
            _nodes = _g.get("nodes") or []
            _prompts, _pcount, _pchars = _graph_prompts(_nodes)
            _mc = collections.Counter(
                str((n.get("data") or {}).get("flowCode"))
                for n in _nodes if (n.get("data") or {}).get("flowCode"))
            flow_graphs[_fid] = {
                "nodeCount": len(_nodes),
                "edgeCount": len(_g.get("connections") or []),
                "groupCount": len(_g.get("groups") or []),
                "nodeTypes": dict(collections.Counter(str(n.get("type")) for n in _nodes)),
                "prompts": _prompts or None,
                "promptCount": _pcount,
                "promptChars": _pchars,
                "modelCodes": [k for k, _ in _mc.most_common(6)] or None,
                "createTime": _r.get("createTime"),
                "updateTime": _r.get("updateTime"),
                "tagIds": [str(t) for t in ([_r.get("canvasFlowTagInfoId"), _r.get("sonTagId")] if _r.get("sonTagId") else [_r.get("canvasFlowTagInfoId")]) if t] or None,
                "des": _r.get("des"),
            }
print(f"[flows] graph summaries: {len(flow_graphs)} of {len(flows_by_id)} flows")
print(f"[flows] prompt corpus: {sum(v['promptChars'] for v in flow_graphs.values()):,} chars, "
      f"{sum(v['promptCount'] for v in flow_graphs.values()):,} prompt nodes, "
      f"{sum(1 for v in flow_graphs.values() if v['modelCodes']):,} flows with model codes")

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
    # params may live in `paramList` AND/OR `oldParamList` (107/191 records
    # carry both; 6 records have prompt text ONLY in oldParamList) — iterate
    # both, deduped by component+name+defValue.
    seen_items = set()
    plists = (pj.get("paramList") or []) + (pj.get("oldParamList") or [])
    for item in plists:
        c = item.get("component") or ""
        dv = str(item.get("defValue") or "").strip()
        field = item.get("name")
        key = (c, field, dv)
        if key in seen_items:
            continue
        seen_items.add(key)
        if c in PROMPT_COMPS:
            if len(dv) > 1:
                prompts.append({"field": field, "text": dv})
            # desList entries under prompt components are prompt continuations,
            # not instructions (37/103 "prompt-less" generations had full
            # prompts hiding there — audit wave 1 P2)
            for d in (item.get("desList") or []):
                if isinstance(d, str) and len(d) > 8:
                    prompts.append({"field": (field or "") + " · des", "text": d})
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
        elif c not in PROMPT_COMPS:
            for d in (item.get("desList") or []):
                if isinstance(d, str) and len(d) > 8:
                    instructions.append({"field": (field or "") + " · des", "text": d})

    # dedupe by TEXT: desList entries can repeat the defValue text verbatim
    # under a different field label (gen_120144 self-duplicated)
    _seen = set()
    prompts = [p for p in prompts if p["text"] not in _seen and not _seen.add(p["text"])]
    instructions = [p for p in instructions if p["text"] not in _seen and not _seen.add(p["text"])]

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
                     "kind": "canvas work" if str((flow or {}).get("type")) == "2" else "node canvas"} if flow else None,
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
    cm = gallery_comments.get(str(r["id"])) or {}
    comments = None
    if cm.get("comment"):
        comments = [{"user": c.get("nickName"), "content": c.get("content"), "at": c.get("createTime")}
                    for c in cm["comment"] if c.get("content")][:5]
    stats = {"likes": num(r.get("goodNum")) or 0, "collects": num(r.get("collectNum")) or 0}
    if num(cm.get("total")):
        stats["comments"] = num(cm.get("total"))
    return {
        "id": f"show_{r['id']}", "sourceId": r["id"], "type": "showcase",
        "title": r.get("name"), "description": r.get("des"),
        "media": {
            "kind": kind, "url": r.get("workFileKey") or r.get("coverFileKey"),
            "thumb": r.get("coverFileKey") or r.get("thumbnailUrl"),
            "width": num(r.get("width")), "height": num(r.get("height")), "bytes": num(r.get("fileSize")),
        },
        "tags": list(dict.fromkeys([t for t in (r.get("parentTagNames") or []) if t]
                                   + [t for t in (r.get("sonTagNames") or []) if t])),
        "creator": creator_of(r.get("userId"), r.get("nickName"), r.get("avatarUrl")),
        "stats": stats,
        "comments": comments,
        "timestamps": {"created": r.get("createTime"), "updated": r.get("updateTime")},
        "canvasRef": r.get("canvasJsonInfoId") or None,  # JWT-phase join key
    }

showcases = [parse_showcase(r) for r in load("gallery")]

# ---------- 3. WORKFLOWS (flows) ----------
# Flow type labels. NOTE (2026-09-16 audit): ALL flow payloads are the site's
# OWN node-canvas format (nodes/connections/groups React-Flow-like JSON) —
# there is ZERO ComfyUI-format JSON anywhere in canvas-flow/details (the
# "ComfyUI" naming came from the site's /apinew/comfy/* API namespace).
# Type 1 (2,756 flows) is the workflow-template library the site brands as
# "ComfyUI Workflow" — relabeled "node canvas" to describe the actual data.
FLOW_TYPE = {"1": "node canvas", "2": "canvas work", "3": "draft", "4": "tool", "None": "other"}
def parse_flow(r):
    tags = []
    tid = str(r.get("canvasFlowTagInfoId") or "")
    if tid and tid in flow_tag: tags.append(flow_tag[tid])
    sid = str(r.get("sonTagId") or "")
    if sid and sid in flow_tag: tags.append(flow_tag[sid])
    # graph join (flow_details is richer than the list view)
    fg = flow_graphs.get(str(r["id"]))
    if fg:
        # tags: prefer the details view (list view strips tag ids entirely)
        det_tags = [flow_tag.get(t) for t in (fg.get("tagIds") or [])]
        det_tags = [t for t in det_tags if t]
        if det_tags: tags = det_tags
        # order-preserving dedupe
        seen_t = set(); tags = [t for t in tags if not (t in seen_t or seen_t.add(t))]
    return {
        "id": f"flow_{r['id']}", "sourceId": r["id"], "type": "workflow",
        "title": r.get("name") or f"Workflow {r['id']}",
        "description": (fg or {}).get("des") or r.get("des"),
        "workflowKind": FLOW_TYPE.get(str(r.get("type")), "other"),
        "media": {
            "kind": media_kind_from_url(r.get("findUrl")) or "image",
            "url": r.get("findUrl"), "thumb": r.get("thumbnailUrl") or r.get("findUrl"),
            "width": num(r.get("width")), "height": num(r.get("height")), "bytes": num(r.get("fileSize")),
        },
        "tags": tags or None,
        "creator": creator_of(r.get("createUserId"), r.get("nickName")),
        "timestamps": {"created": (fg or {}).get("createTime") or r.get("createTime"),
                        "updated": (fg or {}).get("updateTime") or r.get("updateTime")},
        # per-node prompts extracted from the graph (~2.3KB avg per workflow;
        # full des-corpus searchable via `q` once embedded here)
        "prompt": (fg or {}).get("prompts"),
        "promptCount": (fg or {}).get("promptCount") or 0,
        "modelCodes": (fg or {}).get("modelCodes"),
        # any flow with a captured graph (type-2 canvas AND type-1 comfy alike)
        # opens the React Flow viewer via /api/graph?source=flow
        "canvasRef": str(r["id"]) if str(r["id"]) in flow_graphs else None,
        "graph": ({k: v for k, v in flow_graphs[str(r["id"])].items()
                   if k in ("nodeCount", "edgeCount", "groupCount", "nodeTypes")}
                  if str(r["id"]) in flow_graphs else None),
        "graphAvailable": str(r["id"]) in flow_graphs,
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
    # media kind from the URL (audit round2-c P1: 481 mp4 + 2 audio assets
    # were hardcoded kind "image" → broken detail media + no video facets)
    kind = media_kind_from_url(r.get("findUrl")) or media_kind_from_url(r.get("ossUrl")) or "image"
    return {
        "id": f"asset_{r['id']}", "sourceId": r["id"], "type": "asset",
        "title": r.get("name") or f"Asset {r['id']}",
        "description": r.get("des") or None,
        "media": {
            "kind": kind, "url": r.get("findUrl"), "thumb": r.get("thumbnailUrl") or r.get("findUrl"),
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
            "modelCode": facet([r for r in workflows if r.get("modelCodes")], lambda r: r["modelCodes"][0]),
            "hasPrompt": {"yes": sum(1 for r in workflows if r.get("prompt")), "no": sum(1 for r in workflows if not r.get("prompt"))},
        },
        "style_materials": {"category": facet(style_materials, lambda r: r["category"])},
        # Assets tab (7,517 records — the largest tab) had ZERO facets
        "assets": {
            "mediaKind": facet(assets, lambda r: r["media"]["kind"]),
        },
    },
    "canvasQueue": {
        "showcases": sum(1 for r in showcases if r.get("canvasRef")),
        "workflows": sum(1 for r in workflows if r.get("canvasRef")),
        "note": "canvas graph JSON requires JWT (phase 4)",
    },
    # Data-derived date (max record createTime): byte-stable across rebuilds
    # of the same inputs, so the GHA conditional-commit skip actually works.
    # datetime.today() churned daily and forced empty commits.
    "generatedAt": max(
        (str(r.get("timestamps", {}).get("created") or "") for coll in
         (generations, showcases, workflows, style_materials, assets) for r in coll),
        default=""
    )[:10] or "unknown",
    "sourceSite": "aix.studio",
    # modelCode -> product-name labels for the Workflows modelCode facet
    # (auth builder MERGES its attr labels into this dict — do not replace)
    "filterLabels": {f"modelCode:{code}": name for code, name in MODEL_NAME.items()},
}
json.dump(index, open(f"{DST}/index.json", "w"), ensure_ascii=False, indent=1)
print("\nindex:", json.dumps(index["counts"], indent=1))
print("generation facets:", json.dumps(index["facets"]["generations"], ensure_ascii=False, indent=1)[:600])
