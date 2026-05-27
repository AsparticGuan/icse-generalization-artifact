#!/usr/bin/env bash
set -euo pipefail

# dataset/ 中的全部 issue（共 43 个）
ISSUES=(
  58523
  59393
  60167
  63749
  64859
  65863
  69803
  73446
  73622
  73904
  76128
  76623
  85250
  85265
  85313
  88348
  92538
  94170
  94737
  98800
  105632
  117436
  118106
  118155
  118211
  122235
  122388
  123175
  133344
  135557
  141753
  142497
  142593
  142674
  142711
  152948
  154238
  156898
  157113
  157315
  157371
  157524
  158326
)

# 并行度：可通过环境变量覆盖，例如 ISSUE_WORKERS=6 ./run_exp.sh
ISSUE_WORKERS="${ISSUE_WORKERS:-4}"

# 额外参数：可透传给 agent/run.py，例如：
#   ./run_exp.sh --model qwen3.5-plus --retest --retest-force -f
EXTRA_ARGS=("$@")

issues_csv="$(IFS=,; echo "${ISSUES[*]}")"
echo "Running ${#ISSUES[@]} issues with agent/run.py"
echo "Issues: ${issues_csv}"
echo "Issue workers: ${ISSUE_WORKERS}"

export no_proxy='127.0.0.1,localhost' && python -W ignore::DeprecationWarning agent/run.py "${issues_csv}" --issue-workers "${ISSUE_WORKERS}" "${EXTRA_ARGS[@]}"

echo "Done."
