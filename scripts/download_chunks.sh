#!/bin/bash
# Download all Nuxt chunks from aix.studio for endpoint analysis
set -u
OUT=/home/z/my-project/recon/chunks
mkdir -p "$OUT"
cd "$OUT"

UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

dl_one() {
  f="$1"
  [ -s "$f" ] && return 0
  for i in 1 2 3 4 5 6; do
    curl -sS --tlsv1.2 --tls-max 1.2 --max-time 40 -o "$f" \
      -A "$UA" -H "Referer: https://aix.studio/" \
      "https://aix.studio/_nuxt/$f" 2>/dev/null
    if [ -s "$f" ] && ! grep -q "Cannot GET" "$f" 2>/dev/null; then
      echo "OK $f ($(wc -c < "$f")B)"
      return 0
    fi
    sleep $((i))
  done
  echo "FAIL $f"
  return 1
}
export -f dl_one
export UA

# get chunk list from network log dump file
cat /home/z/my-project/recon/chunklist.txt | xargs -P 4 -I {} bash -c 'dl_one "$@"' _ {}
echo "=== DONE. Total files: $(ls -1 "$OUT" | wc -l), failures: $(ls -1 "$OUT" | xargs -I{} sh -c '[ -s "{}" ] || echo empty' | wc -l)"
