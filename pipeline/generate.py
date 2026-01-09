#!/usr/bin/env python3

import sys
import os
import json
import re
import string
import tarfile
from pathlib import Path

sys.path.append(os.path.join(os.path.dirname(os.environ["LAB_DATASET_DIR"]), "scripts"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "scripts"))
import shutil
import llvm_helper # pyright: ignore[reportMissingImports]  # noqa: E402
from lab_env import Environment as Env # pyright: ignore[reportMissingImports]  # noqa: E402
from openai import OpenAI
from openai import NOT_GIVEN

# Import tree-sitter for C++ function extraction
try:
    import tree_sitter_cpp
    from tree_sitter import Language, Parser, Tree
    CXX_LANGUAGE = Language(tree_sitter_cpp.language())
    cxx_parser = Parser(CXX_LANGUAGE)
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False
    cxx_parser = None

# DEBUG
DEBUG = 0
if DEBUG:
    issue_id_debug = "84608"
    issue_log_debug = json.loads(open(os.path.join(os.environ["LAB_FIX_DIR"], f"{issue_id_debug}.json")).read())

token = os.environ["LAB_LLM_TOKEN"]
url = os.environ.get("LAB_LLM_URL", "https://api.deepseek.com")
model = os.environ.get("LAB_LLM_MODEL", "deepseek-reasoner")
basemodel_cutoff = os.environ.get("LAB_LLM_BASEMODEL_CUTOFF", "1970-12-31Z")
client = OpenAI(api_key=token, base_url=url)
temperature = float(os.environ.get("LAB_LLM_TEMP", "0.8"))
# Seems not working, sad :(
enable_tooling = os.environ.get("LAB_LLM_ENABLE_TOOLING", "OFF") == "ON"
enable_streaming = os.environ.get("LAB_LLM_ENABLE_STREAMING", "OFF") == "ON"
max_log_size = int(os.environ.get("LAB_LLM_MAX_LOG_SIZE", 1000000000))
max_sample_count = int(os.environ.get("LAB_LLM_MAX_SAMPLE_COUNT", 4))
omit_issue_body = os.environ.get("LAB_LLM_OMIT_ISSUE_BODY", "ON") == "ON"
max_build_jobs = int(os.environ.get("LAB_MAX_BUILD_JOBS", os.cpu_count()))
fix_dir = os.environ["LAB_FIX_DIR"]
os.makedirs(fix_dir, exist_ok=True)

tools = []
tool_get_source_prompt = "If you need to view the source code, please call the `get_source` function. It is very helpful to address compilation errors by inspecting the latest LLVM API."
tool_get_source_desc = {
    "type": "function",
    "function": {
        "name": "get_source",
        "description": "Get the first 10 lines of the source code starting from the specified line number.",
        "parameters": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Relative path to the source file. Must start with 'llvm/'",
                },
                "lineno": {
                    "type": "number",
                    "description": "The line number to start from. The first line is 1.",
                },
            },
            "required": ["file", "lineno"],
        },
    },
}


def tool_get_source(env, args):
    file = args["file"]
    if not file.startswith("llvm/") or file.contains(".."):
        return "Invalid file path"
    lineno = int(args["lineno"])
    path = os.path.join(llvm_helper.llvm_dir, file)
    env.reset()
    env.use_knowledge(f"source file: {file}:{lineno}", env.knowledge_cutoff)
    with open(path) as f:
        source = f.readlines()
    return "```cpp\n" + "".join(source[lineno - 1 : lineno + 9]) + "```\n"


tools.append((tool_get_source_prompt, tool_get_source_desc, tool_get_source))

tool_get_instruction_docs_prompt = "If you need the definition of an LLVM instruction or an intrinsic, please call the `get_instruction_docs` function. It is useful to understand new poison-generating flags."
tool_get_instruction_docs_desc = {
    "type": "function",
    "function": {
        "name": "get_instruction_docs",
        "description": "Get the documentation of an LLVM instruction or an intrinsic.",
        "parameters": {
            "type": "object",
            "properties": {
                "inst": {
                    "type": "string",
                    "description": "The name of the instruction or intrinsic (e.g., 'add', 'llvm.ctpop'). Do not include the suffix for type mangling.",
                }
            },
            "required": ["inst"],
        },
    },
}


def tool_get_instruction_docs(env, args):
    inst = args["inst"]
    return env.get_langref_desc([inst])[inst]


tools.append(
    (
        tool_get_instruction_docs_prompt,
        tool_get_instruction_docs_desc,
        tool_get_instruction_docs,
    )
)


tool_check_refinement_prompt = "If you want to check if an optimization is correct, please call the `check_refinement` function. If the optimization is incorrect, the function will provide a counterexample."
tool_check_refinement_desc = {
    "type": "function",
    "function": {
        "name": "check_refinement",
        "description": "Check if an optimization is correct. If the optimization is incorrect, the function will provide a counterexample.",
        "parameters": {
            "type": "object",
            "properties": {
                "src": {
                    "type": "string",
                    "description": "The original LLVM function.",
                },
                "tgt": {
                    "type": "string",
                    "description": "The optimized LLVM function. The name of target function should be the same as the original function.",
                },
            },
            "required": ["src", "tgt"],
        },
    },
}


def tool_check_refinement(env, args):
    src = args["src"]
    tgt = args["tgt"]
    env.use_knowledge("alive2", env.knowledge_cutoff)
    if "ptr" in src and "target datalayout" not in src:
        src = f'target datalayout = "p:8:8:8"\n{src}'
    if "ptr" in tgt and "target datalayout" not in tgt:
        tgt = f'target datalayout = "p:8:8:8"\n{tgt}'

    res, log = llvm_helper.alive2_check(src, tgt, "-src-unroll=8 -tgt-unroll=8")
    if res:
        return "The optimization is correct."
    return log


