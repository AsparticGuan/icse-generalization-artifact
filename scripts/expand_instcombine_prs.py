#!/usr/bin/env python3
"""
Expand instcombine_prs.json with older PRs (lower numbers).
GitHub search limits to 1000 results; use created:<date to fetch older batches.
"""

import json
import os
import time
import urllib.request

REPO = "llvm/llvm-project"
SEARCH_URL = "https://api.github.com/search/issues"
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PR_LIST_FILE = os.path.join(PROJECT_DIR, "instcombine_prs.json")


def api_get(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "Mozilla/5.0"
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            if "403" in str(e) or "rate" in str(e).lower() or "429" in str(e):
                wait = 60
                print(f"  Rate limited, waiting {wait}s... (attempt {attempt+1})")
                time.sleep(wait)
            else:
                print(f"  Error: {e}, retrying in 5s...")
                time.sleep(5)
    return None


def fetch_prs_before_date(before_date, max_pages=11):
    """Fetch closed InstCombine PRs created before given date (YYYY-MM-DD)."""
    all_prs = []
    query = f"is:pr+is:closed+instcombine+in:title+repo:{REPO}+created:<{before_date}"

    for page in range(1, max_pages):
        url = f"{SEARCH_URL}?q={query}&per_page=100&page={page}&sort=created&order=desc"
        print(f"  Fetching page {page} (created<{before_date})...", end=" ", flush=True)
        data = api_get(url)
        if not data or "items" not in data:
            print("failed")
            break
        items = data["items"]
        if not items:
            print("no more results")
            break
        for item in items:
            all_prs.append({"number": item["number"], "title": item["title"]})
        print(f"got {len(items)} (total: {len(all_prs)})")
        time.sleep(6.5)
        if len(items) < 100:
            break

    return all_prs


def main():
    with open(PR_LIST_FILE) as f:
        existing = json.load(f)

    existing_nums = {p["number"] for p in existing}
    min_pr = min(p["number"] for p in existing)
    print(f"Current: {len(existing)} PRs, min number: {min_pr}")

    # Multiple cutoffs to get more (GitHub returns max 1000 per query)
    cutoffs = ["2024-01-17", "2023-01-01", "2022-01-01", "2020-01-01", "2017-01-01"]
    all_added = []

    for cutoff in cutoffs:
        print(f"\nFetching PRs (created<{cutoff})...")
        older = fetch_prs_before_date(cutoff)
        added_this = []
        for p in older:
            if p["number"] not in existing_nums:
                existing_nums.add(p["number"])
                added_this.append(p)
                all_added.append(p)
        print(f"  New from this batch: {len(added_this)}")
        if len(older) < 100:
            break

    # Build final list: existing + all_added, sort by number desc
    final = existing + all_added
    final.sort(key=lambda p: p["number"], reverse=True)

    with open(PR_LIST_FILE, "w") as f:
        json.dump(final, f, indent=2)

    print(f"\nAdded {len(all_added)} new PRs total")
    print(f"Total: {len(final)} PRs (was {len(existing)})")
    if all_added:
        print(f"New range: #{min(p['number'] for p in all_added)} .. #{max(p['number'] for p in all_added)}")


if __name__ == "__main__":
    main()
