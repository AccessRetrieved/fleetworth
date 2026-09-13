r"""
Coordinator-side status check for the overnight scrape+shard pipeline.

Deliberately does NOT require any code change or cooperation from the other
machines -- their `overnight_pipeline.py` processes are already running in
memory with nobody awake to restart them, so nothing new can be injected
into their live loop. Instead this reads everything it needs out of git
history, which every machine already writes to via its normal periodic
`git_sync()` commits (see overnight_pipeline.py) -- so it works today,
against machines running the OLD code, with zero changes on their end.

Usage (run from the repo root on any machine with git access, typically
the coordinator):
  python3 scripts/shard_status.py [branch]     # default branch: dino-sharding
"""
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = "data/truckpaper_raw.jsonl"

# Matches the exact commit-message shapes overnight_pipeline.py's git_sync()
# calls produce -- see scripts/overnight_pipeline.py's four git_sync() call
# sites for the source of truth.
PATTERNS = [
    # not anchored with trailing $ -- manual-recovery commits append extra
    # free-text after the closing paren (e.g. "... (hp-server) [manual recovery]")
    (re.compile(r"^auto: scrape progress from shard (\d+) \(([^)]+)\)"), "scrape progress"),
    (re.compile(r"^auto: final scrape progress from shard (\d+) \(([^)]+)\)"), "FINAL scrape push"),
    (re.compile(r"^auto: shard (\d+)/(\d+) ready \(([^)]+)\)"), "shard ready"),
    (re.compile(r"^auto: final DINO index merged from (\d+) shards"), "FINAL index merge"),
]


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout


def added_lines(commit_hash: str) -> int:
    """Lines this specific commit added to DATA_FILE (its own diff against
    its parent) -- meaningful even though the file is shared/union-merged,
    since git still records each commit's own diff independently."""
    try:
        out = git("show", "--numstat", "--format=", commit_hash, "--", DATA_FILE)
        line = out.strip().splitlines()
        if not line:
            return 0
        added, _removed, _path = line[0].split("\t")
        return int(added) if added != "-" else 0
    except Exception:
        return 0


def main() -> int:
    branch = sys.argv[1] if len(sys.argv) > 1 else "dino-sharding"
    git("fetch", "origin", "-q")

    log = git(
        "log", "--format=%H|%ct|%s", f"origin/{branch}", "--", DATA_FILE
    ).strip().splitlines()
    log += git("log", "--format=%H|%ct|%s", f"origin/{branch}").strip().splitlines()

    now = time.time()
    # shard -> {host, commits: [(ts, kind, lines_added)], ready: bool, final_scrape: bool}
    shards: dict[str, dict] = {}
    final_merge_ts = None

    seen_hashes = set()
    for raw in log:
        h, ts_s, subject = raw.split("|", 2)
        if h in seen_hashes:
            continue
        seen_hashes.add(h)
        ts = int(ts_s)

        for pattern, kind in PATTERNS:
            m = pattern.match(subject)
            if not m:
                continue
            if kind == "FINAL index merge":
                final_merge_ts = ts
                break
            shard_idx = m.group(1)
            host = m.group(2) if m.lastindex and m.lastindex >= 2 and kind != "shard ready" else None
            if kind == "shard ready":
                host = m.group(3)
            entry = shards.setdefault(shard_idx, {"host": host, "commits": [], "ready": False, "final_scrape": False})
            entry["host"] = host or entry["host"]
            lines_added = added_lines(h) if kind in ("scrape progress", "FINAL scrape push") else 0
            entry["commits"].append((ts, kind, lines_added))
            if kind == "shard ready":
                entry["ready"] = True
            if kind == "FINAL scrape push":
                entry["final_scrape"] = True
            break

    if not shards:
        print(f"No shard activity found yet on origin/{branch}.")
        return 0

    print(f"=== Shard status (origin/{branch}) ===\n")
    for shard_idx in sorted(shards, key=int):
        info = shards[shard_idx]
        commits = sorted(info["commits"])
        total_lines = sum(c[2] for c in commits)
        last_ts = commits[-1][0] if commits else None
        stale_min = (now - last_ts) / 60 if last_ts else None
        status = "READY (sharded)" if info["ready"] else (
            "scrape done, awaiting shard" if info["final_scrape"] else "scraping"
        )
        flag = ""
        if stale_min is not None and stale_min > 45 and not info["ready"]:
            flag = f"  ⚠ STALE ({stale_min:.0f} min since last push -- may be stuck)"

        print(f"Shard {shard_idx}  [{info['host'] or 'unknown host'}]  status: {status}{flag}")
        print(f"  commits: {len(commits)}   lines contributed so far: {total_lines}")
        if last_ts:
            print(f"  last push: {stale_min:.1f} min ago")
        print()

    if final_merge_ts:
        print(f"Final DINO index merge landed {(now - final_merge_ts) / 60:.1f} min ago.")
    else:
        print("Final DINO index merge: not yet landed.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
