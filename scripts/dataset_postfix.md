# dataset_postfix.py 说明

## 1. 功能定位
`dataset_postfix.py` 是抽取后的**数据修正/后处理脚本**。

它会对每个样本 JSON 进行二次清洗，主要包括：
- 重新计算 hints（行级 + 函数级）；
- 清理 issue 评论噪声；
- 迁移旧字段；
- 补充属性字段（是否单文件/单函数修复）；
- 检查 fix_commit 合法性与重复性。

---

## 2. 核心流程
1. 读入样本 JSON。
2. 过滤无效评论（机器人、`/cherry-pick`）。
3. 基于 `hints.fix_commit` 的 patch 重算：
   - `hints.bug_location_lineno`
   - `hints.bug_location_funcname`
4. 移除旧字段 `hints.files`（兼容迁移）。
5. 计算并写入 `properties`：
   - `is_single_file_fix`
   - `is_single_func_fix`
6. 验证 `fix_commit` 是否满足有效修复规则。
7. 检查是否与其他样本重复使用同一 `fix_commit`。
8. 回写 JSON。

---

## 3. 输入与输出

### 输入
- `LAB_DATASET_DIR` 下的样本 JSON

### 输出
- 原地更新样本 JSON 字段：
  - `hints.*`
  - `properties.*`
  - `issue.comments`

---

## 4. 使用方式

```bash
# 全量后处理
python3 scripts/dataset_postfix.py

# 单样本后处理（issue id）
python3 scripts/dataset_postfix.py 123456
```

---

## 5. 依赖与关联

### 直接依赖
- `scripts/llvm_helper.py`
- `scripts/hints.py`

### 上下游关系
- 上游：`scripts/postfix_extract.py`（生成初始样本）
- 下游：`scripts/dataset_summary.py`、`scripts/verify_repro.py`、pipeline 训练/评测流程

---

## 6. 注意事项
1. 脚本会覆盖原文件，建议配合版本控制运行。
2. `bug_location_funcname` 为空不一定是错误（脚本里对少数历史样本做了白名单）。
3. 重复 `fix_commit` 仅告警，不会自动删除样本。
