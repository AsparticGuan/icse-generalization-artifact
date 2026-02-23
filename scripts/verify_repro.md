# verify_repro.py 说明

## 1. 功能定位
`verify_repro.py` 是数据集样本的质量验证脚本，确保每个样本满足：
1. 在修复前可复现（repro）；
2. 在应用修复后可通过验证（fix）；
3. 相关 lit 测试目录通过。

通过后会写入 `verified=true`。

---

## 2. 两阶段验证流程

### Stage 1（修复前）
- `reset(base_commit)`
- `build()`
- `verify_test_group_orig(repro=True)`

若成功，会把当前优化程序回写到测试项（`current_optimized_program`）。

### Stage 2（修复后）
- `apply(data["patch"])`
- `build()`
- `verify_test_group(repro=False)`
- `verify_lit(...)`

若成功，会回写 `expect_optimized_program` 并标记 `verified=true`。

---

## 3. 输入与输出

### 输入
- `LAB_DATASET_DIR` 下样本 JSON

### 输出
- 原地更新样本 JSON：
  - `verified`
  - `tests[*].tests[*].current_optimized_program`
  - `tests[*].tests[*].expect_optimized_program`

---

## 4. 使用方式

```bash
# 全量验证
python3 scripts/verify_repro.py

# 单样本验证
python3 scripts/verify_repro.py 123456
```

---

## 5. 依赖与关联

### 直接依赖
- `scripts/llvm_helper.py`

### 在文档中的位置
- 根目录 `README.md` 的 Dataset Collection Step 2

---

## 6. 注意事项
1. 该脚本会频繁构建 LLVM，耗时较长。
2. 会修改样本 JSON（写入优化程序与 verified），建议在可追踪环境运行。
3. 某些断言依赖日志和测试名匹配，测试结构异常会触发失败。
