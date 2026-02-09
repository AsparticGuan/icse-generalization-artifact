#!/usr/bin/env bash
# for f in dataset/*.json; do
#     id=$(basename "$f" .json)
#     echo "Running task $id ..."
#     ./pipeline/generate.py "$id"  > "log/output-gemini-3-cot-iter4-${id}.log" 2>&1
# done

# for id in 58342 59393 57741 145875 107228 157315 121701 63749 65968 62155 \
#          156898 142674 76128 123175 88348 85265 85313 73904 77108 129947 \
#          110919 60818 133367 82414 134024 66417 78038 154238 64859 85823 \
#          67916 146263 57831 72653; do
#     echo "Running task $id ..."
#     ./pipeline/generate.py "$id" > "log/output-gemini-3-cot-iter4-${id}.log" 2>&1
# done

NPROC=4
MAX_JOBS=${MAX_JOBS:-$NPROC}
for f in dataset/*-orig.json; do
    id=$(basename "$f" .json)
    echo "Running task $id ..."
    ./pipeline/generate_orig.py "$id" > "log/gemini/output-orig-gemini-3-cot-iter4-${id}.log" 2>&1 &
    while [ "$(jobs -rp | wc -l)" -ge "$MAX_JOBS" ]; do
        wait -n
    done
done
wait