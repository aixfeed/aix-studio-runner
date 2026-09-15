#!/usr/bin/env python3
"""Phase 2 enrichment: user profiles + single-shot public datasets."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aix_transport

BASE = os.environ.get("AIX_BASE", "/home/z/my-project")
OUT = f"{BASE}/download/aixstudio"

def ab_eval(js, timeout=240):
    return aix_transport.eval_js(js, timeout=timeout)

# ---- collect unique user ids across all datasets ----
ids = set()
for name in ["gallery", "flows", "assets", "task_outputs", "user_prism"]:
    p = f"{OUT}/{name}.jsonl"
    if not os.path.exists(p): continue
    for l in open(p):
        r = json.loads(l)
        for k in ("userId", "createUserId"):
            v = r.get(k)
            if v and str(v).isdigit(): ids.add(str(v))
print(f"unique user ids: {len(ids)}")

# ---- fetch profiles in batches ----
profiles_path = f"{OUT}/user_profiles.json"
profiles = {}
if os.path.exists(profiles_path):
    profiles = json.load(open(profiles_path))
    print(f"resuming: {len(profiles)} already fetched")
todo = [i for i in sorted(ids) if i not in profiles]
B = 30
for i in range(0, len(todo), B):
    batch = todo[i:i+B]
    bl = json.dumps(batch)
    js = f"""(async()=>{{
      const ids = {bl}; const out = {{}};
      const q = ids.slice();
      async function w(){{
        while(q.length){{
          const id = q.shift();
          try{{
            const r = await fetch('/apinew/comfy/user/'+id+'/profile');
            const j = await r.json();
            out[id] = (j.code===200) ? j.data : {{err: j.code}};
          }}catch(e){{ out[id] = {{err: e.message}}; }}
        }}
      }}
      await Promise.all([w(),w(),w(),w(),w(),w()]);
      return JSON.stringify(out);
    }})()"""
    for attempt in range(3):
        try:
            profiles.update(aix_transport.eval_json(js))
            break
        except Exception as e:
            print(f"profiles batch {i}: retry ({str(e)[:80]})"); time.sleep(3)
    json.dump(profiles, open(profiles_path, "w"), ensure_ascii=False, indent=0)
    print(f"  profiles {i+len(batch)}/{len(todo)}")
    time.sleep(0.5)

# ---- single-shot datasets ----
singles = {
    "cloud_cards": "/apinew/comfy/cloud-card-info/listAll",
    "cloud_cards_local": "/apinew/comfy/cloud-card-info/listAllLocal",
    "prism_tags": "/apinew/comfy/prism-tag/listAll",
    "task_output_hosts": "/apinew/comfy/task-output-file/openHostList",
    "card_classify": "/apinew/comfy/card-classify/getAllCardList",
    "announcements_latest": "/apinew/comfy/system-announcements/latest",
    "cs_bot_config": "/apinew/comfy/cs-bot/config",
    "param_about_phone": "/apinew/comfy/param-info/detailByCode?code=about_phone",
    "param_about_wx": "/apinew/comfy/param-info/detailByCode?code=about_wx_user",
    "param_img_card": "/apinew/comfy/param-info/detailByCode?code=img_card_code",
    "workflow_main_image": "/apinew/comfy/c-work-flow-info/detailByCode/main_image",
    "workflow_tap03": "/apinew/comfy/c-work-flow-info/detailByCode/tap_03",
}
sdata = {}
for name, path in singles.items():
    for attempt in range(3):
        try:
            js = f"(async()=>JSON.stringify(await (await fetch('{path}')).json()))()"
            sdata[name] = aix_transport.eval_json(js)
            print(f"{name}: code {sdata[name].get('code')}")
            break
        except Exception as e:
            print(f"{name}: retry ({str(e)[:60]})"); time.sleep(2)
json.dump(sdata, open(f"{OUT}/aux_public2.json", "w"), ensure_ascii=False, indent=1)
ok = sum(1 for v in profiles.values() if not v.get("err"))
print(f"profiles fetched OK: {ok}/{len(profiles)}")
print("DONE")
