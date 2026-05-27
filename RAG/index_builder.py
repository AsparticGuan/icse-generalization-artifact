"""构建并持久化向量索引。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from tqdm import tqdm

from .config import DEFAULT_INDEX_DIR, DEFAULT_MODEL_ID, DEFAULT_PATCH_DIR
from .dataset import PatchRecord, iter_patch_records


def _normalize_rows(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    return mat / norms


def build_corpus_records(patch_dir: Path) -> list[PatchRecord]:
    return list(iter_patch_records(patch_dir))


def encode_corpus(
    model: Any,
    records: list[PatchRecord],
    batch_size: int = 4,
    show_progress: bool = True,
) -> np.ndarray:
    texts = [r.document_text for r in records]
    iterator = range(0, len(texts), batch_size)
    if show_progress:
        iterator = tqdm(list(iterator), desc="Embedding corpus")
    chunks: list[np.ndarray] = []
    for start in iterator:
        batch = texts[start : start + batch_size]
        emb = model.encode(
            batch,
            batch_size=len(batch),
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        chunks.append(np.asarray(emb, dtype=np.float32))
    return np.vstack(chunks) if chunks else np.zeros((0, 0), dtype=np.float32)


def save_index(
    index_dir: Path,
    records: list[PatchRecord],
    embeddings: np.ndarray,
) -> None:
    index_dir = Path(index_dir)
    index_dir.mkdir(parents=True, exist_ok=True)
    meta = [
        {
            "pr_number": r.pr_number,
            "test_name": r.test_name,
            "source_program": r.source_program,
            "target_ir": r.target_ir,
            "patch_path": r.patch_path,
        }
        for r in records
    ]
    (index_dir / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=0), encoding="utf-8"
    )
    np.save(index_dir / "embeddings.npy", embeddings.astype(np.float32))


def load_metadata(index_dir: Path) -> list[dict[str, Any]]:
    path = Path(index_dir) / "metadata.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_embeddings(index_dir: Path) -> np.ndarray:
    return np.load(Path(index_dir) / "embeddings.npy")


def build_and_save(
    patch_dir: Path | None = None,
    index_dir: Path | None = None,
    model_id: str = DEFAULT_MODEL_ID,
    batch_size: int = 4,
    device: str | None = None,
) -> tuple[int, Path]:
    from sentence_transformers import SentenceTransformer

    patch_dir = Path(patch_dir or DEFAULT_PATCH_DIR)
    index_dir = Path(index_dir or DEFAULT_INDEX_DIR)
    records = build_corpus_records(patch_dir)
    if not records:
        raise RuntimeError(f"在 {patch_dir} 未发现可用的 patch 记录")

    model_device = "cuda" if device is None else device
    model = SentenceTransformer(model_id, device=model_device)

    emb = encode_corpus(model, records, batch_size=batch_size)
    emb = _normalize_rows(emb)
    save_index(index_dir, records, emb)
    (index_dir / "model_id.txt").write_text(model_id, encoding="utf-8")
    return len(records), index_dir
