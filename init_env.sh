#! /bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export CCACHE_DIR=SCRIPT_DIR/ccache

source $SCRIPT_DIR/init_token.sh
if [ $? -ne 0 ]; then
    echo "Failed to source init_token.sh"
    return
fi

# setup LLM
if [[ "$1" == "gpt-4.1" ]]; then
    export LAB_LLM_TOKEN=$COPILOT_API_TOKEN
    export LAB_LLM_URL=$COPILOT_API_URL
    export LAB_LLM_MODEL=gpt-4.1
    export http_proxy="" # disable proxy on our cse servers
    export https_proxy="" # disable proxy on our cse servers
elif [[ "$1" == "gpt-4o" ]]; then
    export LAB_LLM_TOKEN=$COPILOT_API_TOKEN
    export LAB_LLM_URL=$COPILOT_API_URL
    export LAB_LLM_MODEL=gpt-4o
    export http_proxy="" # disable proxy on our cse servers
    export https_proxy="" # disable proxy on our cse servers
elif [[ "$1" == "gemini-3" ]]; then
    export LAB_LLM_TOKEN=$YUNWU_TOKEN
    export LAB_LLM_URL=$YUNWU_URL
    export LAB_LLM_MODEL=gemini-3-pro-preview
elif [[ "$1" == "deepseek-v3.1" ]]; then
    export LAB_LLM_TOKEN=$OPENROUTER_TOKEN
    export LAB_LLM_URL=$OPENROUTER_URL
    export LAB_LLM_MODEL=deepseek/deepseek-chat-v3.1
elif [[ "$1" == "qwen3-coder" ]]; then
    export LAB_LLM_TOKEN=$OPENROUTER_TOKEN
    export LAB_LLM_URL=$OPENROUTER_URL
    export LAB_LLM_MODEL=qwen/qwen3-coder
elif [[ "$1" == "qwen3" ]]; then
    export LAB_LLM_TOKEN=$OPENROUTER_TOKEN
    export LAB_LLM_URL=$OPENROUTER_URL
    export LAB_LLM_MODEL=qwen/qwen3-235b-a22b-2507
elif [[ "$1" == "sonnet-4.5" ]]; then
    export LAB_LLM_TOKEN=$OPENROUTER_TOKEN
    export LAB_LLM_URL=$OPENROUTER_URL
    export LAB_LLM_MODEL=anthropic/claude-sonnet-4.5
else
    # error
    echo "Invalid model: $1"
    # exit the script butavoid exiting from tmux session
    return
fi


# experimental settings
export LAB_LLM_MAX_SAMPLE_COUNT=4 # 1 or 4
export LAB_PROMPT_TYPE=cot # cot or direct
export LAB_USE_BISECTION=OFF

# setup envs
export LAB_ISSUE_CACHE=$SCRIPT_DIR/issue_cache
export LAB_LLVM_BUILD_DIR=$SCRIPT_DIR/build
export LAB_DATASET_DIR=$SCRIPT_DIR/dataset
export LAB_FIX_DIR=$SCRIPT_DIR/examples/fixes-$1-$LAB_PROMPT_TYPE-iter$LAB_LLM_MAX_SAMPLE_COUNT

# setup LLVM
export LAB_LLVM_DIR=$SCRIPT_DIR/utils/llvm/llvm-project
if [[ ! -d "$LAB_LLVM_DIR" ]]; then
    echo "Cloning llvm-project to $LAB_LLVM_DIR"
    git clone https://github.com/llvm/llvm-project.git "$LAB_LLVM_DIR"
fi
# setup alive2
export ALIVE2_DIR=$SCRIPT_DIR/utils/alive2
export LAB_LLVM_ALIVE_TV=$ALIVE2_DIR/build/alive-tv
if [[ ! -f "$LAB_LLVM_ALIVE_TV" ]]; then
    echo "Building alive2"
    # first, clone alive2
    rm -rf $ALIVE2_DIR
    git clone https://github.com/AliveToolkit/alive2.git $ALIVE2_DIR
    git checkout -C $ALIVE2_DIR 913e1556032ee70a9ebf147b5a0c7e10086b7490
    # second, build a specific LLVM for alive2
    mkdir -p $ALIVE2_DIR/build_llvm
    cd $ALIVE2_DIR/build_llvm
    cmake -GNinja -DLLVM_ENABLE_RTTI=ON -DBUILD_SHARED_LIBS=OFF -DCMAKE_BUILD_TYPE=Release -DLLVM_ENABLE_ASSERTIONS=ON -DLLVM_ENABLE_PROJECTS="llvm;clang" $LAB_LLVM_DIR/llvm
    ninja
    # then, build alive2-tv
    mkdir -p $ALIVE2_DIR/build
    cd $ALIVE2_DIR/build
    cmake -GNinja -DCMAKE_PREFIX_PATH=$ALIVE2_DIR/build_llvm -DBUILD_TV=1 -DBUILD_SHARED_LIBS=OFF -DCMAKE_BUILD_TYPE=Release $ALIVE2_DIR
    ninja
    # finall, test if alive2-tv --help works
    if [[ ! "$($LAB_LLVM_ALIVE_TV --help)" ]]; then
        echo "Building alive2-tv failed"
        return
    fi
fi
# setup cost tool
export LAB_LLVM_COST_TOOL=$SCRIPT_DIR/utils/cost/cost
if [[ ! -f "$LAB_LLVM_COST_TOOL" ]]; then
    echo "Building cost tool"
    cd $SCRIPT_DIR/utils/cost
    ./compile.sh
    # test if cost tool --help works
    if [[ ! "$($LAB_LLVM_COST_TOOL --help)" ]]; then
        echo "Building cost tool failed"
        return
    fi
fi
cd $SCRIPT_DIR

echo "########################################################"
echo "Environment setup complete"
echo "########################################################"
echo "Using model: $1"
echo "Using prompt type: $LAB_PROMPT_TYPE"
echo "Using max sample count (iter): $LAB_LLM_MAX_SAMPLE_COUNT"
echo "Using fix directory: $LAB_FIX_DIR"
echo "########################################################"
