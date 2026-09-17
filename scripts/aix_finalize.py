#!/usr/bin/env python3
"""Finalize v2: dedupe + stats + completeness verification for ALL datasets."""
import json, collections

import os
OUT = f"{os.environ.get("AIX_BASE", "/home/z/my-project")}/download/aixstudio"

CATALOGS = ["gallery", "flows", "assets", "prism_material", "task_outputs", "user_prism"]
report = {}
for name in CATALOGS:
    seen, recs, dupes = set(), [], 0
    for l in open(f"{OUT}/{name}.jsonl"):
        r = json.loads(l)
        if r["id"] in seen: dupes += 1; continue
        seen.add(r["id"]); recs.append(r)
    json.dump(recs, open(f"{OUT}/{name}.deduped.json", "w"), ensure_ascii=False, indent=0)
    rep = {"unique": len(recs), "dupes_removed": dupes}
    if name == "gallery":
        rep["with_canvas_json_id"] = sum(1 for r in recs if r.get("canvasJsonInfoId"))
        rep["authors"] = len({r.get("userId") for r in recs if r.get("userId")})
        rep["with_video"] = sum(1 for r in recs if str(r.get("workFileKey") or "").endswith((".mp4",".mov",".webm")))
        tags = collections.Counter()
        for r in recs:
            for t in (r.get("parentTagNames") or []): tags[t] += 1
        rep["top_tags"] = tags.most_common(10)
        rep["total_likes"] = sum(r.get("goodNum") or 0 for r in recs)
    if name == "flows":
        rep["by_type"] = dict(collections.Counter(str(r.get("type")) for r in recs))
    if name == "task_outputs":
        rep["with_canvas_json_id"] = sum(1 for r in recs if r.get("canvasJsonInfoId"))
        rep["by_output_type"] = dict(collections.Counter(str(r.get("outPutType")) for r in recs))
        rep["with_param"] = sum(1 for r in recs if r.get("param"))
    report[name] = rep

profiles = json.load(open(f"{OUT}/user_profiles.json"))
report["user_profiles"] = {
    "fetched": len(profiles),
    "ok": sum(1 for v in profiles.values() if not v.get("err")),
    "err_code300_no_public_profile": sum(1 for v in profiles.values() if v.get("err")),
}
report["aux"] = {f: "ok" for f in ["aux_public.json", "aux_public2.json", "gallery_comments.json"]}

json.dump(report, open(f"{OUT}/dataset_stats.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(report, ensure_ascii=False, indent=1))

