r"""
Overnight scrape -> merge -> shard pipeline, run once per machine.

Python (not bash) specifically because tonight's machines are one Mac, one
Ubuntu, and one Windows box -- a shell script needs WSL/Git Bash on Windows
and has inconsistent background-process semantics there, while this runs
identically everywhere via plain `python3`/`python`.

This script itself has zero third-party dependencies (stdlib only), so run
it directly with the system interpreter -- do NOT run it via `uv run`,
since there is no top-level pyproject.toml (backend/ and scraper/ are
separate uv projects; this script shells into each of those with `uv run`
internally, using their own environments). Before the first run on a
machine, sync both projects and install the scraper's browser once:
  uv sync --directory backend
  uv sync --directory scraper
  uv run --directory scraper playwright install chromium

Machines are on different networks, so the handoff between them travels
through the shared git remote (GitHub) rather than direct machine-to-machine
transfer (rsync/scp) -- nothing here assumes the machines can reach each
other directly, only that each can reach GitHub.

One machine is the "coordinator" -- effectively the master computer files
get collected onto, except the collection happens via git push/pull through
GitHub rather than a direct file copy, since the machines aren't on the same
network. After the scrape window ends, the coordinator pulls (= collects)
every machine's scraped data, merges it into the final truckpaper_clean.jsonl,
and pushes that back out; once all shards exist (pulled the same way) it
merges them into the final DINO index. Every other machine scrapes its own
keywords, waits for the coordinator's clean dataset, shards its slice, and
pushes (= sends to the master) the result. Shard files travel via git too
(force-added past .gitignore), since nobody's awake to move files by hand
overnight.

Usage (3 machines: pick one as --coordinator, e.g. whichever you'll check
on first in the morning):
  python3 scripts/overnight_pipeline.py --shard 0 --total 3 --hours 8 \\
      --coordinator --keywords "Sleeper Truck" "Day Cab Truck"

  python3 scripts/overnight_pipeline.py --shard 1 --total 3 --hours 8 \\
      --keywords "Volvo VNL 860" "Freightliner Coronado"

  python scripts/overnight_pipeline.py --shard 2 --total 3 --hours 8 \\
      --keywords "Kenworth W900" "Mack Anthem"

This must keep running even if the terminal/Terminal.app/PowerShell window
it was launched from gets closed overnight -- a plain foreground process
dies with its controlling terminal (SIGHUP on Mac/Linux; console detach on
Windows). Launch it detached instead:

  Mac / Ubuntu (tmux -- also lets you reattach and watch progress):
    tmux new -s fleetworth
    python3 scripts/overnight_pipeline.py --shard 0 --total 3 --hours 8 \\
        --coordinator --keywords "..." "..."
    # detach: Ctrl-b then d      reattach later: tmux attach -t fleetworth
  Mac / Ubuntu (no tmux available -- nohup + disown):
    nohup python3 scripts/overnight_pipeline.py --shard 0 --total 3 --hours 8 \\
        --coordinator --keywords "..." "..." > overnight_logs/console.log 2>&1 &
    disown

  Windows (PowerShell -- Start-Process detaches from the console entirely):
    Start-Process -FilePath python -WindowStyle Hidden `
      -ArgumentList @("scripts/overnight_pipeline.py","--shard","2","--total","3","--hours","8","--keywords","...","...") `
      -RedirectStandardOutput "overnight_logs\console.log" `
      -RedirectStandardError "overnight_logs\console_err.log"

Also keep the machine itself from sleeping (a Mac must not sleep either --
`caffeinate` or the Amphetamine app; Windows: Settings > Power > never sleep,
or `powercfg /change standby-timeout-ac 0`) -- a detached process still dies
if the OS suspends the machine.
"""
import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / "overnight_logs"
SCRAPE_PROGRESS_PATH = REPO_ROOT / "data" / "truckpaper_raw_progress.json"
CLEAN_READY_MARKER = "AUTO: clean dataset ready"


def log(msg: str, log_file: Path) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with log_file.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True)


def has_staged_changes() -> bool:
    return git("diff", "--cached", "--quiet").returncode != 0


