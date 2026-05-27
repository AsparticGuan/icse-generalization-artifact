"""默认路径相对于 auto-opt 项目根目录。"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATCH_DIR = PROJECT_ROOT / "dataset-patch"
DEFAULT_SUMMARY_DIR = PROJECT_ROOT / "results" / "generalize-summaries"
DEFAULT_INDEX_DIR = Path(__file__).resolve().parent / "index_store"
DEFAULT_MODEL_ID = "Qwen/Qwen3-Embedding-4B"
