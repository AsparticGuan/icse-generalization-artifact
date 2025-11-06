# LLVM AUTO-OPT: Generating LLVM Patch to Implement Missed Optimizations

This project is inspired and derived from [LLVM-APR-Benchmark](https://github.com/dtcxzyw/llvm-apr-benchmark)

## Preparation

#### Step 1: Set your own tokens
```
cp init_token_template.sh init_token.sh
```
Then setup your tokens in `init_token.sh`

#### Step 2: Compile cost utility
```
cd ./utils/cost
./compile.sh
```

#### Step 3: Install alive2 utility
Please 

#### Step 2: Init the environment
For example, setup the Qwen-3 model environment
```
source init_env.sh qwen3
```

## Dataset Collection

#### Step 1: Collect missed-optimization reports from LLVM Github Issues

```
./scripts/extract_from_isssues.py
```

#### Step 2: Verify that we can reproduce each missed-optimization
```
./scripts/verify_repro.py
```

## Baseline Auto-opt

```
./examples/baseline.py
```