def _commit_pending(message: str) -> None:
    """Stage+commit any dirty tracked files (e.g. the scraper still appending
    to truckpaper_raw.jsonl) so `git pull` never refuses with "local changes
    would be overwritten" -- `git add -u` only touches already-tracked files,
    never sweeps in new untracked ones."""
    git("add", "-u")
    if has_staged_changes():
        git("commit", "-q", "-m", message)


def git_sync(
    message: str,
    log_file: Path,
    critical: bool = False,
    max_attempts: int | None = None,
    sleep_seconds: int | None = None,
) -> bool:
    """Commit whatever's staged (if anything), then pull+push with retries.

    critical=True is for a one-shot handoff (final scrape push, clean
    dataset, shard result, final merge) that MUST land -- there's no "next
    cycle" for these, so they retry far more persistently than a routine
    mid-scrape progress push, and loudly say so if they still can't land
    rather than letting the caller believe it succeeded.
    """
    _commit_pending(message)

    attempts = max_attempts if max_attempts is not None else (20 if critical else 5)
    sleep_s = sleep_seconds if sleep_seconds is not None else (30 if critical else 15)

    for attempt in range(1, attempts + 1):
        # a fast-writing scraper can dirty a tracked file again between
        # attempts (or even between the commit above and this first pull) --
        # re-commit right before every attempt so pull can never be blocked.
        _commit_pending(message)
        pull = git("pull", "--no-edit", "-q")
        if pull.returncode == 0:
            push = git("push", "-q")
            if push.returncode == 0:
                return True
        log(f"git_sync: attempt {attempt}/{attempts} failed, retrying in {sleep_s}s", log_file)
        time.sleep(sleep_s)

    if critical:
        log("!" * 70, log_file)
        log(f"!! CRITICAL PUSH FAILED after {attempts} attempts: '{message}'", log_file)
        log(f"!! This machine's work was NOT shared -- run `git push` by hand", log_file)
        log(f"!! from {socket.gethostname()}:{REPO_ROOT} in the morning.", log_file)
        log("!" * 70, log_file)
    else:
        log(f"git_sync: giving up after {attempts} attempts (will retry next cycle)", log_file)
    return False


def run_uv(args: list[str], cwd: Path, log_file: Path) -> int:
    """Run `uv run python <args>` in cwd, streaming output into log_file."""
    proc = subprocess.run(["uv", "run", "python", *args], cwd=cwd, capture_output=True, text=True)
    with log_file.open("a", encoding="utf-8") as f:
        f.write(proc.stdout)
        f.write(proc.stderr)
    print(proc.stdout, end="")
    print(proc.stderr, end="", file=sys.stderr)
    return proc.returncode


