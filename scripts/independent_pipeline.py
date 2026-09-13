r"""
Independent per-machine scrape + local-clean + embed pipeline.

Unlike overnight_pipeline.py (which has every machine wait for ONE
coordinator to produce a single shared truckpaper_clean.jsonl before any
embedding starts), this variant has each machine scrape, then IMMEDIATELY
clean + embed its own locally-visible data the moment its own scrape window
ends -- no waiting on anyone else. This removes the idle time machines
otherwise spend polling for a coordinator's marker, at the cost of a
correctness step deferred to tomorrow: cross-machine relist dedup and one
authoritative base_prices.json can only be computed once, over everyone's
combined data (see scripts/combine_independent_shards.py, run once, by a
human, in the morning).

Use this only when every machine's owner is awake/reachable tonight -- if
you can't guarantee that, use overnight_pipeline.py instead, since a
machine that goes fully unattended mid-run here has no marker to wait for
and no coordinator will ever chase it down.

Each machine still pushes its raw scraped listings to the same shared,
union-merged data/truckpaper_raw.jsonl as overnight_pipeline.py does, so
nothing scraped is lost or siloed -- only the clean/embed step is kept
local and unsynchronized until tomorrow's combine step.

This is a variable-N design: any number of machines (2, 3, 4, 6, ...) can
run this concurrently with no change to the script and no shared count
configured anywhere -- there is no --total/--shard-index/--coordinator flag
because there is nothing to coordinate. Each machine just needs a unique
--name; combine_independent_shards.py discovers however many shards
actually show up the next morning via a plain glob, so machines can be
added, dropped, or fail mid-run without anyone updating a machine count.

Usage (run once per machine, no --coordinator/--total/--shard-index
coordination needed since there's no shared slicing scheme here). Example
fleet of 5 mixed machines -- 2 RunPod GPU pods, a MacBook, a Windows 11 PC,
and an Ubuntu HP server; 2, 3, 4, 6, or any other count/mix works
identically, just pick a unique --name per machine and split --keywords
however you like:
    # machine 1 (RunPod GPU pod #1, Linux)
    python3 scripts/independent_pipeline.py --name runpod-1 --hours 6 \
        --keywords "Freightliner Cascadia" "Peterbilt 389"
    # machine 2 (RunPod GPU pod #2, Linux)
    python3 scripts/independent_pipeline.py --name runpod-2 --hours 6 \
        --keywords "Kenworth T680" "Volvo VNL"
    # machine 3 (MacBook)
    python3 scripts/independent_pipeline.py --name macbook --hours 6 \
        --keywords "Mack Anthem" "Western Star 4900"
    # machine 4 (Windows 11 PC)
    python3 scripts/independent_pipeline.py --name win-pc --hours 6 \
        --keywords "International LT" "Dump Truck"
    # machine 5 (HP server, Ubuntu)
    python3 scripts/independent_pipeline.py --name hp-server --hours 6 \
        --keywords "Box Truck" "Service Truck"

--name must be unique per machine -- it's used to namespace this machine's
pushed shard files (data/independent_shards/<name>.npy / .json) so any
number of machines running this concurrently never write to the same path.

Cross-platform: this script and combine_independent_shards.py are pure
Python + pathlib (no POSIX-only shell syntax, no OS-specific path
separators) and only shell out to `git` and `uv`, both of which ship real
native executables on macOS, Windows 11, and Linux (RunPod containers and a
plain Ubuntu box alike) -- so the same command line works unchanged across
all of them. It runs fine as an unprivileged user on a Mac, as a normal
user or PowerShell/cmd session on Windows 11 (Python 3.11+, uv, and git
must be on PATH; git for Windows and the official uv installer both
provide this), as an ordinary user on an Ubuntu server, and as the root
user typical of a RunPod pod. GPU vs CPU embedding in Phase 3 is decided
automatically by backend/build_dino_index.py (CUDA on a RunPod GPU pod or
a CUDA-equipped Ubuntu box, MPS on Apple Silicon, CPU fallback on a
GPU-less Windows PC) -- nothing in this script needs to know which
platform or accelerator a given machine has.
"""
import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / "overnight_logs"
INDEPENDENT_SHARD_DIR = REPO_ROOT / "data" / "independent_shards"
MAX_SCRAPE_RESTARTS = 30


