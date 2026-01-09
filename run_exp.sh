for f in dataset/*.json; do
    id=$(basename "$f" .json)
    echo "Running task $id ..."
    ./pipeline/generate.py "$id"  > "log/output-gemini-3-cot-iter4-${id}.log" 2>&1
done

# for id in 158326 164436 58523; do
#     echo "Running task $id ..."
#     ./pipeline/generate.py "$id" -f > "log/output-qwen3-cot-iter4-${id}.log" 2>&1
# done