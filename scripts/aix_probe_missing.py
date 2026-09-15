#!/usr/bin/env python3
"""
Probe the 15 missing endpoints flagged by audit + retry 12 gap canvases.
Transport: agent-browser in-page fetch on aix.studio (public + token header).
Output: download/aixstudio/probe_missing.json (shapes only, small samples).
"""
import json, subprocess, sys

TOKEN = open("/home/z/my-project/scripts/.aix_token").read().strip()

def ab_eval(js, timeout=60):
    r = subprocess.run(["agent-browser", "eval", js], capture_output=True, text=True, timeout=timeout)
    out = r.stdout.strip()
    if not out:
        raise RuntimeError(f"empty output. stderr={r.stderr[:200]}")
    if out.startswith('"') and out.endswith('"'):
        try: out = json.loads(out)
        except Exception: out = out[1:-1]
    return out

def probe(path_qs, use_token=True, method="GET", body=None):
    hdr = "const H={token:'%s'};" % TOKEN if use_token else "const H={};"
    b = json.dumps(body) if body else "null"
    js = f"""(async()=>{{
      {hdr}
      try{{
        const opt={{method:'{method}',headers:H}};
        if({b}) opt.body=JSON.stringify({b});
        const r=await fetch('{path_qs}',opt);
        const j=await r.json();
        const d=j.data;
        let shape=null, size=0;
        if(Array.isArray(d)){{ size=d.length; shape=d.length?Object.keys(d[0]).slice(0,40):[]; }}
        else if(d&&typeof d==='object'){{
          if(Array.isArray(d.records)){{ size=d.records.length; shape={{total:d.total,pages:d.pages,recKeys:d.records.length?Object.keys(d.records[0]).slice(0,40):[]}}; }}
          else {{ shape=Object.keys(d).slice(0,40); size=1; }}
        }}
        return JSON.stringify({{code:j.code,msg:j.message,http:r.status,size,shape}});
      }}catch(e){{ return JSON.stringify({{err:e.message}}); }}
    }})()"""
    return ab_eval(js)

ENDPOINTS = [
    # (label, path+qs, token, method, body)
    ("pub_prompt-info_listAll",      "/apinew/comfy/prompt-info/listAll", False, "GET", None),
    ("pub_prompt-info_listAll_POST", "/apinew/comfy/prompt-info/listAll", False, "POST", {"pageNum":1,"pageSize":10}),
    ("pub_canvas-asset_details",     "/apinew/comfy/canvas-asset/details?id=9328", False, "GET", None),
    ("pub_param-info_detailByCode",  "/apinew/comfy/param-info/detailByCodeNoError?codeNo=test", False, "GET", None),
    ("pub_task-output_getShowUrl",   "/apinew/comfy/task-output-file/getShowUrl?fileKey=test", False, "GET", None),
    ("pub_reply_getAllReplay",       "/apinew/comfy/reply-info/getAllReplayByOutFileId?outFileId=1300", False, "GET", None),
    ("pub_material-tag_listParent",  "/apinew/comfy/material-tag/listParent", False, "GET", None),
    ("pub_tag-info_listPage",        "/apinew/comfy/tag-info/listPage?pageNum=1&current=1&size=10", False, "GET", None),
    ("pub_top-des_listAll",          "/apinew/comfy/top-des-info/listAll", False, "GET", None),
    ("pub_prompt-info_listByCodes",  "/apinew/comfy/prompt-info/listByCodes?codes=1", False, "GET", None),
    ("pub_see-dance_getCheckResult", "/apinew/comfy/see-dance-asset/getCheckResult", False, "GET", None),
    ("gt_canvas-json-info_listPage", "/apinew/comfy/canvas-json-info/listPage?pageNum=1&current=1&size=10", True, "GET", None),
    ("gt_task-output_listPageToCanvas", "/apinew/comfy/task-output-file/listPageToCanvas?pageNum=1&current=1&size=10", True, "GET", None),
    ("gt_c-work-flow_listPage",      "/apinew/comfy/c-work-flow-info/listPage?pageNum=1&current=1&size=10", True, "GET", None),
    ("gt_user-prism_listPageUser",   "/apinew/comfy/user-prism-material-info/listPageUser?pageNum=1&current=1&size=10", True, "GET", None),
    ("gt_c-comfy-server-info",       "/apinew/comfy/c-comfy-server-info", True, "GET", None),
    ("gt_partner-info_getPartnerToHome", "/apinew/comfy/partner-info/getPartnerToHome", True, "GET", None),
    ("gt_task-output_getCountToCanvas", "/apinew/comfy/task-output-file/getCountToCanvas?canvasJsonInfoId=1817", True, "GET", None),
]

results = {}
for label, path, tok, method, body in ENDPOINTS:
    try:
        raw = probe(path, use_token=tok, method=method, body=body)
        results[label] = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        results[label] = {"err": str(e)[:150]}
    print(f"{label:42s} {json.dumps(results[label], ensure_ascii=False)[:220]}")

# retry the 12 gap canvases
GAP_NULL = [10337, 10652, 2875, 3621]
GAP_EMPTY = [2231, 2204, 2132, 2223, 2208, 1845, 1834, 2222]
gap_results = {}
for cid in GAP_NULL + GAP_EMPTY:
    try:
        raw = probe(f"/apinew/comfy/canvas-json-copy-info/detail?id={cid}", use_token=True)
        d = json.loads(raw) if isinstance(raw, str) else raw
        gap_results[str(cid)] = {"code": d.get("code"), "has_data": bool(d.get("shape")), "shape": d.get("shape")}
    except Exception as e:
        gap_results[str(cid)] = {"err": str(e)[:100]}
    print(f"gap canvas {cid}: {json.dumps(gap_results[str(cid)])[:180]}")

json.dump({"endpoints": results, "gap_canvases": gap_results},
          open("/home/z/my-project/download/aixstudio/probe_missing.json", "w"), ensure_ascii=False, indent=1)
print("\nSaved download/aixstudio/probe_missing.json")