def log(msg: str, log_file: Path) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with log_file.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


_GIT_LOCK = threading.Lock()  # the periodic embed thread and the main loop's own
# scrape-progress pushes both run git commands against the SAME working tree --
# without this, a pull from one can race a commit/write from the other (dirty
# file mid-pull, or literal git index contention), causing repeated, otherwise
# unexplained git_sync failures. Every git() call serializes through here.


def git(*args: str) -> subprocess.CompletedProcess:
    with _GIT_LOCK:
        return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True)


def has_staged_changes() -> bool:
    return git("diff", "--cached", "--quiet").returncode != 0


def _commit_pending(message: str) -> None:
    git("add", "-u")
    if has_staged_changes():
        git("commit", "-q", "-m", message)


def git_sync(message: str, log_file: Path, critical: bool = False,
             max_attempts: int | None = None, sleep_seconds: int | None = None) -> bool:
    """Same retry/re-commit design as overnight_pipeline.py's git_sync --
    see that file for the reasoning (re-commits before every pull attempt
    so a fast-writing scraper can't outrun the sync loop)."""
    _commit_pending(message)
    attempts = max_attempts if max_attempts is not None else (20 if critical else 5)
    sleep_s = sleep_seconds if sleep_seconds is not None else (30 if critical else 15)

    for attempt in range(1, attempts + 1):
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
        log(f"git_sync: giving up after {attempts} attempts", log_file)
    return False


def shard_part_paths(npy_path: Path, parts: int) -> list[Path]:
    """Mirror of build_dino_index.shard_part_paths (kept stdlib-only here):
    <stem>.npy, then <stem>.part01.npy, <stem>.part02.npy, ..."""
    return [npy_path] + [npy_path.with_name(f"{npy_path.stem}.part{k:02d}.npy") for k in range(1, parts)]


