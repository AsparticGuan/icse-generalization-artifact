# funcname_agg.py 说明

## 1. 功能定位
`funcname_agg.py` 用于统计数据集中函数级 hints 的高频函数名，帮助识别：
- 高频问题函数；
- 可能的优化热点或脆弱点。

---

## 2. 核心流程
1. 遍历 `LAB_DATASET_DIR` 下所有样本 JSON。
2. 读取 `hints.bug_location_funcname`。
3. 过滤：
   - 跳过包含 `::` 的命名空间/成员函数名；
   - 调用 `llvm_helper.is_interesting_funcname` 过滤泛化名称。
4. 聚合计数并按出现次数降序输出。

---

## 3. 输入与输出

### 输入
- 样本 JSON 中的函数级定位字段

### 输出
- 标准输出：`<funcname> <count>`

---

## 4. 使用方式

```bash
python3 scripts/funcname_agg.py
```

---

## 5. 依赖与关联

### 直接依赖
- `scripts/llvm_helper.py`

### 上游依赖字段来源
- `scripts/postfix_extract.py`
- `scripts/dataset_postfix.py`

---

## 6. 注意事项
1. 该脚本依赖 `llvm_helper.is_interesting_funcname`，若 helper 未提供该函数会报错。
2. 过滤 `::` 会丢失部分类成员函数信息，适合做“粗粒度热点”分析。
3. 结果是频次统计，不代表因果关系或修复难度。
