# bisect_stat.py 说明

## 1. 功能定位
`bisect_stat.py` 用于统计并可视化 bisect 结果质量，回答三个问题：
1. 数据集中有多少样本具备有效 bisect 结果；
2. bug 引入、报告、修复之间的时间跨度分布如何；
3. bisect 命中的提交与真实修复是否“相关”（文件级/行级）。

---

## 2. 统计设计

### 2.1 有效样本判定
仅当以下条件同时满足时，样本才计入统计：
- JSON 中存在 `bisect` 字段；
- `git rev-parse <bisect>` 成功；
- 解析结果与原哈希一致。

### 2.2 时间指标
脚本计算三类时间差（单位天）：
- `report_to_fix`：报告 -> 修复
- `bug_to_fix`：引入 -> 修复
- `bug_to_report`：引入 -> 报告

并使用几何平均值（`geomean`）减少极端值影响。

### 2.3 相关性指标
- 文件级相关：bisect 提交与 fix patch 是否触及同一文件。
- 行级相关：规范化后（去标点/空白、过滤短片段）是否存在重叠修改行。

---

## 3. 输出

### 终端输出
- 可用 bisect 比例
- 三种时间差的几何平均
- `bug_to_report` 的 p95
- 文件级/行级定位精度

### 图像输出
- `work/bisect_stat.png`
- 每个时间指标对应“直方图 + ECDF”双轴图

---

## 4. 使用方式

```bash
python3 scripts/bisect_stat.py
```

---

## 5. 依赖与关联

### 直接依赖
- `scripts/llvm_helper.py`
- `unidiff`、`matplotlib`、`dateparser`

### 上游数据来源
- `scripts/bisect_driver.py` 写入的 `bisect` 字段
- 样本 JSON 中的 `patch`、`hints.fix_commit`、`knowledge_cutoff`

---

## 6. 注意事项
1. 脚本遇到时间顺序异常（负值）会把该样本标记为 `Invalid order` 并跳过。
2. 若样本很少，分布图解释意义有限。
3. 需要本地 git 历史可访问对应提交。