def run_uv(args: list[str], cwd: Path, log_file: Path) -> int:
    proc = subprocess.run(["uv", "run", "python", *args], cwd=cwd, capture_output=True, text=True)
    with log_file.open("a", encoding="utf-8") as f:
        f.write(proc.stdout)
        f.write(proc.stderr)
    print(proc.stdout, end="")
    print(proc.stderr, end="", file=sys.stderr)
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True, help="unique per-machine name, used to namespace pushed shard files")
    parser.add_argument("--hours", type=float, required=True, help="how long to scrape before cleaning+embedding")
    parser.add_argument("--keywords", nargs="+", required=True, help="scraper keywords for this machine")
    parser.add_argument("--push-interval-seconds", type=int, default=1200)
    parser.add_argument("--min-restart-remaining-seconds", type=int, default=360)
    parser.add_argument("--embed-interval-seconds", type=int, default=1800,
                         help="how often to run a local clean+embed+push cycle WHILE scraping continues "
                              "in the background, in addition to the final one after the scrape window ends "
                              "(0 disables periodic cycles -- embed only once, at the end, like before)")
    args = parser.parse_args()

    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"independent_{args.name}.log"
    host = socket.gethostname()
    log(f"starting on {host} ({platform.system()}) as '{args.name}': keywords={args.keywords}", log_file)

    scrape_log = LOG_DIR / f"scrape_{args.name}.log"
    scraper_dir = REPO_ROOT / "scraper"
    backend_dir = REPO_ROOT / "backend"
    shard_dir = REPO_ROOT / "data" / "dino_shards"

    def launch_scraper() -> subprocess.Popen:
        sf = scrape_log.open("a", encoding="utf-8")
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        proc = subprocess.Popen(
            ["uv", "run", "python", "scrape_truckpaper.py", *args.keywords],
            cwd=scraper_dir, stdout=sf, stderr=subprocess.STDOUT, env=env,
        )
        sf.close()
        return proc

    def embed_cycle(final: bool) -> int:
        """Local clean + embed + push this machine's shard. Runs either in a
        background thread WHILE the scraper keeps running (final=False, a
        periodic progress snapshot -- failures are logged but non-fatal,
        since a mid-run hiccup here shouldn't take down the scraper), or
        once, blocking, after the scraper has already stopped (final=True,
        the one that MUST succeed and pushes critically).

        Each cycle re-cleans and re-embeds ALL currently-visible listings,
        not just what's new since the last cycle -- build_dino_index.py has
        no incremental/append mode, though already-downloaded images are
        skipped via its own on-disk cache, so only new images cost real
        time. On a GPU this is cheap even repeated; on a slow CPU-only
        machine, consider raising --embed-interval-seconds or passing 0 to
        disable periodic cycles and only embed once at the end.
        """
        tag = "final" if final else "periodic"
        # Same commit-message shape regardless of final/periodic -- both
        # overwrite the same destination path, so whichever lands last
        # simply supersedes the previous one; independent_status.py's
        # "shard ready" pattern doesn't need to (and shouldn't) distinguish
        # them, since only the latest file's presence on origin matters.
        label = f"auto: independent shard '{args.name}' ready ({host}"

        log(f"embed cycle ({tag}): pulling latest raw data, then cleaning locally", log_file)
        # The scraper writes to truckpaper_raw.jsonl continuously in a
        # separate process, so a bare pull here would routinely fail with
        # "local changes would be overwritten" -- commit whatever's pending
        # first (like git_sync does), retrying a few times since a fast
        # writer can still slip a new line in between commit and pull.
        for _ in range(5):
            _commit_pending(f"auto: scrape progress from {args.name} ({host}) [pre-embed]")
            if git("pull", "--no-edit", "-q").returncode == 0:
                break
            time.sleep(5)
        else:
            log(f"embed cycle ({tag}): could not cleanly pull before cleaning -- proceeding with local data anyway", log_file)
        rc = run_uv(["clean_data.py"], cwd=scraper_dir, log_file=log_file)
        if rc != 0:
            log(f"embed cycle ({tag}): local clean_data.py failed (exit {rc})", log_file)
            return 1

        log(f"embed cycle ({tag}): embedding this machine's local clean dataset", log_file)
        subprocess.run(["uv", "sync", "-q"], cwd=backend_dir)
        rc = run_uv(["build_dino_index.py", "--shard", "0/1"], cwd=backend_dir, log_file=log_file)
        if rc != 0:
            log(f"embed cycle ({tag}): local embedding failed (exit {rc})", log_file)
            return 1

        src_npy, src_json = shard_dir / "shard_0_of_1.npy", shard_dir / "shard_0_of_1.json"
        if not src_npy.exists() or not src_json.exists():
            log(f"embed cycle ({tag}): expected shard_0_of_1.npy/.json not found after embedding", log_file)
            return 1
        INDEPENDENT_SHARD_DIR.mkdir(parents=True, exist_ok=True)
        dst_npy = INDEPENDENT_SHARD_DIR / f"{args.name}.npy"
        dst_json = INDEPENDENT_SHARD_DIR / f"{args.name}.json"
        with src_json.open(encoding="utf-8") as f:
            payload = json.load(f)
        n_items = len(payload["items"])
        # Vectors may be split across several files to stay under GitHub's
        # 100 MB limit (build_dino_index.save_shard_vectors): copy every part,
        # and drop parts left over from an earlier, larger cycle.
        n_parts = payload.get("vector_parts") or 1
        dst_parts = shard_part_paths(dst_npy, n_parts)
        for stale in INDEPENDENT_SHARD_DIR.glob(f"{args.name}.part*.npy"):
            if stale not in dst_parts:
                git("rm", "-q", "--cached", "--ignore-unmatch", str(stale.relative_to(REPO_ROOT)))
                stale.unlink()
        for src, dst in zip(shard_part_paths(src_npy, n_parts), dst_parts):
            dst.write_bytes(src.read_bytes())
        dst_json.write_bytes(src_json.read_bytes())
        log(f"embed cycle ({tag}): wrote {n_items} embedded items ({n_parts} vector file(s)) to {dst_npy} / {dst_json}", log_file)

        git("add", "-f", *(str(p.relative_to(REPO_ROOT)) for p in dst_parts), str(dst_json.relative_to(REPO_ROOT)))
        message = f"{label}, {n_items} items)"
        ok = git_sync(message, log_file, critical=final)
        if not ok:
            log(f"embed cycle ({tag}): could not push shard '{args.name}'"
                + (" -- see CRITICAL PUSH FAILED banner above" if final else " -- will retry next cycle"),
                log_file)
            return 1
        return 0

    # ---- Phase 1: scrape, with periodic auto-push AND periodic embed
    # cycles running concurrently in the background, for `hours` hours ----
    log(f"Phase 1: scraping for {args.hours}h with keywords {args.keywords} "
        f"(embedding every {args.embed_interval_seconds}s alongside it)"
        if args.embed_interval_seconds > 0 else
        f"Phase 1: scraping for {args.hours}h with keywords {args.keywords} "
        f"(embedding disabled -- scrape only, no embed cycles at all)",
        log_file)
    scrape_proc = launch_scraper()
    restarts = 0
    embed_thread: threading.Thread | None = None
    last_embed_time = time.monotonic()

    end_time = time.monotonic() + args.hours * 3600
    while time.monotonic() < end_time:
        time.sleep(args.push_interval_seconds)
        if scrape_proc.poll() is not None:
            exit_code = scrape_proc.returncode
            remaining_seconds = end_time - time.monotonic()
            if remaining_seconds > args.min_restart_remaining_seconds and restarts < MAX_SCRAPE_RESTARTS:
                restarts += 1
                log(f"scraper exited (code {exit_code}) with {remaining_seconds/3600:.1f}h left -- "
                    f"restarting (attempt {restarts}/{MAX_SCRAPE_RESTARTS})", log_file)
                time.sleep(10)
                scrape_proc = launch_scraper()
            else:
                log(f"scraper exited (code {exit_code}); not restarting", log_file)
        git("add", "data/truckpaper_raw.jsonl")
        git_sync(f"auto: scrape progress from {args.name} ({host})", log_file)

        due = args.embed_interval_seconds > 0 and time.monotonic() - last_embed_time >= args.embed_interval_seconds
        idle = embed_thread is None or not embed_thread.is_alive()
        if due and idle:
            last_embed_time = time.monotonic()
            embed_thread = threading.Thread(target=embed_cycle, args=(False,), daemon=True)
            embed_thread.start()
        elif due and not idle:
            log("embed cycle due, but the previous one is still running -- skipping this cycle, will try next time", log_file)

    log("Phase 1 done: scrape window elapsed, stopping scraper", log_file)
    if scrape_proc.poll() is None:
        scrape_proc.terminate()
        try:
            scrape_proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            scrape_proc.kill()
    git("add", "data/truckpaper_raw.jsonl")
    git_sync(f"auto: final scrape progress from {args.name} ({host})", log_file, critical=True)

    if embed_thread is not None and embed_thread.is_alive():
        log("waiting for the in-progress periodic embed cycle to finish before the final one", log_file)
        embed_thread.join()

    if args.embed_interval_seconds <= 0:
        # --embed-interval-seconds 0 means "never embed on this machine at
        # all" (e.g. a CPU-only box you want doing pure scraping while GPU
        # machines handle embedding) -- skip the final cycle too, not just
        # periodic ones. A final, plain scrape-progress push still happens
        # above regardless, so nothing scraped here is left unshared.
        log("embedding disabled (--embed-interval-seconds 0) -- skipping the final embed cycle. "
            "This machine's scraped listings are already shared and will be embedded by "
            "whichever machine(s) run their own embed cycle against the combined dataset.", log_file)
        return 0

    # ---- Final embed cycle: clean + embed + push whatever's visible now
    # that scraping has fully stopped. Same steps as a periodic cycle, but
    # blocking and critical -- this one must land. ----
    rc = embed_cycle(final=True)
    if rc != 0:
        log("ABORTING: final embed cycle failed -- see the messages above", log_file)
        return 1

    log(f"Done. Tomorrow: run scripts/combine_independent_shards.py once, on any machine, "
        f"to build the final authoritative dataset + DINO index from every '*.npy'/'*.json' "
        f"pair under {INDEPENDENT_SHARD_DIR}.", log_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
