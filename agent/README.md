# LLVM InstCombine Fix Agent

An autonomous LLVM missed-optimization fixer built on
[mini-swe-agent](https://mini-swe-agent.com/).

This module runs an agent that explores LLVM source code, applies targeted edits,
and validates patches with the same verification stack used by `pipeline/`.

---

## 1) Scope and Goals

The `agent/` workflow is designed for **missed optimization** issues in LLVM
(especially InstCombine-style transformations).

It focuses on:

- issue-by-issue autonomous fixing
- iterative tool-driven debugging (view source, patch, build, verify)
- output compatibility with existing pipeline tooling (`retest_patches.py`)

It is **not** intended as a generic auto-fix framework for arbitrary repositories.

---

## 2) Directory Structure

```text
agent/
├── run.py                  # Main orchestrator (issue loop, env/model/agent wiring, final export)
├── run_batch.py            # Batch runner (pass model via --model/--models, then call run.py)
├── llvm_agent.py           # LLVMFixAgent (safety guard + custom submit protocol)
├── llvm_env.py             # LLVMEnvironment (cwd/env injection + timeout)
├── env_config.py           # Centralized env-var config (auto-loads .env, provides `cfg` singleton)
├── .env.example            # Template — copy to .env and fill in values
├── config.yaml             # Prompt/config reference file (not currently loaded by run.py)
├── init_agent_env.sh       # Agent-specific env bootstrap (sources root init_env.sh first)
├── tools/                  # CLI tools executed by agent bash actions
│   ├── _setup.py           # Shared sys.path + env bootstrap for tool scripts
│   ├── issue_info.py       # Print issue metadata + tests
│   ├── view_source.py      # View LLVM source with line numbers
│   ├── apply_code.py       # Edit source (replace / write / sed modes)
│   ├── build_and_check.py  # Build + fast check
│   ├── check_full.py       # Build + fast check + llvm-lit regression
│   ├── alive2_check.py     # Alive2 semantic validation helper
│   ├── get_langref.py      # Query LLVM LangRef snippets
│   └── show_diff.py        # Show current diff under llvm/lib and llvm/include
└── README.md / README-zh.md
```

---

## 3) Execution Lifecycle

For each issue, `agent/run.py` does the following:

1. **Prepare per-issue LLVM clone** under `utils/llvm-<issue_id>`.
2. **Initialize / reuse build cache** (`build/<issue_id>` and `build/<issue_id>_cache`).
3. **Build task description** from dataset metadata + failing test case + optional localization output.
4. **Create model + environment + agent**:
   - model from `minisweagent.models.get_model(...)`
   - environment from `LLVMEnvironment`
   - agent from `LLVMFixAgent`
5. **Run autonomous loop** (tool-calling via bash actions).
6. **Run final verification** through `lab_env.Environment` (`check_fast`, then `check_full` when applicable).
7. **Export pipeline-compatible JSON** and trajectory.

---

## 4) Prerequisites

- Python `>=3.10`
- LLVM build toolchain available on host (`git`, `cmake`, `ninja`, C/C++ compiler)
- project dependencies installed
- `mini-swe-agent` installed (imported as `minisweagent` in code)

Suggested setup from repo root:

```bash
uv sync
uv pip install mini-swe-agent
```

---

## 5) Environment Setup

**Only one file needed**: copy `agent/.env.example` → `agent/.env` and fill in your
values. This fully replaces `init_agent_env.sh` + `init_env.sh`.

```bash
cp agent/.env.example agent/.env
# edit agent/.env — fill in LAB_LLM_TOKEN, LAB_LLM_MODEL, LAB_LLM_URL
python agent/run.py 85250
```

`agent/env_config.py` auto-loads `.env` via `python-dotenv` at import time.
Priority: **system env vars > `.env` file > code defaults**.

All variables, their meanings, and defaults are documented in `.env.example`.

### Key Environment Variables

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `LAB_LLM_TOKEN` | **yes** | | API key |
| `LAB_LLM_URL` | | `https://api.openai.com/v1` | API endpoint |
| `LAB_LLM_MODEL` | | `gpt-4o` | Model name (for custom endpoints, keep the gateway model id as-is; both prefixed and non-prefixed names are supported) |
| `LAB_AGENT_LOCALIZE_MODE` | | `pipeline` | Runtime localization mode fallback (`pipeline` or `lite`), overridden by CLI `--localize-mode` |
| `LAB_AGENT_RUNTIME_LOCALIZE` | | `ON` | Enable runtime localization when `results/agent/<model>/<issue>/localization.json` is missing |
| `LAB_AGENT_STEP_LIMIT` | | `30` | Max steps per issue |
| `LAB_AGENT_COST_LIMIT` | | `5.0` | Max cost per issue (USD) |
| `MSWEA_GLOBAL_COST_LIMIT` | | `10.0` | mini-swe-agent global cost cap |
| `MSWEA_GLOBAL_CALL_LIMIT` | | `0` | mini-swe-agent global call cap (`0` means unlimited) |
| `LAB_LLVM_DIR` | | `utils/llvm/llvm-project` | Base LLVM source (seed for per-issue clones) |
| `LAB_LLVM_BUILD_DIR` | | `build/` | Build root |
| `LAB_LLVM_ALIVE_TV` | | `utils/alive2/build/alive-tv` | Alive2 tool path |
| `LAB_LLVM_COST_TOOL` | | `utils/cost/cost` | Cost tool path |
| `LAB_FIX_DIR_AGENT` | | auto-generated from model name | Result JSON output dir |
| `LAB_TRAJ_DIR_AGENT` | | auto-generated from model name | Trajectory output dir |

> Dataset path is fixed to `<project_root>/dataset` in agent runtime (aligned with
> `pipeline/generate.py` style). External `LAB_DATASET_DIR` overrides are ignored.

> **Legacy**: `source agent/init_agent_env.sh <model>` still works but is no
> longer required.

---

## 6) CLI Usage

### 6.1 `agent/run.py` (single-model runner)

Basic usage:

```bash
# all issues in dataset/
uv run python agent/run.py

# single / multiple issues
uv run python agent/run.py 85250
uv run python agent/run.py 85250,76128
```

Arguments (`agent/run.py`):

| Argument | Type | Default | Description |
|---|---|---|---|
| `issues` | positional | `all` | `all` / single issue / comma-separated issues. |
| `-f`, `--force` | flag | off | Overwrite existing run results. |
| `--fresh-run` | flag | off | Remove and recreate per-issue clone/build/build_cache before each issue. |
| `--retest`, `--retest-patches` | flag | off | After run loop, call `agent/retest_patches.py` for selected issues. |
| `--retest-force` | flag | off | Overwrite existing retest outputs (passed to retest script). |
| `--retest-dir <path>` | string | empty | Retest output root; if empty, defaults to `results/agent/<safe_model>`. |
| `--model <name>` | string | empty | Override `LAB_LLM_MODEL` for this run. |
| `--localize-mode {pipeline,lite}` | enum | none | Runtime localization mode override (priority: CLI > `LAB_AGENT_LOCALIZE_MODE` > `pipeline`). |
| `--localize-refresh` | flag | off | Ignore existing `localization.json` and recompute runtime localization. |
| `--effort {none,minimal,low,medium,high,xhigh,max}` | enum | none | Generic reasoning level mapped by model family. |
| `--reasoning-effort ...` | enum | none | Explicit OpenAI `reasoning.effort`. |
| `--thinking-level ...` | enum | none | Explicit Gemini `thinkingLevel`. |
| `--thinking-type ...` | enum | none | Explicit DeepSeek/Claude `thinking.type`. |
| `--output-effort ...` | enum | none | Explicit Claude `output_config.effort`. |
| `--budget-tokens <int>` | int | none | Claude `thinking.budget_tokens`. |
| `--thinking-budget <int>` | int | none | Gemini/Qwen `thinking_budget`. |
| `--enable-thinking <true\|false>` | bool | none | Qwen `enable_thinking`. |
| `--reasoning-json '<json_obj>'` | JSON object | empty | Direct merge into `reasoning`. |
| `--extra-body-json '<json_obj>'` | JSON object | empty | Direct merge into `extra_body`. |

Behavior notes:

- `--retest-force` or `--retest-dir` automatically enables `--retest`.
- Unknown issue IDs are skipped if they do not exist under `dataset/`.
- Non-verified issues are skipped.
- If an issue already passes initial fast-check, fixing is skipped for that issue.

### 6.2 `agent/run_batch.py` (multi-model runner)

`run_batch.py` keeps model order exactly as provided and forwards parameters into `run.py`.

```bash
# example from this repository (single model + all issues)
uv run python agent/run_batch.py \
  --model qwen3.5-plus \
  --issues all \
  --localize-mode pipeline \
  --thinking-profile '{"enable_thinking":true,"thinking_budget":81920}' \
  -f \
  --fresh-run \
  --retest \
  --retest-force
```

Arguments (`agent/run_batch.py`):

| Argument | Type | Default | Description |
|---|---|---|---|
| `-m`, `--model <name>` | repeatable | empty | Add model(s) in CLI order. |
| `--models a,b,c` | CSV | empty | Add comma-separated models. |
| `--issues all\|id\|id1,id2` | string | `all` | Shared issue set for all models. |
| `--localize-mode {pipeline,lite}` | enum | none | Pass-through to `run.py --localize-mode`. |
| `--localize-refresh` | flag | off | Pass-through to `run.py --localize-refresh`. |
| `--effort ...` | repeatable | empty | Ordered generic effort values. |
| `--efforts a,b,c` | CSV | empty | Ordered effort values (CSV form). |
| `--thinking-profile '<json_obj>'` | repeatable | empty | Ordered per-model profile JSON. |
| `--thinking-profiles-json '<json_obj_or_array>'` | JSON | empty | Single profile or ordered profile array. |
| `-f`, `--force` | flag | off | Pass-through `-f` to `run.py`. |
| `--fresh-run` | flag | off | Pass-through `--fresh-run` to `run.py`. |
| `--stop-on-error` | flag | off | Stop remaining models on first non-zero return code. |
| `--model-timeout-seconds <int>` | int | `0` | Per-model timeout (`0` means disabled). |
| `--retest`, `--retest-patches` | flag | off | Pass-through `--retest` to `run.py`. |
| `--retest-force` | flag | off | Pass-through `--retest-force` to `run.py`. |
| `--retest-dir <path>` | string | empty | Pass-through retest root; for multiple models, `run_batch.py` auto-appends `/<safe_model>`. |

Batch behavior notes:

- Model selection is passed via `run.py --model`; `run_batch.py` does not rewrite `agent/.env`.
- `--retest-force` or `--retest-dir` automatically enables `--retest`.
- With custom endpoints (`LAB_LLM_URL` not OpenAI official), use the exact gateway model id.

---

## 7) Outputs

Primary run artifacts (per issue, timestamped):

- `results/agent/<safe_model>/<safe_issue>/<timestamp>/fix.json`
- `results/agent/<safe_model>/<safe_issue>/<timestamp>/traj.json`
- `results/agent/<safe_model>/<safe_issue>/<timestamp>/preds.json`

Localization cache (stable per issue under model root):

- `results/agent/<safe_model>/<safe_issue>/localization.json`

Retest artifact (`agent/retest_patches.py`):

- `<retest_root>/<safe_issue>/<timestamp>/retest.json`
- default `<retest_root>` is `results/agent/<safe_model>`.

Important result fields include:

- `wall_time`
- `build_count`, `build_failure_count`
- `fast_check_count`, `full_check_count`
- `fast_check_pass`, `full_check_pass`
- `patch`
- `log.model`, `log.messages`, `log.trajectory`
- `check_fast_log`, `check_full_log`

### Retest patch-selection rule

`agent/retest_patches.py` validates with `dataset/<issue_id>.json` `dev_tests` and selects patches as follows:

- `fast_check_pass` must be `true`, otherwise skip.
- if `full_check_pass == false`, use `fast_full_mismatch_patch`.
- otherwise use `patch`.

Manual retest command:

```bash
uv run python agent/retest_patches.py [issue_id_or_csv] [-f]
```

---

## 8) Agent Tool Reference (`agent/tools/`)

These scripts are primarily called by the agent itself, but are also useful for debugging.

| Tool | Purpose |
|---|---|
| `issue_info.py <issue_id>` | Print issue metadata, components, tests, and failing cases |
| `view_source.py <file> [start] [end]` | Read LLVM source with line numbers |
| `apply_code.py replace/write/sed ...` | Edit source code in targeted ways |
| `build_and_check.py` | Build + run fast validation |
| `check_full.py` | Run full validation (build + fast + lit) |
| `alive2_check.py <src.ll> <tgt.ll>` | Semantic equivalence check with Alive2 |
| `get_langref.py <instruction...>` | Query LLVM LangRef snippets |
| `show_diff.py` | Show current working diff (`llvm/lib`, `llvm/include`) |

---

## 9) Safety and Constraints

`LLVMFixAgent` adds policy checks before executing shell actions:

- blocks dangerous command patterns (e.g., destructive root-level deletion)
- stops run on explicit submission signal (`echo SUBMIT_PATCH`)

Runtime constraints:

- commands run in fresh shell contexts (always use explicit working directory)
- execution timeout is set to 600s per command in `LLVMEnvironment`
- expected code-edit scope is LLVM source under `llvm/lib/` and `llvm/include/`

---

## 10) Relationship with `pipeline/`

The agent path intentionally reuses core logic from `scripts/lab_env.py` and
`scripts/llvm_helper.py`, so build/check behavior remains aligned with the
pipeline path.

| Aspect | Pipeline (`pipeline/generate.py`) | Agent (`agent/run.py`) |
|---|---|---|
| Control flow | Scripted fixed stages | Autonomous iterative loop |
| Edit strategy | Generate larger patch candidates | Incremental command-driven edits |
| Feedback loop | Structured rounds (`max_sample_count`) | Flexible step loop (`step_limit`) |
| Outputs | JSON cert/log format | Compatible JSON cert/log format |

---

## 11) Troubleshooting

1. **`ModuleNotFoundError: minisweagent`**
   - Install it explicitly: `uv pip install mini-swe-agent`.

2. **Environment variable missing errors**
   - Copy `agent/.env.example` → `agent/.env` and fill in at least `LAB_LLM_TOKEN`.

3. **Issue skipped as unverified**
   - Only verified dataset entries are processed by default.

4. **Issue skipped because fast check already passes**
   - This means the current base already satisfies fast-check criteria for that issue.

5. **Result exists and run is skipped**
   - Use `-f` to overwrite existing `results/agent/<safe_model>/<safe_issue>/<timestamp>/fix.json`.

---

## 12) Important Note about `config.yaml`

`agent/config.yaml` documents the intended prompt/config structure, but current
runtime behavior is defined directly in `run.py` (`_load_system_template()` and
`_load_instance_template()`).

If you want prompt changes to take effect immediately, update `run.py`.
