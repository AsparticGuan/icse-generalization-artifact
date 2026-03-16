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
├── run_batch.py            # Batch runner (switch model by editing .env, then run run.py)
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
| `MSWEA_GLOBAL_CALL_LIMIT` | | `200` | mini-swe-agent global call cap |
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

## 6) Running

```bash
# single issue
python agent/run.py 85250

# multiple issues
python agent/run.py 85250,76128

# all issues from dataset directory
python agent/run.py

# overwrite existing result JSON
python agent/run.py 85250 -f

# force runtime localization mode
python agent/run.py 85250 --localize-mode pipeline
python agent/run.py 85250 --localize-mode lite
```

Localization mode priority:

- CLI `--localize-mode` > env `LAB_AGENT_LOCALIZE_MODE` > default `pipeline`.
- Per issue cache file path is fixed: `results/agent/<safe_model>/<safe_issue>/localization.json`.
- If that file exists, agent uses it first; if missing, runtime localization runs and persists to the same path.

### Batch Testing Script (multiple models × shared issues)

`agent/run_batch.py` can run the same issue set across one or more models,
strictly in the input order.

Model selection is passed directly via `run.py --model` for each run.
`run_batch.py` does **not** rewrite `agent/.env`.

```bash
# 1) two models (order preserved), one issue
uv run python agent/run_batch.py \
  --model openai/gpt-5.3-codex \
  --model qwen/qwen3.5-plus \
  --issues 104772

# 2) one model, multiple issues
uv run python agent/run_batch.py \
  --model openai/gpt-5.3-codex \
  --issues 104772,85250

# 3) multiple models, all issues
uv run python agent/run_batch.py \
  --models openai/gpt-5.3-codex,qwen/qwen3.5-plus \
  --issues all

# 4) overwrite existing results (-f passes through to run.py)
uv run python agent/run_batch.py \
  --model openai/gpt-5.3-codex \
  --issues 104772 \
  -f

# 5) force pipeline runtime localization in batch mode
uv run python agent/run_batch.py \
  --model qwen3.5-plus \
  --issues all \
  --localize-mode pipeline \
  --thinking-profile '{"enable_thinking":true,"thinking_budget":81920}' \
  -f \
  --retest \
  --retest-force
```

Main options:

- `--model <name>`: repeatable, preserves input order.
- `--models a,b,c`: comma-separated model list.
- `--issues all|<id>|<id1,id2,...>`: one/many/all issues (applies to every model).
- `--localize-mode pipeline|lite`: pass runtime localization mode to `run.py`.
- `-f/--force`: pass `-f` to `run.py`.
- `--stop-on-error`: stop immediately when one model fails.

For custom endpoints (`LAB_LLM_URL` not OpenAI official), `run.py` preserves
`LAB_LLM_MODEL` as-is (both prefixed and non-prefixed names are supported).
Use the exact model id registered on your gateway.

Notes:

- If no issue id is given, all `*.json` in dataset are processed.
- Non-verified issues are skipped.
- If an issue already passes fast check before editing, it is skipped.

---

## 7) Outputs

Primary run artifacts (per issue, timestamped):

- `results/agent/<model>-<issue_id>/<timestamp>/fix.json`
- `results/agent/<model>-<issue_id>/<timestamp>/traj.json`
- `results/agent/<model>-<issue_id>/<timestamp>/preds.json`

Pipeline-compatible flat artifacts (for direct reuse by `pipeline/retest_patches.py`):

- `results/agent/fixes-<model>-agent-<timestamp>/<issue_id>.json`
- `results/agent/traj-<model>-agent-<timestamp>/<issue_id>.traj.json`

Result JSON is intentionally compatible with pipeline consumers
(notably `pipeline/retest_patches.py`).

Important result fields include:

- `wall_time`
- `build_count`, `build_failure_count`
- `fast_check_count`, `full_check_count`
- `fast_check_pass`, `full_check_pass`
- `patch`
- `log.model`, `log.messages`, `log.trajectory`
- `check_fast_log`, `check_full_log`

### Retest historical patches (use pipeline directly)

For patch reproducibility retest, **reuse pipeline script directly** (do not add a
separate agent retest implementation):

```bash
LAB_PATCH_DIR=<results/agent/fixes-...-<timestamp>> \
python pipeline/retest_patches.py [issue_id_or_csv] [-f]
```

`pipeline/retest_patches.py` verifies with `dataset/<issue_id>.json` **`dev_tests`**
(developer golden test cases). This is intentionally different from normal
`tests` used in generate/fix loops — do not mix them.

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
   - Use `-f` to overwrite existing `fixes-.../<issue_id>.json`.

---

## 12) Important Note about `config.yaml`

`agent/config.yaml` documents the intended prompt/config structure, but current
runtime behavior is defined directly in `run.py` (`_load_system_template()` and
`_load_instance_template()`).

If you want prompt changes to take effect immediately, update `run.py`.
