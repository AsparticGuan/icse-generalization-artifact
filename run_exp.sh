# # for id in 84608 86417 121775 121771 94170 121773 130110 142711 108052 143529 118211 118155 104567; do
# #     echo "Running task $id ..."
# #     ./examples/baseline.py "$id" > "log/output-qwen3-cot-iter4-${id}.log" 2>&1
# # done

# for id in 97336 122235 142744 87854 143529 122388 118155 147176; do
#     echo "Running task $id ..."
#     ./examples/baseline.py "$id" > "log/output-qwen3-cot-iter4-${id}.log" 2>&1
# done

# for f in dataset/*.json; do
#     id=$(basename "$f" .json)
#     echo "Running task $id ..."
#     ./examples/baseline.py "$id" > "log/output-qwen3-cot-iter4-${id}.log" 2>&1
# done


for id in 84608 86417 121775 121771 94170 121773 142711 108052 143529 118211 118155 104567; do
    echo "Running task $id ..."
    ./examples/single-func.py "$id" > "log/output-qwen3-cot-iter4-${id}.log" 2>&1
done