def scraper_keywords_complete(keywords: list[str], progress_path: Path = SCRAPE_PROGRESS_PATH) -> bool:
    """Return true only when the scraper persisted completion for every keyword.

    scrape_truckpaper.py catches individual page-load failures and can still
    exit with code 0, so the process return code alone cannot distinguish
    genuine exhaustion from a recoverable interrupted scrape.
    """
    try:
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(progress, dict) and all(
        isinstance(progress.get(keyword), dict) and progress[keyword].get("done") is True
        for keyword in keywords
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--shard", type=int, required=True, help="this machine's shard index (0-based)")
    parser.add_argument("--total", type=int, required=True, help="total number of shards/machines")
    parser.add_argument("--hours", type=float, required=True, help="how long to scrape before cutting over")
    parser.add_argument("--coordinator", action="store_true", help="this machine merges the final dataset and index")
    parser.add_argument("--keywords", nargs="+", required=True, help="scraper keywords for this machine")
    # Overrides for testing; overnight defaults are sane on their own.
    parser.add_argument("--push-interval-seconds", type=int, default=1200)
    parser.add_argument("--poll-interval-seconds", type=int, default=300)
    parser.add_argument("--max-poll-seconds", type=int, default=14400)
    parser.add_argument("--min-restart-remaining-seconds", type=int, default=360,
                         help="don't bother restarting a crashed/exhausted scraper with less than this much of the window left")
    args = parser.parse_args()

    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"pipeline_shard{args.shard}.log"
    host = socket.gethostname()
    log(f"starting on {host} ({platform.system()}): shard {args.shard}/{args.total}, "
        f"coordinator={args.coordinator}, keywords={args.keywords}", log_file)

    # ---- Phase 1: scrape, with periodic auto-push, for `hours` hours ----
    scrape_log = LOG_DIR / f"scrape_shard{args.shard}.log"
    scraper_dir = REPO_ROOT / "scraper"
    MAX_SCRAPE_RESTARTS = 30  # generous backstop against a true crash-loop eating the whole night

    def launch_scraper() -> subprocess.Popen:
        sf = scrape_log.open("a", encoding="utf-8")
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}  # else stdout->file is block-buffered and the log looks frozen for minutes at a time
        proc = subprocess.Popen(
            ["uv", "run", "python", "scrape_truckpaper.py", *args.keywords],
            cwd=scraper_dir, stdout=sf, stderr=subprocess.STDOUT, env=env,
        )
        sf.close()  # the child holds its own fd; safe to close our handle immediately
        return proc

    log(f"Phase 1: scraping for {args.hours}h with keywords {args.keywords}", log_file)
    scrape_proc = launch_scraper()
    restarts = 0

    end_time = time.monotonic() + args.hours * 3600
    while time.monotonic() < end_time:
        # Don't sleep past the scrape window just because the next periodic
        # push is scheduled later; the cutover should happen promptly.
        time.sleep(min(args.push_interval_seconds, max(0, end_time - time.monotonic())))
        if scrape_proc.poll() is not None:
            exit_code = scrape_proc.returncode
            if exit_code == 0 and scraper_keywords_complete(args.keywords):
                # Continuing to relaunch an exhausted scraper only repeats the
                # same no-op and delays the clean/shard phases.
                log("scraper completed successfully before the window ended -- moving to the next phase", log_file)
                break
            remaining_seconds = end_time - time.monotonic()
            remaining_hours = remaining_seconds / 3600
            # Restarting is safe either way: if the keyword list is genuinely
            # exhausted, scrape_truckpaper.py's own per-keyword progress
            # tracking marks each one "done" and the relaunch just exits
            # again almost immediately (harmless no-op); if it crashed
            # (network hiccup, a page structure surprise, etc.), the same
            # progress tracking means it resumes from wherever it left off
            # instead of rescraping from page 1.
            if remaining_seconds > args.min_restart_remaining_seconds and restarts < MAX_SCRAPE_RESTARTS:
                restarts += 1
                log(f"scraper exited (code {exit_code}) with {remaining_hours:.1f}h left in the window -- "
                    f"restarting (attempt {restarts}/{MAX_SCRAPE_RESTARTS})", log_file)
                time.sleep(10)  # brief backoff so a truly instant repeat failure doesn't spin hot
                scrape_proc = launch_scraper()
            else:
                reason = "out of restart budget" if restarts >= MAX_SCRAPE_RESTARTS else "window nearly over"
                log(f"scraper exited (code {exit_code}); not restarting ({reason})", log_file)
        git("add", "data/truckpaper_raw.jsonl")
        git_sync(f"auto: scrape progress from shard {args.shard} ({host})", log_file)

    log("Phase 1 done: scrape window elapsed, stopping scraper", log_file)
    if scrape_proc.poll() is None:
        scrape_proc.terminate()
        try:
            scrape_proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            scrape_proc.kill()
    git("add", "data/truckpaper_raw.jsonl")
    if not git_sync(f"auto: final scrape progress from shard {args.shard} ({host})", log_file, critical=True):
        log("continuing despite the failed push above -- later critical pushes will retry sharing everything", log_file)

    # ---- Phase 2: coordinator builds the final clean dataset; others wait ----
    if args.coordinator:
        log("Phase 2 (coordinator): merging + cleaning", log_file)
        git("pull", "--no-edit", "-q")  # pull in the other machine's last push before cleaning
        rc = run_uv(["clean_data.py"], cwd=scraper_dir, log_file=log_file)
        if rc != 0:
            log(f"ABORTING: clean_data.py failed (exit {rc}) -- not pushing a broken/stale dataset", log_file)
            return 1
        git("add", "data/truckpaper_clean.jsonl", "data/base_prices.json", "data/truckpaper_raw.jsonl")
        if not git_sync(CLEAN_READY_MARKER, log_file, critical=True):
            log("ABORTING: could not share the clean dataset -- see the CRITICAL PUSH FAILED banner above", log_file)
            return 1
    else:
        log("Phase 2: waiting for coordinator's clean dataset", log_file)
        waited = 0
        while waited < args.max_poll_seconds:
            git("pull", "--no-edit", "-q")
            # Check the last 20 commit subjects, not just the tip -- a pull
            # that produces a merge commit shadows the coordinator's actual
            # marker commit behind a synthetic "Merge branch..." message, so
            # checking only the latest one can miss a marker that's already
            # merged in and present.
            recent_subjects = git("log", "-20", "--format=%s").stdout
            if CLEAN_READY_MARKER in recent_subjects:
                log("clean dataset detected", log_file)
                break
            time.sleep(args.poll_interval_seconds)
            waited += args.poll_interval_seconds
        else:
            log(f"WARNING: gave up waiting for clean dataset after {args.max_poll_seconds/3600:.1f}h "
                f"-- sharding whatever truckpaper_clean.jsonl is currently present", log_file)

    # ---- Phase 3: everyone shards their slice ----
    backend_dir = REPO_ROOT / "backend"
    log(f"Phase 3: sharding {args.shard}/{args.total}", log_file)
    subprocess.run(["uv", "sync", "-q"], cwd=backend_dir)
    rc = run_uv(["build_dino_index.py", "--shard", f"{args.shard}/{args.total}"], cwd=backend_dir, log_file=log_file)
    if rc != 0:
        log(f"ABORTING: sharding failed (exit {rc}) -- not pushing broken/missing shard output", log_file)
        return 1
    stem = f"shard_{args.shard}_of_{args.total}"
    git("add", "-f", f"data/dino_shards/{stem}.npy", f"data/dino_shards/{stem}.json")
    if not git_sync(f"auto: shard {args.shard}/{args.total} ready ({host})", log_file, critical=True):
        log(f"ABORTING: shard {args.shard} could not be shared -- see the CRITICAL PUSH FAILED banner above", log_file)
        return 1

    # ---- Phase 4: coordinator waits for all shards, then merges ----
    if args.coordinator:
        log(f"Phase 4 (coordinator): waiting for all {args.total} shards", log_file)
        waited = 0
        shard_dir = REPO_ROOT / "data" / "dino_shards"
        while waited < args.max_poll_seconds:
            git("pull", "--no-edit", "-q")
            missing = [i for i in range(args.total) if not (shard_dir / f"shard_{i}_of_{args.total}.npy").exists()]
            if not missing:
                break
            time.sleep(args.poll_interval_seconds)
            waited += args.poll_interval_seconds
        else:
            missing = [i for i in range(args.total) if not (shard_dir / f"shard_{i}_of_{args.total}.npy").exists()]

        merge_args = ["build_dino_index.py", "--merge", str(args.total)]
        if missing:
            merge_args.append("--allow-missing-shards")
            log(f"WARNING: shard(s) {missing} never arrived, merging with --allow-missing-shards", log_file)
        rc = run_uv(merge_args, cwd=backend_dir, log_file=log_file)
        if rc != 0:
            log(f"ABORTING: merge failed (exit {rc}), e.g. a mismatched-hash shard was refused -- "
                f"NOT pushing stale/incomplete index. Check the log above, fix the offending "
                f"shard, and rerun `--merge {args.total}` by hand.", log_file)
            return 1
        git("add", "-f", "data/dino_index/comps.faiss", "data/dino_index/comps_meta.json")
        if git_sync(f"auto: final DINO index merged from {args.total} shards", log_file, critical=True):
            log("Phase 4 done: final index ready and pushed", log_file)
        else:
            log("Phase 4 FAILED: index was built locally but NOT pushed -- see the CRITICAL PUSH FAILED banner above", log_file)
            return 1

    log(f"Pipeline complete for shard {args.shard}", log_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
