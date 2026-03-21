#!/usr/bin/env python3
"""Scan results/agent for all traj.json and write traj_index.json for the HTML viewer."""
from __future__ import annotations

import json
import os
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent
INDEX_PATH = AGENT_DIR / "traj_index.json"


def main() -> None:
    entries = []
    for root, _dirs, files in os.walk(AGENT_DIR):
        if "traj.json" not in files:
            continue
        traj_path = Path(root) / "traj.json"
        try:
            rel = traj_path.relative_to(AGENT_DIR)
        except ValueError:
            continue
        # rel like: openai-gpt-5.4/122235/20260315-135436/traj.json
        parts = rel.parts
        if len(parts) < 4:
            continue
        model = parts[0]
        issue_id = parts[1]
        run_id = parts[2]
        exit_status = ""
        api_calls = None
        try:
            with open(traj_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            info = data.get("info", {})
            exit_status = info.get("exit_status", "")
            submission = (info.get("submission") or "").strip()
            ms = info.get("model_stats", {})
            api_calls = ms.get("api_calls")
        except Exception:
            pass
        entries.append({
            "path": str(rel).replace("\\", "/"),
            "model": model,
            "issue_id": issue_id,
            "run_id": run_id,
            "exit_status": exit_status,
            "submission": bool(exit_status == "Submitted" or bool(submission)),
            "api_calls": api_calls,
        })
    entries.sort(key=lambda e: (e["model"], int(e["issue_id"]) if e["issue_id"].isdigit() else 0, e["run_id"]))
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump({"trajectories": entries}, f, indent=2, ensure_ascii=False)
    print(f"Wrote {len(entries)} entries to {INDEX_PATH}")


if __name__ == "__main__":
    main()
