"""用法: 在 auto-opt 根目录执行 python -m RAG build-index | python -m RAG query ..."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