tools.append(
    (tool_check_refinement_prompt, tool_check_refinement_desc, tool_check_refinement)
)


def get_tooling_prompt():
    if not enable_tooling:
        return ""
    prompt = "You are allowed to use the following functions when fixing this bug:\n"
    for x in tools:
        prompt += x[0] + "\n"
    return prompt


def get_available_tools():
    if not enable_tooling:
        return NOT_GIVEN
    return [x[1] for x in tools]


def dispatch_tool_call(env, name, args):
    assert enable_tooling

    try:
        args = json.loads(args)
        for tool in tools:
            if tool[1]["function"]["name"] == name:
                return tool[2](env, args)
    except Exception as e:
        return str(e)


def append_message(messages, full_messages, message, dump=True):
    role = message["role"]
    content = message["content"]
    if dump:
        print(f"{role}: {content}")
    messages.append({"role": role, "content": content})
    full_messages.append(message)


def chat_with_tooling(env, messages, full_messages):
    reasoning_content = ""
    content = ""
    try:
        while True:
            response = (
                client.chat.completions.create(
                    model=model,
                    messages=messages,
                    timeout=300,
                    temperature=temperature,
                    tools=get_available_tools(),
                )
                .choices[0]
                .message
            )
            if response.tool_calls is None or len(response.tool_calls) == 0:
                # Will break here because tool_calls is None.
                break

            if hasattr(response, "reasoning_content"):
                reasoning_content += response.reasoning_content
                print("Thinking:")
                print(response.reasoning_content)

            messages.append(response)

            for tool_call in response.tool_calls:
                name = tool_call.function.name
                args = tool_call.function.arguments
                res = dispatch_tool_call(env, name, args)
                print(f"Call tool {name} with")
                print(args)
                print("Result: ", res)
                full_messages.append(
                    {
                        "role": "assistant - funccall",
                        "tool_name": name,
                        "tool_args": args,
                        "tool_res": res,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": str(res),
                    }
                )

        print("assistant:")
        if hasattr(response, "reasoning_content"):
            reasoning_content += response.reasoning_content
            print("Thinking:")
            print(response.reasoning_content)
        content = response.content
        print("Answer:")
        print(content)
    except Exception as e:
        print(e)
        append_message(
            messages,
            full_messages,
            {"role": "assistant", "content": f"Exception: {e}"},
            dump=False,
        )
        return ""
    answer = {"role": "assistant", "content": content}
    if len(reasoning_content) > 0:
        answer["reasoning_content"] = reasoning_content
    append_message(messages, full_messages, answer, dump=False)
    return content


def chat_with_streaming(env, messages, full_messages):
    reasoning_content = ""
    content = ""
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=messages,
            timeout=300,
            temperature=temperature,
            stream=True,
        )
        is_thinking = False
        is_answering = False
        for chunk in completion:
            delta = chunk.choices[0].delta
            if (
                hasattr(delta, "reasoning_content")
                and delta.reasoning_content is not None
            ):
                if not is_thinking:
                    print("Thinking:")
                    is_thinking = True
                print(delta.reasoning_content, end="", flush=True)
                reasoning_content += delta.reasoning_content
            elif delta.content is not None:
                if delta.content != "" and is_answering is False:
                    print("\nAnswer:")
                    is_answering = True
                print(delta.content, end="", flush=True)
                content += delta.content

    except Exception as e:
        print(e)
        append_message(
            messages,
            full_messages,
            {"role": "assistant", "content": f"Exception: {e}"},
            dump=False,
        )
        return ""
    answer = {"role": "assistant", "content": content}
    if len(reasoning_content) > 0:
        answer["reasoning_content"] = reasoning_content
    append_message(messages, full_messages, answer, dump=False)
    return content


def chat(env, messages, full_messages):
    if enable_streaming:
        assert not enable_tooling
        return chat_with_streaming(env, messages, full_messages)
    return chat_with_tooling(env, messages, full_messages)


format_requirement_direct = """
Please answer with the code directly. Do not include any additional information in the output.
Please answer with the complete code snippet (including the unmodified part) that replaces the original code. Do not answer with a diff.
"""

format_requirement_cot = \
"""
## Instructions for Fixing ##

Your response is for a formal code review and MUST strictly follow the structure below. Use the exact Markdown headings provided.

---

### 1. Analyze
**Content**: Provide a deep analysis of the build failure or missed optimization. Explain the "what" (what is the issue), the "why" (why does it happen in the current code), and the "impact" (what is the performance or correctness impact).

### 2. Propose a Patch
**Content**: Describe your proposed changes at a conceptual level. Explain the logic behind your fix and why it is the correct approach according to LLVM's coding standards.

### 3. Verify
**Content**: Walk through the execution of the provided test program with your patch applied. Explain how the new code path handles the case correctly and achieves the desired optimization.

### 4. Submit
**Content**: Provide the complete and final source code for the file that needs to be changed.
*   The code must be a single, self-contained block.
*   The code must be the full file content, not a diff or a snippet.
*   Enclose the code within a ```cpp ... ``` Markdown block.

---
"""
# """
# ## Instructions for Fixing ##

# Your goal is to create a clean, minimal patch that properly implements the missed optimization while maintaining LLVM's code quality standards. \
# Follow these steps:

