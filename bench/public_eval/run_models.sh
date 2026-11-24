#!/usr/bin/env bash
# Ingest each public benchmark with each candidate model (unchanged pipeline, only --model differs) and evaluate.
# usage: run_models.sh BASE_DIR MODEL [MODEL ...]     (MODEL = <arch>/<pretrained>)
set -u
B=$1; shift
cd "$(dirname "$0")/../.."
export PYTHONIOENCODING=utf-8
for M in "$@"; do
  slug=$(echo "$M" | tr '/' '_')
  for ds in flickr coco msrvtt; do
    out=$B/models/$slug
    mkdir -p "$out"
    [ -f "$out/$ds.json" ] && continue
    for batch in 64 16; do
      if .venv/Scripts/roamingest run "$B/media/$ds" --out-dir "$out/lib_$ds" --storage ssd --quiet --model "$M" --batch $batch \
           > "$out/ingest_$ds.log" 2>&1; then break; fi
      echo "$M $ds: ingest failed with batch $batch"; rm -rf "$out/lib_$ds"
    done
    .venv/Scripts/python -c "import json,sys;r=json.load(open(sys.argv[1]));print(sys.argv[2],sys.argv[3],'ingest',r['wall_s'],'s')" \
      "$out/lib_$ds/report.json" "$M" "$ds"
    .venv/Scripts/python -I bench/public_eval/evaluate_fast.py "$ds" "$out/lib_$ds" "$B/${ds}_queries.json" \
      --out "$out/$ds.json" > "$out/eval_$ds.log" 2>&1 || echo "$M $ds: eval failed"
    .venv/Scripts/python -c "import json,sys;r=json.load(open(sys.argv[1]));print('  R@1',round(r['product']['R@1'],4),'R@10',round(r['product']['R@10'],4),'lat p50',round(r['latency_ms_cpu']['p50']),'ms')" "$out/$ds.json"
  done
done
