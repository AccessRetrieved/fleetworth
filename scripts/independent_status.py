r"""
Status check for independent_pipeline.py's per-machine runs -- the
variable-N, no-coordinator design (see independent_pipeline.py and
combine_independent_shards.py). Companion to shard_status.py, which reads
overnight_pipeline.py's older coordinator-based commit message shapes; this
one reads independent_pipeline.py's shapes instead. Both read purely from
git history, so this works against any number of machines with zero
cooperation needed from them -- same reasoning as shard_status.py.

Reports, per machine name seen in commit history:
  - current phase (scraping / embedding&done) inferred from which commit
    types have landed
  - lines contributed to the shared raw dataset so far
  - whether its independent shard (data/independent_shards/<name>.npy/.json)
    has actually landed on origin -- this is the real "is this machine's
    work safely saved" signal, independent of whether the machine itself
    (e.g. a RunPod pod set to self-terminate) is still reachable at all
  - staleness (minutes since last commit), flagged once it exceeds ~2x a
    typical push interval

Usage:
    python3 scripts/independent_status.py [branch]     # default: dino-sharding
"""
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = "data/truckpaper_raw.jsonl"

PATTERNS = [
    (re.compile(r"^auto: scrape progress from (\S+) \(([^)]+)\)"), "scraping"),
    (re.compile(r"^auto: final scrape progress from (\S+) \(([^)]+)\)"), "scrape done"),
    (re.compile(r"^auto: independent shard '([^']+)' ready \(([^,)]+)"), "shard ready"),
]


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout


def added_lines(commit_hash: str) -> int:
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

    log = git("log", "--format=%H|%ct|%s", f"origin/{branch}").strip().splitlines()

    now = time.time()
    machines: dict[str, dict] = {}

    seen_hashes = set()
    for raw in log:
        h, ts_s, subject = raw.split("|", 2)
        if h in seen_hashes:
            continue
        seen_hashes.add(h)
        ts = int(ts_s)

        for pattern, phase in PATTERNS:
            m = pattern.match(subject)
            if not m:
                continue
            name, host = m.group(1), m.group(2)
            entry = machines.setdefault(
                name, {"host": host, "commits": [], "phase": "scraping", "shard_ready": False}
            )
            lines_added = added_lines(h) if phase in ("scraping", "scrape done") else 0
            entry["commits"].append((ts, phase, lines_added))
            # phases only ever move forward: scraping -> scrape done -> shard ready
            order = {"scraping": 0, "scrape done": 1, "shard ready": 2}
            if order[phase] >= order[entry["phase"]] or entry["phase"] == "scraping":
                entry["phase"] = phase
            if phase == "shard ready":
                entry["shard_ready"] = True
            break

    if not machines:
        print(f"No independent_pipeline.py activity found yet on origin/{branch}.")
        return 0

    # Cross-check shard_ready claims against the actual files on origin --
    # a commit message alone doesn't prove the push fully landed if history
    # got rewritten/force-pushed since; the tree listing is the ground truth.
    tree_files = set(
        git("ls-tree", "-r", "--name-only", f"origin/{branch}", "--",
            "data/independent_shards/").strip().splitlines()
    )

    print(f"=== Independent-pipeline status (origin/{branch}) ===\n")
    for name in sorted(machines):
        info = machines[name]
        commits = sorted(info["commits"])
        total_lines = sum(c[2] for c in commits)
        last_ts = commits[-1][0] if commits else None
        stale_min = (now - last_ts) / 60 if last_ts else None

        npy_on_origin = f"data/independent_shards/{name}.npy" in tree_files
        json_on_origin = f"data/independent_shards/{name}.json" in tree_files
        shard_confirmed = npy_on_origin and json_on_origin

        if shard_confirmed:
            status = "DONE -- shard confirmed saved on origin"
        elif info["phase"] == "shard ready":
            status = "shard commit seen, but files not found in tree (⚠ check manually)"
        elif info["phase"] == "scrape done":
            status = "scrape finished, embedding/pushing shard now"
        else:
            status = "scraping"

        flag = ""
        if stale_min is not None and stale_min > 45 and not shard_confirmed:
            flag = f"  ⚠ STALE ({stale_min:.0f} min since last activity -- may be stuck or dropped)"

        print(f"{name}  [{info['host']}]  status: {status}{flag}")
        print(f"  commits: {len(commits)}   lines contributed so far: {total_lines}")
        if last_ts:
            print(f"  last activity: {stale_min:.1f} min ago")
        print()

    confirmed = sum(1 for n in machines if f"data/independent_shards/{n}.npy" in tree_files)
    print(f"{confirmed}/{len(machines)} machines have a confirmed, saved shard on origin.")
    print("Once all expected machines show DONE, run scripts/combine_independent_shards.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
