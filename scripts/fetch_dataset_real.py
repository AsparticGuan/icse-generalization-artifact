#!/usr/bin/env python3
"""Fetch open LLVM issues and write dataset-real/{issue_id}.json entries."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from html import unescape
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "dataset-real"

# 20 issues from prior filtering (15 strict + 5 supplemental).
ISSUE_IDS = [
    186554,
    186553,
    186509,
    184131,
    163093,
    157111,
    144020,
    138636,
    134318,
    132908,
    118164,
    72773,
    72651,
    61644,
    56562,
    200661,
    199806,
    198082,
    186957,
    169763,
]

LLVM_BLOCK_RE = re.compile(r"```llvm\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
GENERIC_CODE_RE = re.compile(r"```\s*\n((?:target |define |declare ).*?)```", re.DOTALL)
DEFINE_RE = re.compile(r"define\b[^@]*@([A-Za-z0-9_.]+)\s*\(", re.MULTILINE)
ALIVE2_LINK_RE = re.compile(r"https://alive2\.llvm\.org/ce/z/[A-Za-z0-9_-]+")
OG_DESC_RE = re.compile(
    r'<meta\s+property="og:description"\s+content="([^"]*)"',
    re.DOTALL,
)


def github_get(url: str) -> dict | list:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "auto-opt-dataset-real",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def fetch_issue(issue_id: int) -> dict:
    issue = github_get(
        f"https://api.github.com/repos/llvm/llvm-project/issues/{issue_id}"
    )
    comments = github_get(issue["comments_url"])
    return issue, comments


def _keep_llvm_block(block: str) -> bool:
    if "define " not in block:
        return False
    if "#0:" in block or "%lpo_" in block:
        return False
    return True


def extract_llvm_blocks(text: str) -> list[str]:
    out: list[str] = []
    for pattern in (LLVM_BLOCK_RE, GENERIC_CODE_RE):
        for m in pattern.finditer(text or ""):
            block = m.group(1).strip()
            if _keep_llvm_block(block):
                out.append(block)
    if not out and text and "define " in text:
        candidate = text.strip()
        if _keep_llvm_block(candidate):
            out.append(candidate)
    return out


def split_arrow_block(block: str) -> tuple[str, str] | None:
    if "=>" not in block:
        return None
    left, right = re.split(r"\s*=>\s*", block, maxsplit=1)
    left, right = left.strip(), right.strip()
    if "define " not in left or "define " not in right:
        return None
    return left, right


ARROW_LINE_RE = re.compile(r"^\s*=>\s*$", re.MULTILINE)


def normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def strip_ir_garbage(text: str) -> str:
    text = normalize_text(text).strip()
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "=>":
            break
        if stripped.lower().startswith("transformation seems"):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def extract_define_functions(text: str) -> dict[str, str]:
    text = strip_ir_garbage(normalize_text(text))
    funcs: dict[str, str] = {}
    pos = 0
    while pos < len(text):
        m = re.search(r"^define\b", text[pos:], re.MULTILINE)
        if not m:
            break
        start = pos + m.start()
        fm = DEFINE_RE.search(text, start)
        if not fm:
            break
        name = fm.group(1)
        brace = text.find("{", fm.end())
        if brace < 0:
            break
        depth = 0
        end = brace
        for i in range(brace, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        else:
            break
        funcs[name] = text[start:end].strip() + "\n"
        pos = end
    return funcs


def functions_in_block(block: str) -> dict[str, str]:
    return extract_define_functions(block)


def pick_src_tgt(funcs: dict[str, str]) -> tuple[str, str, str, str] | None:
    if "src" in funcs and "tgt" in funcs:
        return "src", funcs["src"], "tgt", funcs["tgt"]
    if "tgt" in funcs:
        tgt_name = "tgt"
        others = [n for n in funcs if n != "tgt"]
        if not others:
            return None
        src_name = "src" if "src" in others else others[0]
        return src_name, funcs[src_name], tgt_name, funcs[tgt_name]
    names = list(funcs.keys())
    if len(names) >= 2:
        return names[0], funcs[names[0]], names[1], funcs[names[1]]
    return None


def merge_module(functions: list[str]) -> str:
    decls: list[str] = []
    defines: list[str] = []
    for fn in functions:
        for line in fn.splitlines():
            if line.startswith("target ") or line.startswith(";"):
                if line not in decls:
                    decls.append(line)
            elif line.startswith("declare "):
                if line not in decls:
                    decls.append(line)
            elif line.startswith("attributes "):
                if line not in decls:
                    decls.append(line)
        defines.append(fn.strip() + "\n")
    header = "\n".join(decls).strip()
    body = "\n".join(defines).strip() + "\n"
    return (header + "\n\n" + body) if header else body


def rename_function(program: str, old: str, new: str) -> str:
    return re.sub(rf"@{re.escape(old)}\b", f"@{new}", program)


def infer_components(labels: list[str]) -> list[str]:
    mapping = {
        "llvm:instcombine": "InstCombine",
        "llvm:instsimplify": "InstSimplify",
        "llvm:aggressiveinstcombine": "AggressiveInstCombine",
    }
    comps: list[str] = []
    for label in labels:
        key = label.lower()
        if key in mapping:
            comps.append(mapping[key])
    if not comps and any("instcombine" in x.lower() for x in labels):
        comps.append("InstCombine")
    return sorted(set(comps)) or ["InstCombine"]


def fetch_alive2_ir(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "auto-opt"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError:
        return None
    m = OG_DESC_RE.search(html)
    if not m:
        return None
    return unescape(m.group(1))


def pair_from_functions(funcs: dict[str, str]) -> tuple[str, str] | None:
    picked = pick_src_tgt(funcs)
    if not picked:
        return None
    src_name, src_ir, tgt_name, tgt_ir = picked
    return (
        rename_function(src_ir, src_name, "test"),
        rename_function(tgt_ir, tgt_name, "test"),
    )


def extract_pair_from_text(text: str) -> tuple[str, str] | None:
    text = normalize_text(text or "")
    if ARROW_LINE_RE.search(text):
        parts = ARROW_LINE_RE.split(text, maxsplit=1)
        if len(parts) == 2:
            src_funcs = extract_define_functions(parts[0])
            tgt_funcs = extract_define_functions(parts[1])
            if src_funcs and tgt_funcs:
                pair = pair_from_functions({**src_funcs, **tgt_funcs})
                if pair:
                    return pair

    blocks = extract_llvm_blocks(text)
    merged_funcs: dict[str, str] = {}
    for block in blocks:
        merged_funcs.update(extract_define_functions(block))
    if merged_funcs:
        pair = pair_from_functions(merged_funcs)
        if pair:
            return pair

    if "define " in text:
        pair = pair_from_functions(extract_define_functions(text))
        if pair:
            return pair
    return None


def build_record(issue_id: int) -> dict:
    issue, comments = fetch_issue(issue_id)
    labels = [x["name"] for x in issue.get("labels", [])]
    body = issue.get("body") or ""

    alive2_links = list(
        dict.fromkeys(
            u if u.startswith("http") else f"https://{u}"
            for u in ALIVE2_LINK_RE.findall(body)
        )
    )
    pair = extract_pair_from_text(body)

    def _valid(p: tuple[str, str] | None) -> bool:
        return p is not None and p[0].strip() != p[1].strip()

    if not _valid(pair):
        for url in alive2_links:
            ir = fetch_alive2_ir(url)
            if ir:
                alive_pair = extract_pair_from_text(ir)
                if _valid(alive_pair):
                    pair = alive_pair
                    break
            time.sleep(0.3)

    if pair is None:
        raise RuntimeError(f"#{issue_id}: cannot extract src/tgt IR")

    source_program = strip_ir_garbage(pair[0]) + "\n"
    expect_optimized_program = strip_ir_garbage(pair[1]) + "\n"

    issue_comments = [
        {"author": c["user"]["login"], "body": c.get("body") or ""}
        for c in comments
    ]

    return {
        "bug_id": str(issue_id),
        "issue_url": issue["html_url"],
        "bug_type": "missed-optimization",
        "base_commit": "",
        "knowledge_cutoff": issue["created_at"],
        "lit_test_dir": [],
        "hints": {
            "fix_commit": "",
            "components": infer_components(labels),
            "bug_location_lineno": {},
            "bug_location_funcname": {},
        },
        "patch": "",
        "tests": [
            {
                "file": "issue_body_ir.ll",
                "commands": [],
                "tests": [
                    {
                        "test_name": "test",
                        "test_body": "",
                        "source_program": source_program,
                        "expect_optimized_program": expect_optimized_program,
                        "current_optimized_program": source_program,
                    }
                ],
            }
        ],
        "dev_tests": [],
        "issue": {
            "title": issue.get("title") or "",
            "body": body,
            "author": issue["user"]["login"],
            "labels": labels,
            "comments": issue_comments,
        },
        "properties": {
            "is_single_file_fix": False,
            "is_single_func_fix": False,
        },
        "verified": False,
    }


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    failed: list[str] = []
    for issue_id in ISSUE_IDS:
        out_path = OUTPUT_DIR / f"{issue_id}.json"
        try:
            record = build_record(issue_id)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2, ensure_ascii=False)
                f.write("\n")
            print(f"[OK] #{issue_id} -> {out_path}")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] #{issue_id}: {exc}")
            failed.append(str(issue_id))
        time.sleep(0.5)
    print(f"\nDone: {ok}/{len(ISSUE_IDS)} succeeded")
    if failed:
        print("Failed:", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
