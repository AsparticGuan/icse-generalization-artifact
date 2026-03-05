#!/usr/bin/env python3
"""
Check how many closed InstCombine PRs in llvm-project modified test cases.
Uses GitHub REST API (unauthenticated, rate-limited).
Strategy: fetch all PR numbers via search API, then sample-check files.
"""

import urllib.request
import json
import time
import random
import sys

REPO = "llvm/llvm-project"
SEARCH_URL = "https://api.github.com/search/issues"
PR_FILES_URL = "https://api.github.com/repos/{}/pulls/{}/files"

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
            if "403" in str(e) or "rate" in str(e).lower():
                print(f"  Rate limited, waiting 60s... (attempt {attempt+1})")
                time.sleep(60)
            else:
                print(f"  Error: {e}, retrying in 5s...")
                time.sleep(5)
    return None

def fetch_all_pr_numbers():
    """Fetch PR numbers using search API (max 1000 results due to GitHub limit)."""
    all_prs = []
    query = "is:pr+is:closed+instcombine+in:title+repo:llvm/llvm-project"
    
    for page in range(1, 11):  # 10 pages × 100 = 1000 max
        url = f"{SEARCH_URL}?q={query}&per_page=100&page={page}&sort=created&order=desc"
        print(f"Fetching search page {page}...", end=" ", flush=True)
        data = api_get(url)
        if not data or "items" not in data:
            print("failed")
            break
        items = data["items"]
        if not items:
            print("no more results")
            break
        for item in items:
            all_prs.append({
                "number": item["number"],
                "title": item["title"],
            })
        print(f"got {len(items)} PRs (total so far: {len(all_prs)})")
        time.sleep(6.5)  # search API: 10 req/min
    
    return all_prs

def check_pr_files(pr_number):
    """Check if a PR modified test files."""
    url = PR_FILES_URL.format(REPO, pr_number) + "?per_page=100"
    data = api_get(url)
    if not data:
        return None
    
    test_files = []
    all_files = []
    for f in data:
        fname = f.get("filename", "")
        all_files.append(fname)
        if "/test/" in fname or fname.endswith(".ll"):
            test_files.append(fname)
    
    return {
        "total_files": len(all_files),
        "test_files": test_files,
        "has_test": len(test_files) > 0
    }

def main():
    print("=" * 60)
    print("Fetching all InstCombine closed PR numbers...")
    print("=" * 60)
    all_prs = fetch_all_pr_numbers()
    print(f"\nTotal PRs fetched: {len(all_prs)}")
    
    # Save all PR numbers
    with open("/data/btguan/auto-opt/instcombine_prs.json", "w") as f:
        json.dump(all_prs, f, indent=2)
    print(f"Saved PR list to instcombine_prs.json")
    
    # Sample PRs for file checking (max ~55 to stay within rate limit)
    sample_size = min(55, len(all_prs))
    sample = random.sample(all_prs, sample_size)
    
    print(f"\n{'=' * 60}")
    print(f"Checking files for {sample_size} sampled PRs...")
    print(f"{'=' * 60}")
    
    has_test = 0
    no_test = 0
    errors = 0
    
    for i, pr in enumerate(sample):
        print(f"[{i+1}/{sample_size}] PR #{pr['number']}: {pr['title'][:60]}...", end=" ", flush=True)
        result = check_pr_files(pr["number"])
        if result is None:
            print("ERROR")
            errors += 1
            continue
        
        if result["has_test"]:
            has_test += 1
            print(f"✓ TEST ({len(result['test_files'])} test files / {result['total_files']} total)")
        else:
            no_test += 1
            print(f"✗ NO TEST ({result['total_files']} files)")
        
        time.sleep(1.2)  # be gentle with rate limit
    
    print(f"\n{'=' * 60}")
    print(f"RESULTS (sample of {sample_size} from {len(all_prs)} PRs)")
    print(f"{'=' * 60}")
    checked = has_test + no_test
    if checked > 0:
        pct = has_test / checked * 100
        print(f"PRs with test changes:    {has_test}/{checked} ({pct:.1f}%)")
        print(f"PRs without test changes: {no_test}/{checked} ({100-pct:.1f}%)")
        print(f"Errors:                   {errors}")
        print(f"\nEstimated total PRs with test changes: ~{int(len(all_prs) * pct / 100)} / {len(all_prs)}")
    
if __name__ == "__main__":
    main()