# 1. **Analyze**: Analyze the provided information to fully understand the missed optimization.
# 2. **Propose a Patch**: Outline your proposed solution. Explain your reasoning and the specific changes you intend to make.
# 3. **Verify**: Analyze your own patch and the provided test program to verify if the patch can really realize the missed optimization.
# 4. **Submit**: Provide the final, clean patch for review. The patch should be the complete code snippet (including the unmodified part) that replaces the original code. Do not answer with a diff.
# """

if os.getenv("LAB_PROMPT_TYPE") == "direct":
    format_requirement = format_requirement_direct
else:
    format_requirement = format_requirement_cot

def get_system_prompt() -> str:
    return (
        """You are an LLVM maintainer.
Users have reported a case where LLVM failed to optimize a specific program.
You are adding new code or modifying existing code to implement the missing optimization."""
        + format_requirement
        + get_tooling_prompt()
    )

def traverse_tree(tree: Tree):
    """Traverse all nodes in the syntax tree"""
    cursor = tree.walk()
    reached_root = False
    while reached_root == False:
        yield cursor.node
        if cursor.goto_first_child():
            continue
        if cursor.goto_next_sibling():
            continue
        retracing = True
        while retracing:
            if not cursor.goto_parent():
                retracing = False
                reached_root = True
            if cursor.goto_next_sibling():
                retracing = False


def extract_function_name(func_def_node):
    """Extract function name from function definition node"""
    try:
        declarators = func_def_node.children_by_field_name("declarator")
        if len(declarators) == 0:
            return None
        func_name_node = declarators[0]
        while True:
            decl = func_name_node.children_by_field_name("declarator")
            if len(decl) == 0:
                if func_name_node.type == "reference_declarator":
                    if func_name_node.child_count > 1:
                        func_name_node = func_name_node.child(1)
                        continue
                break
            func_name_node = decl[0]
        func_name = func_name_node.text.decode("utf-8")
        return func_name
    except Exception:
        return None


def get_full_path_from_filename(filename: str, llvm_root: str = None) -> str | None:
    """Find the full path from filename (relative to llvm_root)"""
    if llvm_root is None:
        llvm_root = os.path.join(os.path.dirname(__file__), "..", "utils", "llvm", "llvm-project")
    basename = os.path.basename(filename)
    llvm_path = Path(llvm_root)
    for cpp_file in llvm_path.rglob(basename):
        if cpp_file.is_file():
            rel_path = cpp_file.relative_to(llvm_path)
            return str(rel_path).replace("\\", "/")
    return None


def match_function_name(pred_func: str, gold_func: str) -> bool:
    """Check if predicted function name matches gold function name"""
    # Handle cases like "InstCombinerImpl::visitMul" vs "visitMul"
    if pred_func == gold_func:
        return True
    # Check if pred_func is a suffix of gold_func (e.g., "visitMul" in "InstCombinerImpl::visitMul")
    if gold_func.endswith("::" + pred_func):
        return True
    # Check if pred_func contains gold_func (e.g., "InstCombinerImpl::visitMul" contains "visitMul")
    if pred_func in gold_func or gold_func in pred_func:
        return True
    return False


def extract_function_content(file_path: str, func_name: str, source_code: str) -> str | None:
    """Extract function content from source code using tree-sitter"""
    if not TREE_SITTER_AVAILABLE:
        return None
    
    try:
        tree = cxx_parser.parse(bytes(source_code, "utf-8"))
        for node in traverse_tree(tree):
            if node.type == "function_definition":
                extracted_name = extract_function_name(node)
                if extracted_name and match_function_name(extracted_name, func_name):
                    # Extract function content
                    start_byte = node.start_byte
                    end_byte = node.end_byte
                    func_content = source_code[start_byte:end_byte]
                    return func_content
    except Exception as e:
        print(f"Error extracting function {func_name} from {file_path}: {e}")
        return None
    return None


def _extract_json_obj(text: str) -> dict | None:
    """Best-effort: extract the first JSON object from LLM output."""
    if not isinstance(text, str):
        return None
    # Try strict parse first.
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # Fallback: find the first {...} region.
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _fallback_truncate_hunk(hunk: str, *, keep_last_lines: int = 200) -> str:
    if not isinstance(hunk, str):
        return hunk
    lines = hunk.splitlines()
    keep_last_lines = min(max(1, keep_last_lines), len(lines))
    return "\n".join(lines[-keep_last_lines:])


def _strip_optional_lineno_prefix(line: str) -> str:
    # Accept formats like "  123: code" or "123:code"
    m = re.match(r"^\s*\d+\s*:\s?(.*)$", line)
    if m:
        return m.group(1)
    return line


def _find_contiguous_subsequence(haystack: list[str], needle: list[str]) -> tuple[int, int] | None:
    """Return (start_idx, end_idx_exclusive) if needle matches a contiguous region of haystack."""
    if not needle:
        return None
    n = len(needle)
    if n > len(haystack):
        return None
    # Compare using rstrip() to tolerate trailing spaces.
    hs = [x.rstrip() for x in haystack]
    nd = [x.rstrip() for x in needle]
    first = nd[0]
    for i in range(0, len(hs) - n + 1):
        if hs[i] != first:
            continue
        if hs[i : i + n] == nd:
            return i, i + n
    return None


