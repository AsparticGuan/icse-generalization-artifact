# extract_from_issues.py 说明

## 1. 功能定位
`extract_from_issues.py` 是数据集构建入口的**批量抓取驱动器**。

它会在指定 issue id 区间内循环：
1. 请求 GitHub issue；
2. 做状态/标签快速筛选；
3. 调用 `postfix_extract.py` 进行深度抽取；
4. 使用缓存与限流等待机制保证长任务稳定运行。

---

## 2. 核心设计

### 2.1 两级过滤
- 轻量过滤：状态、是否 PR、标签黑白名单。
- 深度过滤：交给 `postfix_extract.py` 做 fix commit、patch、测试可用性判定。

### 2.2 缓存策略
- `LAB_ISSUE_CACHE/<issue_id>` 作为哨兵文件。
- 命中缓存时跳过，避免重复请求 GitHub API。

### 2.3 速率限制处理
- 通过 `/rate_limit` 查询剩余额度。
- 额度耗尽时等待到 reset 时间窗。

---

## 3. 输入与输出

### 输入
- 环境变量：
  - `LAB_GITHUB_TOKEN`
  - `LAB_ISSUE_CACHE`
  - `LAB_DATASET_DIR`（由 `llvm_helper` 使用）

### 输出
- 抽取成功的样本 JSON（写入数据集目录）
- 缓存哨兵文件（表示 issue 已处理）
- 终端进度日志

---

## 4. 使用方式

```bash
python3 scripts/extract_from_issues.py
```

---

## 5. 依赖与关联

### 直接依赖
- `scripts/llvm_helper.py`
- `scripts/postfix_extract.py`
- `requests`、`tqdm`

### 被谁引用
- `README.md` 的数据收集步骤

---

## 6. 注意事项
1. 需要有效 GitHub Token，否则会频繁触发限流或请求失败。
2. 标签规则较严格，调整规则会显著改变样本覆盖。
3. 本脚本只负责“批处理与调度”，样本内容质量主要由 `postfix_extract.py` 决定。
