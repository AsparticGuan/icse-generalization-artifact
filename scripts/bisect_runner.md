# bisect_runner.py 说明

## 1. 功能定位
`bisect_runner.py` 是给 `git bisect run` 使用的**单提交判定器**。

给定一个 commit 和一个 issue 样本，它会返回：
- `0` (`GOOD`)：该提交可视为“好”；
- `1` (`BAD`)：该提交可视为“坏”；
- `125` (`SKIP`)：环境或判定不稳定，跳过该提交。

---

## 2. 核心判定流程
1. 读取 issue JSON（获取 `bug_type`、`tests`）。
2. 根据测试内容决定需要构建的二进制：
   - 默认 `opt`
   - 若出现 `lli_expected_out`，再追加 `lli`
3. 调用 `LAB_PROVIDER` 构建当前 commit 的二进制到 `LAB_LLVM_BUILD_DIR/bin/`。
4. 做基础健康检查（`--version`）。
5. 执行两段验证：
   - `repro=True`：若可复现，则判定 BAD；
   - `repro=False`：若可验证通过，则判定 GOOD。
6. 否则返回 SKIP。

---

## 3. 输入与输出

### 输入
- 命令行：
  - `issue_path`
  - `commit_sha`（可选，不传时取 `BISECT_HEAD`）
- 环境变量：
  - `LAB_PROVIDER`：外部构建器（签名约定：`provider <commit> <binary> <target_file>`）

### 输出
- 进程退出码（GOOD/BAD/SKIP）
- `stderr` 会打印判定状态与 skip 原因，便于 bisect 日志追踪

---

## 4. 使用方式

```bash
# 手工判定一个提交
python3 scripts/bisect_runner.py <issue_json_path> <commit_sha>

# 或由 git bisect run 自动调用（通常由 bisect_driver 发起）
```

---

## 5. 依赖与关联

### 直接依赖
- `scripts/llvm_helper.py`

### 被谁调用
- `scripts/bisect_driver.py`
- `git bisect run`

---

## 6. 注意事项
1. `LAB_PROVIDER` 是关键依赖，缺失会导致大量 SKIP。
2. 该脚本会删除旧二进制后重建，避免混入其他提交产物。
3. 判定逻辑偏保守：不确定就 SKIP，优先保证 bisect 可靠性。
