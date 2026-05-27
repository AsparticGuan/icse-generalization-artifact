"""用 Qwen3-Embedding 做 IR 对检索，并挂载 generalize-summaries。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .config import DEFAULT_INDEX_DIR, DEFAULT_MODEL_ID, DEFAULT_SUMMARY_DIR
from .dataset import format_ir_pair
from .index_builder import load_embeddings, load_metadata


def _normalize_rows(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    return mat / norms


@dataclass
class RetrievalHit:
    pr_number: int
    test_name: str
    score: float
    source_ir: str
    target_ir: str
    patch_path: str
    summary: str | None
    summary_path: str | None
    summary_title: str | None


class IRPairRetriever:
    def __init__(
        self,
        index_dir: Path | None = None,
        summary_dir: Path | None = None,
        model_id: str | None = None,
        device: str | None = None,
    ) -> None:
        self.index_dir = Path(index_dir or DEFAULT_INDEX_DIR)
        self.summary_dir = Path(summary_dir or DEFAULT_SUMMARY_DIR)
        mid_path = self.index_dir / "model_id.txt"
        self.model_id = (
            model_id
            or (mid_path.read_text(encoding="utf-8").strip() if mid_path.is_file() else None)
            or DEFAULT_MODEL_ID
        )
        self._device = device
        self._model: Any = None
        self._corpus: np.ndarray | None = None
        self._meta: list[dict[str, Any]] | None = None

    def _get_model(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            kwargs: dict[str, Any] = {}
            if self._device:
                kwargs["device"] = self._device
            self._model = SentenceTransformer(self.model_id, **kwargs)
        return self._model

    def load_index(self) -> None:
        self._corpus = load_embeddings(self.index_dir).astype(np.float32)
        self._corpus = _normalize_rows(self._corpus)
        self._meta = load_metadata(self.index_dir)

    @property
    def corpus_ready(self) -> bool:
        return self._corpus is not None and self._meta is not None

    def _ensure_loaded(self) -> None:
        if not self.corpus_ready:
            self.load_index()

    def _read_summary(self, pr_number: int) -> tuple[str | None, str | None, str | None]:
        path = self.summary_dir / f"{pr_number}_summary.json"
        if not path.is_file():
            return None, str(path), None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None, str(path), None
        summary = data.get("summary")
        title = data.get("title")
        if summary is not None:
            summary = str(summary)
        if title is not None:
            title = str(title)
        return summary, str(path), title

    def encode_query(self, src_ir: str, tgt_ir: str) -> np.ndarray:
        """按 Qwen3 文档建议：query 侧使用 prompt_name='query'。"""
        self._ensure_loaded()
        model = self._get_model()
        text = format_ir_pair(src_ir, tgt_ir)
        prompts = getattr(model, "prompts", None) or {}
        use_query_prompt = "query" in prompts
        if use_query_prompt:
            emb = model.encode(
                [text],
                prompt_name="query",
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        else:
            emb = model.encode(
                [text],
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        v = np.asarray(emb, dtype=np.float32)
        return _normalize_rows(v)[0]

    def retrieve(
        self,
        src_ir: str,
        tgt_ir: str,
        top_k: int = 5,
        dedupe_pr: bool = False,
    ) -> list[RetrievalHit]:
        self._ensure_loaded()
        assert self._corpus is not None and self._meta is not None
        q = self.encode_query(src_ir, tgt_ir)
        scores = (self._corpus @ q).astype(np.float64)
        order = np.argsort(-scores)
        hits: list[RetrievalHit] = []
        seen_pr: set[int] = set()
        for idx in order:
            if len(hits) >= top_k:
                break
            pr = int(self._meta[idx]["pr_number"])
            if dedupe_pr and pr in seen_pr:
                continue
            seen_pr.add(pr)
            summ, spath, title = self._read_summary(pr)
            hits.append(
                RetrievalHit(
                    pr_number=pr,
                    test_name=str(self._meta[idx].get("test_name", "")),
                    score=float(scores[idx]),
                    source_ir=str(self._meta[idx]["source_program"]),
                    target_ir=str(self._meta[idx]["target_ir"]),
                    patch_path=str(self._meta[idx]["patch_path"]),
                    summary=summ,
                    summary_path=spath,
                    summary_title=title,
                )
            )
        return hits
