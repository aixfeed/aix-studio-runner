#!/bin/bash
# Visit every public route, capture which apinew endpoints each page fires
set -u
OUT=/home/z/my-project/recon/page_audit
mkdir -p "$OUT"

PAGES=(
  "/arena"
  "/story"
  "/anim"
  "/join"
  "/main"
  "/creation/HomePortal"
  "/creation/creationMain"
  "/creation/showOpen"
  "/creation/goodWork"
  "/creation/goodPrism"
  "/creation/PrismShow"
  "/creation/UserPrism"
  "/creation/UserPrismShow"
  "/creation/WorkFlowListNew2"
  "/creation/WorkFlowListNewNew"
  "/creation/cloudList"
  "/creation/cloudApp"
  "/creation/drawOutputNew"
  "/creation/profilePage"
  "/creation/about"
  "/creation/apiDes"
  "/user/spaceNew"
  "/tutorial"
)

for p in "${PAGES[@]}"; do
  fname=$(echo "$p" | tr '/' '_')
  echo "### $p"
  timeout 40 agent-browser open "https://aix.studio$p" >/dev/null 2>&1
  timeout 30 agent-browser wait --load networkidle >/dev/null 2>&1 || true
  sleep 2
  timeout 30 agent-browser network requests 2>/dev/null | grep -oE 'https://aix\.studio/apinew/[a-zA-Z0-9/?=&_{}$-]+' | sort -u > "$OUT/$fname.txt"
  echo "  $(wc -l < "$OUT/$fname.txt") api calls"
done
echo ALLDONE
