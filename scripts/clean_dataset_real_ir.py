#!/usr/bin/env python3
"""Re-extract and normalize LLVM IR in dataset-real/*.json."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Reuse extraction helpers from fetch script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_dataset_real as fr  # noqa: E402

DATASET_REAL = Path(__file__).resolve().parent.parent / "dataset-real"

ARROW_LINE_RE = re.compile(r"^\s*=>\s*$", re.MULTILINE)
GARBAGE_LINE_RE = re.compile(
    r"^(?:Transformation seems.*|(?:\r?\n)?=>\s*)$",
    re.IGNORECASE | re.MULTILINE,
)
INSTCOMBINE_COMMANDS = [
    [
        "opt < %s -passes=instcombine -S",
        "FileCheck %s",
    ]
]


def normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def strip_ir_garbage(text: str) -> str:
    text = normalize_text(text).strip()
    lines = []
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
        fm = fr.DEFINE_RE.search(text, start)
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


def extract_pair_from_text(text: str) -> tuple[str, str] | None:
    text = normalize_text(text or "")
    # Alive2 / issue arrow syntax on its own line.
    if ARROW_LINE_RE.search(text):
        parts = ARROW_LINE_RE.split(text, maxsplit=1)
        if len(parts) == 2:
            src_funcs = extract_define_functions(parts[0])
            tgt_funcs = extract_define_functions(parts[1])
            if src_funcs and tgt_funcs:
                pair = fr.pair_from_functions(
                    {
                        **({"src": src_funcs["src"]} if "src" in src_funcs else {}),
                        **({"tgt": tgt_funcs["tgt"]} if "tgt" in tgt_funcs else {}),
                    }
                )
                if not pair and len(src_funcs) == 1 and len(tgt_funcs) == 1:
                    s_name, s_ir = next(iter(src_funcs.items()))
                    t_name, t_ir = next(iter(tgt_funcs.items()))
                    pair = (
                        fr.rename_function(s_ir, s_name, "test"),
                        fr.rename_function(t_ir, t_name, "test"),
                    )
                if not pair:
                    pair = fr.pair_from_functions({**src_funcs, **tgt_funcs})
                if pair:
                    return pair

    blocks = fr.extract_llvm_blocks(text)
    merged: dict[str, str] = {}
    for block in blocks:
        merged.update(extract_define_functions(block))
    if merged:
        pair = fr.pair_from_functions(merged)
        if pair:
            return pair

    # Plain IR without markdown fences.
    if "define " in text:
        pair = fr.pair_from_functions(extract_define_functions(text))
        if pair:
            return pair
    return None


def finalize_ir(ir: str) -> str:
    ir = strip_ir_garbage(ir)
    if not ir.endswith("\n"):
        ir += "\n"
    return ir


def sources_for_record(data: dict) -> list[str]:
    texts: list[str] = []
    body = (data.get("issue") or {}).get("body") or ""
    if body:
        texts.append(body)
    for url in fr.ALIVE2_LINK_RE.findall(body):
        full = url if url.startswith("http") else f"https://{url}"
        ir = fr.fetch_alive2_ir(full)
        if ir:
            texts.append(ir)
    return texts


def clean_record(data: dict) -> tuple[dict, bool]:
    pair: tuple[str, str] | None = None
    for text in sources_for_record(data):
        pair = extract_pair_from_text(text)
        if pair and pair[0].strip() != pair[1].strip():
            break
        pair = None

    if not pair:
        raise RuntimeError(f"#{data.get('bug_id')}: cannot extract clean src/tgt IR")

    source_program = finalize_ir(pair[0])
    expect_optimized_program = finalize_ir(pair[1])

    for group in data.get("tests", []):
        group["commands"] = INSTCOMBINE_COMMANDS
        for test in group.get("tests", []):
            test["source_program"] = source_program
            test["expect_optimized_program"] = expect_optimized_program
            test["current_optimized_program"] = source_program
            if not (test.get("test_body") or "").strip():
                test["test_body"] = ""

    return data, True


def validate_ir(ir: str, bug_id: str, field: str) -> None:
    ir_n = normalize_text(ir)
    if not ir_n.strip():
        raise ValueError(f"#{bug_id} {field}: empty IR")
    if not re.search(r"^define\b.*@test\s*\(", ir_n, re.MULTILINE):
        raise ValueError(f"#{bug_id} {field}: missing define @test")
    if re.search(r"^\s*=>\s*$", ir_n, re.MULTILINE):
        raise ValueError(f"#{bug_id} {field}: contains arrow separator line")
    if "Transformation seems" in ir_n:
        raise ValueError(f"#{bug_id} {field}: contains transformation message")


def main() -> int:
    failed: list[str] = []
    for path in sorted(DATASET_REAL.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data, _ = clean_record(data)
            for group in data.get("tests", []):
                for test in group.get("tests", []):
                    bug_id = data.get("bug_id", path.stem)
                    for field in (
                        "source_program",
                        "expect_optimized_program",
                        "current_optimized_program",
                    ):
                        validate_ir(test[field], bug_id, field)
            path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print(f"[OK] {path.name}")
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {path.name}: {exc}")
            failed.append(path.name)
    if failed:
        print("Failed:", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
