#!/usr/bin/env bash
set -euo pipefail

ISSUES=(
  118164
  132908
  134318
  138636
  144020
  157111
  163093
  169763
  184131
  186509
  186553
  186554
  186957
  198082
  199806
  200661
  56562
  61644
  72651
  72773
)

# 并行度：可通过环境变量覆盖，例如 ISSUE_WORKERS=6 ./run_exp.sh
ISSUE_WORKERS="${ISSUE_WORKERS:-1}"

# 额外参数：可透传给 agent/run.py，例如：
#   ./run_exp.sh --model qwen3.5-plus --retest --retest-force -f
EXTRA_ARGS=("$@")

issues_csv="$(IFS=,; echo "${ISSUES[*]}")"
echo "Running ${#ISSUES[@]} issues with agent/run.py"
echo "Issues: ${issues_csv}"
echo "Issue workers: ${ISSUE_WORKERS}"

export no_proxy='127.0.0.1,localhost' && python -W ignore::DeprecationWarning agent/run_real.py "${issues_csv}" --issue-workers "${ISSUE_WORKERS}" "${EXTRA_ARGS[@]}"

echo "Done."
