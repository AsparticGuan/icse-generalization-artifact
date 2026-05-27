"""从 dataset-patch JSON 中提取 (src_ir, tgt_ir) 文本对。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


def strip_filecheck_lines(text: str) -> str:
    """去掉 FileCheck 的 ; CHECK* 行，保留可执行的 LLVM IR 片段。"""
    out: list[str] = []
    for line in text.splitlines():
        s = line.lstrip()
        if s.startswith("; CHECK"):
            continue
        out.append(line)
    return "\n".join(out).strip()


def format_ir_pair(src_ir: str, tgt_ir: str) -> str:
    """与检索 query 使用同一模板，便于对称相似度（见 retriever 中 encode 策略）。"""
    return f"[SOURCE]\n{src_ir.strip()}\n[/SOURCE]\n[TARGET]\n{tgt_ir.strip()}\n[/TARGET]"


@dataclass
class PatchRecord:
    pr_number: int
    test_name: str
    source_program: str
    target_ir: str
    patch_path: str

    @property
    def document_text(self) -> str:
        return format_ir_pair(self.source_program, self.target_ir)


def iter_patch_records(patch_dir: Path) -> Iterator[PatchRecord]:
    patch_dir = Path(patch_dir)
    for path in sorted(patch_dir.glob("*.json")):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        pr_number = int(data.get("pr_number", 0))
        if not pr_number:
            m = re.match(r"^(\d+)\.json$", path.name)
            if m:
                pr_number = int(m.group(1))
        tests_root = data.get("tests") or []
        for file_entry in tests_root:
            inner = file_entry.get("tests") or []
            for t in inner:
                src = t.get("source_program")
                body = t.get("test_body")
                name = t.get("test_name") or ""
                if not src or body is None:
                    continue
                tgt = strip_filecheck_lines(str(body))
                if not tgt:
                    tgt = str(body)
                yield PatchRecord(
                    pr_number=pr_number,
                    test_name=str(name),
                    source_program=str(src),
                    target_ir=tgt,
                    patch_path=str(path),
                )
