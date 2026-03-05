# submit.py 说明

## 1. 功能定位
`submit.py` 用于将修复结果目录打包成统一提交 JSON，便于提交到外部评测/记录系统。

---

## 2. 核心流程
1. 从环境变量读取方法与基座模型元信息。
2. 遍历 `fix_dir` 中的 `*.json` 修复证据。
3. 对每个文件先读取数据集同名样本（`LAB_DATASET_DIR`）：
   - 仅保留 `verified=true` 样本。
4. 将修复证据补齐 `bug_id`、`bug_type`。
5. 检查是否使用过 hint（通过 `knowledge` 字段）。
6. 输出统一结构：
   - `method_*`
   - `base_model_*`
   - `with_hint`
   - `fixes`

---

## 3. 输入输出

### 输入
- 参数 1：`fix_dir`
- 参数 2：`output_file`
- 环境变量：
  - `LAB_METHOD_NAME` / `LAB_METHOD_URL`
  - `LAB_BASE_MODEL_NAME` / `LAB_BASE_MODEL_URL`

### 输出
- 聚合提交 JSON（`output_file`）

---

## 4. 使用方式

```bash
python3 scripts/submit.py <fix_dir> <submission.json>
```

---

## 5. 依赖与关联

### 直接依赖
- `scripts/llvm_helper.py`（用于定位数据集目录）

### 关联脚本
- 可配合 `scripts/unpack.py` 做反向拆分

---

## 6. 注意事项
1. `fix_dir` 与数据集目录文件名需可对齐（通常都是 `<issue_id>.json`）。
2. 非 verified 样本会被自动忽略。
3. 不会校验证据字段完整性，只做聚合与元信息补齐。