def truncate_hunk(
    env: Env,
    hunk: str,
    *,
    file_path: str | None = None,
    func_name: str | None = None,
    max_lines: int = 500,
    window_lines: int = 200,
    keep_last_lines: int = 200,
) -> str:
    if not isinstance(hunk, str):
        return hunk
    lines = hunk.splitlines()
    if len(lines) <= max_lines:
        return hunk

    numbered = "\n".join(f"{i+1:5d}: {ln}" for i, ln in enumerate(lines))
    issue_desc = ""
    try:
        issue_desc = get_issue_desc(env)
    except Exception:
        issue_desc = ""

    meta = []
    if file_path:
        meta.append(f"file: {file_path}")
    if func_name:
        meta.append(f"function: {func_name}")
    meta_s = ("\n".join(meta) + "\n") if meta else ""

    system = (
        "You are an expert LLVM engineer and code localizer.\n"
        "Given an LLVM optimization bug report and a C++ function body, pick the smallest contiguous region "
        "that is most likely to require modification to fix the issue.\n"
        "Do NOT propose a patch. Do NOT call any tools.\n"
        "You MUST output only the truncated hunk text (no Markdown, no code fences, no JSON).\n"
        "The output must be a contiguous excerpt copied verbatim from the given hunk.\n"
        f"Keep it within {window_lines} lines.\n"
    )
    user = (
        f"{issue_desc}"
        f"{meta_s}"
        "Below is the FULL hunk with line numbers.\n"
        "Return ONLY the truncated hunk text (without the line numbers), as a contiguous excerpt.\n"
        "Do not add, rewrite, or summarize any lines; copy the exact lines from the hunk.\n"
        "\n"
        f"{numbered}\n"
    )

    messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    full_messages: list[dict] = []
    try:
        reply = chat(env, messages, full_messages)
        # Be tolerant: some models may wrap with ``` or keep the "NNN:" prefixes.
        picked = extract_code_from_reply(reply).strip()
        if not picked:
            return _fallback_truncate_hunk(hunk, keep_last_lines=keep_last_lines)
    except Exception:
        return _fallback_truncate_hunk(hunk, keep_last_lines=keep_last_lines)

    # Normalize reply: drop optional "NNN:" prefixes, and enforce line budget.
    picked_lines = [_strip_optional_lineno_prefix(x) for x in picked.splitlines()]
    picked_lines = [x.rstrip("\n") for x in picked_lines]
    while picked_lines and picked_lines[0].strip() == "":
        picked_lines.pop(0)
    while picked_lines and picked_lines[-1].strip() == "":
        picked_lines.pop()
    if not picked_lines:
        return _fallback_truncate_hunk(hunk, keep_last_lines=keep_last_lines)

    n = len(lines)
    if window_lines is None or window_lines <= 0:
        window_lines = keep_last_lines
    window_lines = min(window_lines, n)
    if len(picked_lines) > window_lines:
        picked_lines = picked_lines[:window_lines]

    match = _find_contiguous_subsequence(lines, picked_lines)
    if not match:
        return _fallback_truncate_hunk(hunk, keep_last_lines=keep_last_lines)
    start_idx, end_idx = match
    return "\n".join(lines[start_idx:end_idx])


def load_func_info_from_json(issue_id: str, json_path: str = None) -> tuple[dict, list, str | None]:
    """Load pred_funcs, gold_funcs, and gold_file from pipeline/localize-result.json"""
    if json_path is None:
        json_path = os.path.join(os.path.dirname(__file__), "localize-result.json")
    
    if not os.path.exists(json_path):
        return {}, [], None
    
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}, [], None
    
    issue_data = data.get(issue_id, {})
    pred_funcs = issue_data.get("pred_funcs", {})
    gold_funcs = issue_data.get("gold_funcs", [])
    gold_file = issue_data.get("gold_file", None)
    
    return pred_funcs, gold_funcs, gold_file


def get_hunk_from_func(env: Env, file_path: str, func_name: str) -> tuple[str, str] | None:
    """Extract function content as hunk from specified file and function name"""
    base_commit = env.get_base_commit()
    llvm_root = os.path.join(os.path.dirname(__file__), "..", "utils", "llvm", "llvm-project")

    # Read file content from git
    try:
        if file_path.startswith("llvm/"):
            source_code = str(llvm_helper.git_execute(["show", f"{base_commit}:{file_path}"]))
        else:
            # Try to find the file
            full_path = get_full_path_from_filename(os.path.basename(file_path), llvm_root)
            if full_path:
                source_code = str(llvm_helper.git_execute(["show", f"{base_commit}:{full_path}"]))
                file_path = full_path
            else:
                print(f"Warning: Cannot find file {file_path}, skipping")
                return None
    except Exception as e:
        print(f"Warning: Cannot read file {file_path}: {e}, skipping")
        return None

    # Extract function content using tree-sitter
    func_content = extract_function_content(file_path, func_name, source_code)
    if func_content:
        return file_path, truncate_hunk(env, func_content, file_path=file_path, func_name=func_name)

    # Fallback: try to find function by name using regex (less accurate)
    lines = source_code.splitlines()
    for i, line in enumerate(lines):
        # Simple heuristic: look for function definition
        if func_name in line and ("{" in line or (i + 1 < len(lines) and "{" in lines[i + 1])):
            # Extract function with some context
            start = max(0, i - 2)
            # Find the end of function (next closing brace at same indentation)
            end = i + 1
            brace_count = 0
            for j in range(i, len(lines)):
                brace_count += lines[j].count("{") - lines[j].count("}")
                if brace_count == 0 and j > i:
                    end = j + 1
                    break
            if end > i + 1:
                func_content = "\n".join(lines[start:end])
                return file_path, truncate_hunk(env, func_content, file_path=file_path, func_name=func_name)
    return None


