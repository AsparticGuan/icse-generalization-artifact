# dataset_summary.py 说明

## 1. 功能定位
`dataset_summary.py` 用于对数据集做**全局统计体检**，快速回答：
- 样本总量与 `verified` 覆盖率；
- 缺陷类型、组件、标签分布；
- patch 规模与测试规模分布。

适合在数据构建后做健康检查与版本对比。

---

## 2. 核心统计设计

### 2.1 元数据分布
- `bug_type`：missed-optimization / miscompilation / crash / hang
- `components`：来自 `hints.components`
- `labels`：来自 issue 原始标签

### 2.2 patch 规模
- 修改文件数（`hints.bug_location_lineno`）
- 插入/删除行数（解析 `patch`）
- hunk 数

### 2.3 结构化属性
- 单文件修复占比
- 单函数/单 hunk 修复占比

### 2.4 测试与质量
- 每个样本的子测试数量（`tests[*].tests`）
- `verified` 占比

---

## 3. 输入与输出

### 输入
- `LAB_DATASET_DIR` 下所有 `*.json`

### 输出
- 终端文本报告（无额外文件输出）

---

## 4. 使用方式

```bash
python3 scripts/dataset_summary.py
```

---

## 5. 依赖与关联

### 直接依赖
- `scripts/llvm_helper.py`
- `unidiff`

### 上游数据来源
- `scripts/postfix_extract.py`
- `scripts/dataset_postfix.py`
- `scripts/verify_repro.py`

---

## 6. 注意事项
1. 该脚本假设样本结构完整，缺关键字段会抛异常。
2. 统计结果强依赖 `verified` 与 `hints` 字段质量。
3. 输出为一次性快照，建议配合日志保存用于横向对比。
