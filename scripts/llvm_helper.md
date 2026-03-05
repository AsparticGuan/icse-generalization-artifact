# llvm_helper.py 说明

## 1. 功能定位
`llvm_helper.py` 是 scripts 目录的**基础设施模块**，封装了：
- git 操作
- LLVM 构建
- patch 应用
- Alive2/FileCheck/lli/cost 验证
- lit 测试执行

多数脚本都会 import 它。

---

## 2. 主要能力分组

### 2.1 仓库与环境
- `git_execute()`：统一 git 调用
- `reset()`：重置到指定提交
- 环境变量：`LAB_LLVM_DIR`、`LAB_LLVM_BUILD_DIR`、`LAB_DATASET_DIR` 等

### 2.2 构建与测试
- `build()`：cmake + ninja
- `verify_lit()`：运行 llvm-lit 子目录测试

### 2.3 语义/行为验证
- `alive2_check()`：语义等价验证
- `filecheck_check()`：FileCheck 断言验证
- `lli_check()`：运行时输出验证
- `cost_check()`：优化代价比较

### 2.4 样本辅助
- `infer_related_components()`：推断组件
- `is_valid_comment()`：评论过滤
- `is_valid_fix()`：修复提交合法性校验
- `get_first_failed_test()`：抓取首个失败测试

---

## 3. 验证流程入口

### 标准样本
- `verify_test_group()` + `verify_dispatch()`
- 适用于带 RUN/FileCheck 的测试结构

### dataset-orig 样本
- `verify_test_group_orig()`
- 针对 commands 为空场景，仅用 alive2 + cost

---

## 4. 使用方式（示例）

```python
import llvm_helper

llvm_helper.reset("<commit>")
ok, build_log = llvm_helper.build(max_build_jobs=32)
if ok:
    ok2, test_log = llvm_helper.verify_test_group(False, tests, "missed-optimization")
```

---

## 5. 关联关系
- 被 scripts 下大部分脚本调用（extract、verify、summary、submit 等）
- 被 pipeline 与 examples 侧通过 `sys.path` 引入

---

## 6. 注意事项
1. 强依赖外部环境变量，未初始化会直接报错。
2. 默认会操作真实 LLVM 工作树（reset/checkout/apply），务必在隔离环境运行。
3. `cost_check`、`alive2_check` 等工具依赖外部二进制存在并可执行。
