for id in 84608 86417 121775 121771 94170 121773 130110 142711 108052 143529 118211 118155 104567; do
    echo "Running task $id ..."
    ./examples/baseline.py "$id" -f > "log/output-qwen3-direct-iter4-${id}.log" 2>&1
done