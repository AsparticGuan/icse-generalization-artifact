# bisect_driver.py 说明

## 1. 功能定位
`bisect_driver.py` 是**批量 bisect 调度器**：
- 以数据集中的 issue JSON 为输入；
- 调用 `git bisect` 在提交历史中定位 first bad commit；
- 将定位结果写回样本文件的 `bisect` 字段。

它和 `bisect_runner.py` 配套使用：
- `bisect_driver.py` 负责“区间搜索 + bisect 控制”；
- `bisect_runner.py` 负责“单个提交是 GOOD/BAD/SKIP 的判定”。

---

## 2. 核心设计

### 2.1 BAD/GOOD 端点策略
- BAD 端点默认取 `data["base_commit"]`。
- GOOD 端点不是固定值，而是通过**指数回退**（offset 逐步增大）在历史中寻找第一个可判定 GOOD 的提交，减少线性探测成本。

### 2.2 knowledge cutoff 保护
当 `knowledge_cutoff < base_commit_time` 时，脚本会尝试把 BAD 端点调整到“报告时间之前最近的提交”，避免把未来信息带入定位区间。

### 2.3 异常/不确定结果处理
- bisect 超时：写入 `"Timeout"`。
- bisect 给出多个候选提交：使用 `is_llvm_functional_change` 过滤仅修改 `llvm/lib` 或 `llvm/include` 的候选。
- 若无法明确：写入 `"Unrelated"` 或错误信息。

---

## 3. 输入与输出

### 输入
- 数据目录：`LAB_DATASET_DIR`（来自 `scripts/llvm_helper.py`）
- issue 样本 JSON（含 `base_commit`、`tests`、`knowledge_cutoff` 等）

### 输出
- 原地更新样本 JSON：新增/覆盖 `bisect` 字段

---

## 4. 使用方式

```bash
# 全量处理数据集
python3 scripts/bisect_driver.py

# 只处理单个 issue（例如 123456）
python3 scripts/bisect_driver.py 123456
```

---

## 5. 依赖与关联

### 直接依赖
- `scripts/llvm_helper.py`
- `scripts/bisect_runner.py`

### 结果被谁消费
- `scripts/bisect_stat.py`：统计 bisect 质量与时间跨度
- `scripts/lab_env.py` (`get_bisect_commit`)：运行时读取已定位提交

---

## 6. 注意事项
1. `hang` 类型样本当前直接跳过（未支持）。
2. 该脚本会对样本 JSON 落盘，建议在干净分支/有版本控制的目录运行。
3. 依赖 git 历史完整性，浅克隆可能导致定位失败。
