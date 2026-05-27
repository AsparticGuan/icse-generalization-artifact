"""命令行：构建索引 / 检索。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 支持直接执行 `python RAG/cli.py ...`：
# 这种情况下 __package__ 为空，需要把项目根目录加入 sys.path。
if __package__ in {None, ""}:
    _project_root = Path(__file__).resolve().parents[1]
    root_str = str(_project_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


def cmd_build(args: argparse.Namespace) -> int:
    try:
        from .index_builder import build_and_save
    except ImportError:
        from RAG.index_builder import build_and_save

    n, out = build_and_save(
        patch_dir=Path(args.patch_dir) if args.patch_dir else None,
        index_dir=Path(args.index_dir) if args.index_dir else None,
        model_id=args.model,
        batch_size=args.batch_size,
        device=args.device,
    )
    print(f"已索引 {n} 条 IR 对，输出目录: {out}", file=sys.stderr)
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    try:
        from .retriever import IRPairRetriever
    except ImportError:
        from RAG.retriever import IRPairRetriever

    src = Path(args.src_file).read_text(encoding="utf-8")
    tgt = Path(args.tgt_file).read_text(encoding="utf-8")
    r = IRPairRetriever(
        index_dir=Path(args.index_dir) if args.index_dir else None,
        summary_dir=Path(args.summary_dir) if args.summary_dir else None,
        model_id=args.model if args.model else None,
        device=args.device,
    )
    hits = r.retrieve(src, tgt, top_k=args.top_k, dedupe_pr=args.dedupe_pr)
    out = []
    for h in hits:
        out.append(
            {
                "pr_number": h.pr_number,
                "test_name": h.test_name,
                "score": h.score,
                "source_ir": h.source_ir,
                "target_ir": h.target_ir,
                "patch_path": h.patch_path,
                "summary": h.summary,
                "summary_path": h.summary_path,
                "summary_title": h.summary_title,
            }
        )
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="LLVM IR 对 RAG 检索（Qwen3-Embedding）")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build-index", help="扫描 dataset-patch 并写入向量索引")
    b.add_argument("--patch-dir", type=str, default=None)
    b.add_argument("--index-dir", type=str, default=None)
    b.add_argument("--model", type=str, default="Qwen/Qwen3-Embedding-4B")
    b.add_argument("--batch-size", type=int, default=4)
    b.add_argument("--device", type=str, default="cuda", help="默认 cuda；无 GPU 时用 --device cpu")
    b.set_defaults(func=cmd_build)

    q = sub.add_parser("query", help="给定 src/tgt IR 文件，输出 Top-K 与 summary")
    q.add_argument("src_file", type=str)
    q.add_argument("tgt_file", type=str)
    q.add_argument("--index-dir", type=str, default=None)
    q.add_argument("--summary-dir", type=str, default=None)
    q.add_argument("--model", type=str, default=None)
    q.add_argument("--top-k", type=int, default=5)
    q.add_argument(
        "--dedupe-pr",
        action="store_true",
        help="每个 PR 只保留一条最高分命中（适合按 patch 粒度浏览）",
    )
    q.add_argument("--device", type=str, default=None)
    q.set_defaults(func=cmd_query)

    args = p.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
