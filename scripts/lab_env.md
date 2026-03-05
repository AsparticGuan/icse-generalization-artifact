# lab_env.py 说明

## 1. 功能定位
`lab_env.py` 提供 `Environment` 类，把“单个 issue 样本”的运行状态与操作封装在一起。

它是 pipeline/baseline 的统一运行入口，负责：
- 数据读取与状态管理；
- 构建、快速检查、完整检查；
- hints 与知识使用记录访问；
- 运行统计与日志导出。

---

## 2. 核心设计

### 2.1 TimeCompensationGuard
上下文管理器，用来统计“交互等待时间”（例如构建/测试外部耗时），并在 `dump()` 的 `wall_time` 中扣除，提升评测公平性。

### 2.2 Environment 的状态字段
- 样本元信息：`base_commit`、`bug_type`、`test_commit`、`knowledge_cutoff`
- 统计字段：`build_count`、`build_failure_count`、`fast_check_count`、`full_check_count`
- 结果字段：`fast_check_pass`、`full_check_pass`
- 日志字段：`check_fast_log`、`check_full_log`

### 2.3 检查方法
- `check_fast()`：`build + verify_test_group`
- `check_full()`：`build + verify_test_group + verify_lit`

---

## 3. 常用接口
- `reset()`：重置到 `base_commit`
- `build()`：构建 LLVM
- `dump()`：导出当前快照（patch、日志、统计）
- `get_hint_*()`：按知识边界读取 hints
- `get_langref_desc()`：基于关键字获取 LangRef 片段

---

## 4. 使用示例

```python
from lab_env import Environment

env = Environment(issue_id="123456", base_model_knowledge_cutoff="2024-01-01")
env.reset()
ok, log = env.check_fast()
print(ok)
print(env.dump())
```

---

## 5. 依赖与关联

### 直接依赖
- `scripts/llvm_helper.py`
- `dateparser`

### 被谁使用
- `examples/baseline.py`
- `pipeline/generate.py`
- `pipeline/generate_orig.py`
- `pipeline/localize.py`
- `pipeline/generalize.py`
- `pipeline/retest_patches.py`

---

## 6. 注意事项
1. `verify_head()` 会强校验当前仓库 HEAD，防止在错误提交上跑评估。
2. `use_knowledge()` 会检查时间边界，超出 `knowledge_cutoff` 会抛错。
3. `max_test_jobs` 默认跟随 `max_build_jobs` 参数行为（代码里保持既有实现）。