def get_functions_to_try(issue_id: str) -> list[tuple[str, str]]:
    """Get list of (file_path, func_name) tuples to try from pipeline/localize-result.json"""
    pred_funcs, gold_funcs, gold_file = load_func_info_from_json(issue_id)
    
    # If no pred_funcs found, return empty list (will fall back to original method)
    if not pred_funcs:
        return []
    
    functions_to_try = []
    llvm_root = os.path.join(os.path.dirname(__file__), "..", "utils", "llvm", "llvm-project")
    
    # If gold_funcs exist, try to match with pred_funcs
    # pred_funcs is a dict: {"filename.cpp": ["func1", "func2", ...], ...}
    # If there are multiple gold functions, skip matching and directly use all pred funcs.
    if gold_funcs and len(gold_funcs) == 1 and gold_file:
        # Find matching functions
        for pred_file, pred_func_list in pred_funcs.items():
            # pred_func_list is the list of function names for this file
            if not pred_func_list:
                continue
            # Get full path for pred_file
            full_path = get_full_path_from_filename(pred_file, llvm_root)
            # Check if this file matches gold_file (compare basename or full path)
            file_matches = False
            if full_path:
                file_matches = full_path == gold_file
            if not file_matches:
                file_matches = os.path.basename(pred_file) == os.path.basename(gold_file)
            
            if file_matches:
                # Found matching file, check for matching functions
                for pred_func in pred_func_list:
                    for gold_func in gold_funcs:
                        if match_function_name(pred_func, gold_func):
                            # Use gold_file path if available, otherwise use full_path
                            target_path = gold_file if gold_file.startswith("llvm/") else (full_path or gold_file)
                            functions_to_try.append((target_path, pred_func))
                            break
                # If we found matching functions, only use those
                if functions_to_try:
                    return functions_to_try
    
    # If no matches found or no gold_funcs, use all pred_funcs
    # pred_funcs is a dict: {"filename.cpp": ["func1", "func2", ...], ...}
    for pred_file, pred_func_list in pred_funcs.items():
        # pred_func_list is the list of function names for this file
        if not pred_func_list:
            continue
        full_path = get_full_path_from_filename(pred_file, llvm_root)
        for pred_func in pred_func_list:
            functions_to_try.append((full_path or pred_file, pred_func))
    
    return functions_to_try

def extract_code_from_reply(tgt: str):
    # if tgt.startswith("```"):
    #     tgt = tgt.strip().removeprefix("```cpp").removeprefix("```").removesuffix("```")
    #     return tgt
    # Match the last code block
    # re1 = re.compile("```cpp([\s\S]+)```")
    re1 = re.compile(r"```cpp([\s\S]+?)```")
    matches = re.findall(re1, tgt)
    if len(matches) > 0:
        return matches[-1]
    # re2 = re.compile("```([\s\S]+)```")
    re2 = re.compile(r"```([\s\S]+?)```")
    matches = re.findall(re2, tgt)
    if len(matches) > 0:
        return matches[-1]
    return tgt


def modify_inplace(file, src, tgt):
    tgt = extract_code_from_reply(tgt)
    path = os.path.join(llvm_helper.llvm_dir, file)
    with open(path) as f:
        code = f.read()
    code = code.replace(src, tgt)
    with open(path, "w") as f:
        f.write(code)


def get_issue_desc(env: Env) -> str:
    issue = env.get_hint_issue()
    if issue is None:
        return ""
    title = issue["title"]
    body = "<omitted>" if omit_issue_body else issue["body"]
    return f"Issue title: {title}\nIssue body: {body}\n"

format_issle_desc_from_programs = """
The following program reveals the missed optimization.
The source program is
```llvm
{source_program}
```

However, current LLVM cannot optimize the source program. The expected optimized program is
```llvm
{expect_optimized_program}
```
"""
def get_issue_desc_from_programs(env: Env) -> str:
    for test_file in env.get_tests():
        for test in test_file["tests"]:
            if test["current_optimized_program"].strip() != test["expect_optimized_program"].strip():
                return format_issle_desc_from_programs.format(
                    source_program=test["source_program"],
                    current_optimized_program=test["current_optimized_program"],
                    expect_optimized_program=test["expect_optimized_program"],
                )
    raise Exception("No missed optimization found")


def normalize_feedback(log) -> str:
    # if not isinstance(log, list):
    #     if len(log) > max_log_size:
    #         return log[:max_log_size] + "\n<Truncated>..."
    #     return str(log)
    # return json.dumps(llvm_helper.get_first_failed_test(log), indent=2)
    if not isinstance(log, list):
        text = str(log)
        matches = re.compile(r"(Check file:.*?>>>>>>)", re.DOTALL).findall(text)
        if matches:
            extracted = "\n\n".join(matches)
            if len(extracted) > max_log_size:
                return extracted[:max_log_size] + "\n<Truncated>..."
            return extracted
        if len(text) > max_log_size:
            return text[:max_log_size] + "\n<Truncated>..."
        return text
    return json.dumps(llvm_helper.get_first_failed_test(log), indent=2)

def normalize_feedback_with_build_failure(log) -> str:
    if not isinstance(log, list):
        # error_list = re.findall(r"llvm-project/llvm/.* error: .*", str(log))
        # log_str = "\n".join(error_list)
        # if len(log_str) > max_log_size:
        #     return log_str[:max_log_size] + "\n..."
        # return str(log_str)
        pattern = re.compile(r"(llvm-project/llvm/[^\n]+error:[^\n]+(?:\n[ ]*\d+\s*\|[^\n]*)?)")
        matches = pattern.findall(str(log))
        log_str = "\n".join(matches)
        if len(log_str) > max_log_size:
            return log_str[:max_log_size] + "\n..."
        return str(log_str) or "No LLVM errors found."
    return json.dumps(llvm_helper.get_first_failed_test(log), indent=2)

