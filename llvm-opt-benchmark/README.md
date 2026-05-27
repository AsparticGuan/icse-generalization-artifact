# llvm-opt-benchmark hit 对比

这个目录用于比较**同一个 issue 的两个 patch**（agent patch vs dataset patch）在 llvm-opt-benchmark 上的命中次数。

## 脚本

- `run_patch_hit_compare.py`

## 依赖前提

先保证 LLVM 实验环境变量可用（与现有脚本一致），例如已经执行过：

```bash
source init_env.sh
```

## 核心思路

1. 从 `results/agent/.../<issue>/<ts>/fix.json` 取 agent patch；
2. 从 `dataset/<issue>.json` 取官方 patch；
3. 分别构建两个 `opt`；
4. 从 Hugging Face bucket 下载指定 project 的 `original/*.bc`；
5. 对每个 `.bc` 分别运行两个 `opt`，统计输出中的命中字符串次数；
6. 比较总命中次数。

## patch 命中计数建议

你可以在 patch 命中路径里加入打印，例如：

```cpp
llvm::errs() << "PATCH_HIT\n";
```

然后使用 `--hit-pattern "PATCH_HIT"`（或分别指定 `--agent-hit-pattern` / `--dataset-hit-pattern`）。

## 用法示例

```bash
python llvm-opt-benchmark/run_patch_hit_compare.py \
  --patch-root results/agent/qwen3.5-plus \
  --issues 142711 \
  --patch-ts 20260321-214746 \
  --project arrow \
  --file-limit 80 \
  --hit-pattern "PATCH_HIT"
```

不传 `--issues` 时，会自动扫描 `--patch-root` 下全部可处理 issue（与 `agent/discriminating_tests.py` 行为对齐）。
`--patch-ts` 为必填，脚本只处理该时间戳目录下的 issue。

默认结果输出到：

- `results/llvm_opt_benchmark_hits/<issue>/<patch_ts>/<project>.json`

可通过 `--output` 自定义输出路径。

## 常用参数

- `--project`: bucket 下的项目名（如 `arrow`、`duckdb`）
- `--patch-ts`: 固定运行时间戳（必填）
- `--file-limit`: 最多下载并测试多少个 `.bc`（默认 `50`）
- `--pass-name`: 强制指定 pass（不传则按 dataset hints 推断）
- `--hit-pattern`: 两边共用的命中正则
- `--agent-hit-pattern`: agent 独立命中正则
- `--dataset-hit-pattern`: dataset 独立命中正则
- `--require-fast-pass`: 要求 `fix.json.fast_check_pass == true`
