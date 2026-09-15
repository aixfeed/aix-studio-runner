#!/usr/bin/env python3
"""Delta analysis: v2 (fresh scrape) vs v1 (August snapshot) + rebuild deduped files."""
import json, os, collections

SRC = os.environ.get("AIX_BASE", "/home/z/my-project") + "/download/aixstudio"

def load_unique(name):
    seen, recs = {}, []
    for l in open(f"{SRC}/{name}.jsonl"):
        r = json.loads(l)
        if r["id"] in seen: continue
        seen[r["id"]] = r
    return seen

report = {}
for name in ["gallery", "flows", "assets"]:
    old = {}
    for l in open(f"{SRC}/{name}.jsonl.v1bak"):
        r = json.loads(l)
        old[r["id"]] = r
    new = load_unique(name)

    added_ids = [i for i in new if i not in old]
    removed_ids = [i for i in old if i not in new]
    changed = []
    for i in new:
        if i in old:
            o, n = old[i], new[i]
            diff = {k for k in o if o.get(k) != n.get(k)}
            # ignore version/audit fields noise for the change summary
            meaningful = diff - {"version", "updateTime", "updateUserId", "sort", "sortNum"}
            if diff:
                changed.append({"id": i, "fields": sorted(diff)[:8], "meaningful": sorted(meaningful)[:8]})
    rep = {
        "v1_unique": len(old), "v2_unique": len(new),
        "new": len(added_ids), "removed": len(removed_ids),
        "updated_records": len(changed),
        "updated_fields_freq": dict(collections.Counter(f for c in changed for f in c["fields"]).most_common(10)),
    }
    if name == "gallery" and added_ids:
        rep["new_items"] = [{"id": i, "name": new[i].get("name"), "tags": new[i].get("parentTagNames"),
                             "author": new[i].get("nickName"), "created": new[i].get("createTime"),
                             "hasCanvas": bool(new[i].get("canvasJsonInfoId")),
                             "hasVideo": str(new[i].get("workFileKey") or "").endswith(".mp4")} for i in added_ids[:20]]
    if name == "flows" and added_ids:
        rep["new_items_sample"] = [{"id": i, "name": new[i].get("name"), "type": new[i].get("type"),
                                    "created": new[i].get("createTime")} for i in added_ids[:10]]
    report[name] = rep
    # write fresh deduped jsonl (v2 authoritative)
    with open(f"{SRC}/{name}.deduped.json", "w") as f:
        json.dump(list(new.values()), f, ensure_ascii=False)
    print(f"{name}: v1={len(old)} v2={len(new)} new={len(added_ids)} removed={len(removed_ids)} updated={len(changed)}")

json.dump(report, open(f"{SRC}/delta_report.json", "w"), ensure_ascii=False, indent=1)
print("\ngallery new items:")
for it in report["gallery"].get("new_items", [])[:14]:
    print(f"  [{it['id']}] {str(it['name'])[:30]} | {','.join(it['tags'] or [])} | {it['author']} | canvas={it['hasCanvas']}")
print("\nflows updated-field freq:", report["flows"]["updated_fields_freq"])
print("gallery updated-field freq:", report["gallery"]["updated_fields_freq"])