def truncate_text(text: str, *, max_chars: int = 8000) -> str:
    if not isinstance(text, str):
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n<Truncated>..."


def _extract_cost_fields(test_log: dict) -> tuple[dict | None, dict | None]:
    """
    Safely extract cost fields from a test log.

    Returns:
      (cost_dict, log_dict). If any part is missing/malformed, cost_dict is None.
    """
    if not isinstance(test_log, dict):
        return None, None
    log_obj = test_log.get("log", None)
    if not isinstance(log_obj, dict):
        return None, None
    cost_obj = log_obj.get("cost", None)
    if not isinstance(cost_obj, dict):
        return None, log_obj
    required = ("source_program", "expect_optimized_program", "current_optimized_program")
    if not all(k in cost_obj for k in required):
        return None, log_obj
    return {k: cost_obj.get(k) for k in required}, log_obj


def _append_missing_cost_feedback(feedback: str, test_name: str, test_log: dict) -> str:
    log_obj = test_log.get("log", None) if isinstance(test_log, dict) else None
    try:
        rendered = json.dumps(log_obj, ensure_ascii=False, indent=2, default=str)
    except Exception:
        rendered = str(log_obj)
    feedback += (
        f"The test log for `@{test_name}` is missing expected cost fields under "
        f"`log.cost` (need: source_program/expect_optimized_program/current_optimized_program). "
        "Raw `test_log['log']` follows:\n"
        f"```json\n{truncate_text(rendered)}\n```\n"
    )
    return feedback


def get_alive2_log(test_log: dict) -> str | None:
    try:
        a2 = test_log.get("log", {}).get("alive2", {})
        a2log = a2.get("log", None)
        if isinstance(a2log, str) and a2log.strip():
            return a2log
    except Exception:
        pass
    return None


def alive2_log_looks_success(alive2_log: str) -> bool:
    """Heuristic: treat Alive2 as success if it reports 0 incorrect/failed-to-prove/errors."""
    if not isinstance(alive2_log, str):
        return False
    s = alive2_log
    # Typical success strings:
    # - "Transformation seems to be correct!"
    # - "Transformation seems to be correct! (syntactically equal)"
    # - Summary with 0 incorrect / 0 failed-to-prove / 0 errors
    return (
        ("Summary:" in s)
        and ("0 incorrect transformations" in s)
        and ("0 failed-to-prove transformations" in s)
        and ("0 Alive2 errors" in s)
        and ("Transformation seems to be correct" in s)
    )


def test_is_successfully_optimized(test_log: dict) -> bool:
    """Prefer verifier result / IR equality over cost heuristics.

    We observed cases where FileCheck fails (IR != expected) but cost looks
    "good", which previously caused us to incorrectly mark a test as
    successful and skip adding it to the next prompt.
    """
    if isinstance(test_log, dict):
        r = test_log.get("result", None)
        if isinstance(r, bool):
            return r
        try:
            cur = test_log.get("log", {}).get("current_optimized_program", None)
            exp = test_log.get("log", {}).get("expect_optimized_program", None)
            if isinstance(cur, str) and isinstance(exp, str):
                return cur.strip() == exp.strip()
        except Exception:
            pass
        try:
            cost = test_log.get("log", {}).get("cost", {})
            curc = cost.get("current_optimized_program", None)
            srcc = cost.get("source_program", None)
            expc = cost.get("expect_optimized_program", None)
            if all(isinstance(x, (int, float)) for x in [curc, srcc, expc]):
                return (curc < srcc) or (curc <= expc)
        except Exception:
            pass
    return False

def issue_fixing_iter(
    env: Env, file, src, messages, full_messages, context_requirement
):
    env.reset()
    if DEBUG:
        for message in issue_log_debug["log"]["messages"]:
            if message["role"] == "assistant":
                tgt = message["content"]
                break
    else:   
        tgt = chat(env, messages, full_messages)
    modify_inplace(file, src, tgt)
    last_build_failure_count = env.build_failure_count
    res, log = env.check_full()
    if res:
        return True
    if env.build_failure_count > last_build_failure_count:
        # when there is a build failure, the feedback is the key error messages
        feedback = "Your generated code caused the following build failure:\n" + \
            normalize_feedback_with_build_failure(log) + \
            "\nPlease adjust your code according to the feedback."
    elif env.fast_check_pass != True:
        # if the build is successful, we get the remaining unoptimized test program
        # firstly, update the cost of the prompted tests
        for test_name in env.prompted_tests:
            test = env.prompted_tests[test_name]
            for test_log in log:
                if test_log["name"] == test_name:
                    # print("[TEST LOG1]", test_log)   
                    cost_fields, _ = _extract_cost_fields(test_log)
                    if cost_fields is not None:
                        test["cost"]["source_program"] = cost_fields["source_program"]
                        test["cost"]["expect_optimized_program"] = cost_fields["expect_optimized_program"]
                        test["cost"]["current_optimized_program"] = cost_fields["current_optimized_program"]
                        break
                    continue
        # then, check if the optimization was successful
        feedback = ""
        prompted_test_fail_count = 0
        for test_name in env.prompted_tests:
            test = env.prompted_tests[test_name]
            for test_log in log:
                if test_log["name"] == test_name:
                    # print("[TEST LOG2]", test_log)
                    cost_fields, log_obj = _extract_cost_fields(test_log)
                    if cost_fields is not None:
                        test["cost"]["source_program"] = cost_fields["source_program"]
                        test["cost"]["expect_optimized_program"] = cost_fields["expect_optimized_program"]
                        test["cost"]["current_optimized_program"] = cost_fields["current_optimized_program"]
                    else:
                        feedback = _append_missing_cost_feedback(feedback, test_name, test_log)
                    if test_is_successfully_optimized(test_log):
                        feedback += f"The previous test program `@{test_name}` was successfully optimized.\n"
                    else:
                        prompted_test_fail_count += 1
                        feedback += f"The previous test program `@{test_name}` was not successfully optimized.\n"
                        if isinstance(log_obj, dict) and isinstance(log_obj.get("source_program", None), str):
                            feedback += f"The previous test program is:\n```llvm\n{log_obj['source_program']}\n```\n"
                        if isinstance(log_obj, dict) and isinstance(log_obj.get("expect_optimized_program", None), str):
                            feedback += f"The expected optimized program is:\n```llvm\n{log_obj['expect_optimized_program']}\n```\n"
                        if isinstance(log_obj, dict) and isinstance(log_obj.get("current_optimized_program", None), str):
                            feedback += f"But the current optimized program is:\n```llvm\n{log_obj['current_optimized_program']}\n```\n"
                        if not isinstance(log_obj, dict):
                            # fall back: show whatever we have
                            try:
                                rendered = json.dumps(test_log.get("log", None), ensure_ascii=False, indent=2, default=str)
                            except Exception:
                                rendered = str(test_log.get("log", None))
                            feedback += f"Raw log follows:\n```json\n{truncate_text(rendered)}\n```\n"
                        alive2_log = get_alive2_log(test_log)
                        if alive2_log and not alive2_log_looks_success(alive2_log):
                            feedback += (
                                "Alive2 failed to verify the transformation; log follows:\n"
                                f"```text\n{truncate_text(alive2_log)}\n```\n"
                            )
        if prompted_test_fail_count == 0:
            # add new test to the prompt
            for test_file in env.get_tests():
                # print("[TEST FILE]", test_file)
                for test in test_file["tests"]:
                    # print("[TEST]", test)
                    if test["test_name"] in env.prompted_tests:
                        continue
                    for test_log in log:
                        if test_log["name"] != test["test_name"]:
                            continue
                        if test_is_successfully_optimized(test_log):
                            continue
                        cost_fields, log_obj = _extract_cost_fields(test_log)
                        env.prompted_tests[test["test_name"]] = {
                            "cost": {
                                "source_program": (cost_fields or {}).get("source_program", -1),
                                "expect_optimized_program": (cost_fields or {}).get("expect_optimized_program", -1),
                                "current_optimized_program": -1,
                            }
                        }
                        if not isinstance(log_obj, dict):
                            log_obj = test_log.get("log", {}) if isinstance(test_log, dict) else {}
                        source_program = log_obj.get("source_program", "")
                        expect_optimized_program = log_obj.get("expect_optimized_program", "")
                        feedback += f"Here is another failed to optimize program:\n" + \
                            f"The source program is:\n```llvm\n{source_program}\n```\n" + \
                            f"The expect optimized program is:\n```llvm\n{expect_optimized_program}\n```\n"
                        alive2_log = get_alive2_log(test_log)
                        if alive2_log and not alive2_log_looks_success(alive2_log):
                            feedback += (
                                "Alive2 failed to verify the transformation; log follows:\n"
                                f"```text\n{truncate_text(alive2_log)}\n```\n"
                            )
                        # keep prompt sizes under control: add at most one new failing test each round
                        break
                    else:
                        continue
                    break
                else:
                    continue
                break
        feedback += "Please revisit your generated code and adjust it according to the feedback.\n"
    else:
        feedback = "Your generated code has successfully optimized the given programs. However, it caused the behavior change in other programs as revealed by the following log:\n" + \
            normalize_feedback(log) + \
            "\nPlease adjust your code such that it keeps the already-achieved optimization while fixing the behavior change."
    append_message(
        messages,
        full_messages,
        {
            "role": "user",
            "content": feedback + format_requirement + context_requirement,
        },
    )
    return False

def normalize_messages(messages):
    return {"model": model, "messages": messages}


override = False


def fix_issue(issue_id):
    fix_log_path = os.path.join(fix_dir, f"{issue_id}.json")
    if not override and os.path.exists(fix_log_path) and not DEBUG:
        print(f"Skip {issue_id}")
        return
    print(f"Fixing {issue_id}")
    env = Env(issue_id, basemodel_cutoff, max_build_jobs=max_build_jobs)
    verified = env.data.get("verified", False)
    if not verified:
        print(f"Issue {issue_id} is not verified")
        return
    bug_funcs = env.get_hint_bug_functions()
    if not env.is_single_func_fix():
        print("Multi-func bug is not supported")
        return
    messages = []
    full_messages = []  # Log with COT tokens
    append_message(
        messages, full_messages, {"role": "system", "content": get_system_prompt()}
    )
    bug_type = env.get_bug_type()
    bug_file_hint = next(iter(bug_funcs.keys()))
    bug_func_name = next(iter(bug_funcs.values()))[0]
    # component = next(iter(env.get_hint_components()))
    component = next(iter(env.get_hint_components()), None)
    if component is None:
        print("component is None")
        return
    # get the unoptimized test program
    issue_desc = ""
    flag_prompted_test = False
    for test_file in env.get_tests():
        for test in test_file["tests"]:
            if test["current_optimized_program"].strip() != test["expect_optimized_program"].strip():
                issue_desc = format_issle_desc_from_programs.format(
                    source_program=test["source_program"],
                    current_optimized_program=test["current_optimized_program"],
                    expect_optimized_program=test["expect_optimized_program"],
                )
                env.prompted_tests[test["test_name"]] = {
                    "cost": {
                        "source_program": -1,
                        "expect_optimized_program": -1,
                        "current_optimized_program": -1
                    }
                }
                flag_prompted_test = True
                break
        if flag_prompted_test:
            break
        
    desc = f"This is a {bug_type} in {component}.\n"
    desc += issue_desc
    env.reset()
    llvm_helper.llvm_build_dir = os.path.join(os.environ["LAB_LLVM_BUILD_DIR"], task)
    llvm_build_cache_dir = os.path.join(os.environ["LAB_LLVM_BUILD_DIR"], task + "_cache")
    if os.path.exists(llvm_build_cache_dir):
        # first, remove the build directory
        shutil.rmtree(llvm_helper.llvm_build_dir, ignore_errors=True)
        # copy the build directory
        shutil.copytree(llvm_build_cache_dir, llvm_helper.llvm_build_dir)
        res, log = env.check_fast(skip_build=True)
    else:
        res, log = env.check_fast(skip_build=False)
        # copy the build directory
        shutil.copytree(llvm_helper.llvm_build_dir, llvm_build_cache_dir)
            
    # Some case can pass check fast directly, these test cases are skipped.
    assert not res, "Could pass check fast directly without fix."
    
    # Get functions to try from JSON
    functions_to_try = get_functions_to_try(issue_id)
    
    # If no functions found in JSON, fall back to the hint function itself.
    if not functions_to_try:
        result = get_hunk_from_func(env, bug_file_hint, bug_func_name)
        if not result:
            print(
                f"Warning: Cannot extract function {bug_func_name} from {bug_file_hint}, skipping"
            )
            return
        file, hunk = result
        desc += f"Please modify the following code in {file}:{bug_func_name} to implement the missed optimization:\n```cpp\n{hunk}\n```\n"
        prefix = "\n".join(hunk.splitlines()[:5])
        suffix = "\n".join(hunk.splitlines()[-5:])
        context_requirement = f"Please make sure the answer includes the prefix:\n```cpp\n{prefix}\n```\nand the suffix:\n```cpp\n{suffix}\n```\n"
        desc += format_requirement + context_requirement
        append_message(messages, full_messages, {"role": "user", "content": desc})
        # try:
        for idx in range(max_sample_count):
            print(f"Round {idx + 1}")
            if issue_fixing_iter(
                env, file, hunk, messages, full_messages, context_requirement
            ):
                cert = env.dump(normalize_messages(full_messages))
                # print(cert)
                with open(fix_log_path, "w") as f:
                    f.write(json.dumps(cert, indent=2))
                return
        print(messages)
        # except Exception as e:
        #     # import traceback
        #     # print("⚠️ An exception occurred:")
        #     # traceback.print_exc()  
        #     pass

        cert = env.dump(normalize_messages(full_messages))
        with open(fix_log_path, "w") as f:
            f.write(json.dumps(cert, indent=2))
        return
    
    # Try each function separately
    for func_idx, (file_path, func_name) in enumerate(functions_to_try):
        print(f"\nTrying function {func_idx + 1}/{len(functions_to_try)}: {func_name} in {file_path}")
        
        # Reset environment and messages for each function
        env.reset()
        # Restore build cache for each function attempt
        if os.path.exists(llvm_build_cache_dir):
            shutil.rmtree(llvm_helper.llvm_build_dir, ignore_errors=True)
            shutil.copytree(llvm_build_cache_dir, llvm_helper.llvm_build_dir)
        
        messages = []
        full_messages = []
        append_message(
            messages, full_messages, {"role": "system", "content": get_system_prompt()}
        )
        
        # Get function hunk
        result = get_hunk_from_func(env, file_path, func_name)
        if not result:
            print(f"Warning: Cannot extract function {func_name} from {file_path}, skipping")
            continue
        
        file, hunk = result
        
        # Build description for this function
        func_desc = f"This is a {bug_type} in {component}.\n"
        func_desc += issue_desc
        func_desc += f"Please modify the following code in {file}:{func_name} to implement the missed optimization:\n```cpp\n{hunk}\n```\n"
        prefix = "\n".join(hunk.splitlines()[:5])
        suffix = "\n".join(hunk.splitlines()[-5:])
        context_requirement = f"Please make sure the answer includes the prefix:\n```cpp\n{prefix}\n```\nand the suffix:\n```cpp\n{suffix}\n```\n"
        func_desc += format_requirement + context_requirement
        append_message(messages, full_messages, {"role": "user", "content": func_desc})
        
        # Try to fix with this function
        for idx in range(max_sample_count):
            print(f"Round {idx + 1}")
            if issue_fixing_iter(
                env, file, hunk, messages, full_messages, context_requirement
            ):
                # Check if the fix passes both fast_check and full_check
                cert = env.dump(normalize_messages(full_messages))
                with open(fix_log_path, "w") as f:
                    f.write(json.dumps(cert, indent=2))
                print(f"Successfully fixed with function {func_name} in {file_path}")
                return
        
        print(f"Failed to fix with function {func_name} in {file_path}")
    
    # If all functions failed, save the last attempt
    cert = env.dump(normalize_messages(full_messages))
    with open(fix_log_path, "w") as f:
        f.write(json.dumps(cert, indent=2))


if len(sys.argv) == 1:
    task_list = sorted(
        map(lambda x: x.removesuffix(".json"), os.listdir(llvm_helper.dataset_dir))
    )
else:
    task_list = [sys.argv[1]]
    if len(sys.argv) == 3 and sys.argv[2] == "-f":
        override = True

if DEBUG:
    task_list = [issue_id_debug]

for task in task_list:
    # try:
        fix_issue(task)
    # except Exception as e:
    #     print(e)
    #     pass
