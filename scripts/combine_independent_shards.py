r"""
Morning-after combine step for independent_pipeline.py's per-machine runs.

Run this ONCE, by a human, on any one machine, after every machine that ran
independent_pipeline.py tonight has finished and pushed its
data/independent_shards/<name>.npy + <name>.json. It does the one thing
that genuinely needs a single global view across everyone's data (which
independent_pipeline.py deliberately skipped overnight to avoid the
coordinator-wait idle time):

  1. Pulls the latest combined data/truckpaper_raw.jsonl (already merged
     from everyone's pushes all night, Jonathan's included, via the
     .gitattributes union-merge driver -- nothing needs to change there).
  2. Runs ONE authoritative scraper/clean_data.py pass over ALL of it --
     this is the first point any relist/duplicate scraped by two different
     machines' overlapping keyword searches gets caught, and the first
     point base_prices.json reflects everyone's data instead of one
     machine's local, partial view.
  3. Combines every independent shard's embeddings+metadata, dropping:
       - any listing_id embedded by more than one machine (keeps one copy)
       - any listing_id the fresh clean pass dropped as a duplicate/relist/
         bad row -- its embedding would otherwise reference a listing that
         no longer exists in the authoritative dataset
     Listings that exist in the fresh clean data but were never embedded by
     any machine (e.g. everything Jonathan's still-unattended machine
     scraped, since it wasn't part of tonight's independent-pipeline group)
     simply have no visual embedding in the final index -- their price
     still counts toward base_prices.json, they just won't surface as a
     DINO-retrieved visual comp. Re-run independent_pipeline.py-style
     embedding against them later if you want that closed.
  4. Builds and saves the final data/dino_index/ from the combined,
     deduped embeddings, and commits+pushes everything (truckpaper_clean.jsonl,
     base_prices.json, data/dino_index/) as the one shared source of truth.

Variable N, by design: shard discovery is a plain glob over
data/independent_shards/*.json -- there is no hardcoded or configured
expectation of how many machines ran tonight. Whether 2 machines ran or 6,
this script combines whatever shard pairs it actually finds. A fleet of
e.g. 2 RunPod GPU pods + 1 MacBook + 1 Windows 11 PC (4 machines, 4 --name
values) works exactly the same way as 3 or 6 machines -- nothing here
changes with fleet size.

Resilient to partial failure, by design: any machine that never started,
crashed mid-scrape, or crashed mid-embed simply never produces its
<name>.npy/.json pair (or produces a partial/corrupt one). combine_shards()
below handles every such case without crashing or needing manual cleanup --
a shard whose .json exists but .npy doesn't (or vice versa, since discovery
is json-driven), a .json that isn't valid JSON or is missing the "items"
key, a .npy that numpy can't load, or an items/embeddings length mismatch
are all skipped with a printed reason, and the run proceeds using whatever
DID survive. Only a *complete* wipeout (zero usable shards) aborts.

Usage (run once, by a human, on any one surviving machine, regardless of
how many machines ran independent_pipeline.py overnight or how many of
them dropped out):
    python3 scripts/combine_independent_shards.py
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRAPER_DIR = REPO_ROOT / "scraper"
BACKEND_DIR = REPO_ROOT / "backend"
CLEAN_PATH = REPO_ROOT / "data" / "truckpaper_clean.jsonl"
INDEPENDENT_SHARD_DIR = REPO_ROOT / "data" / "independent_shards"

sys.path.insert(0, str(BACKEND_DIR))


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True)


def run_uv(args: list[str], cwd: Path) -> int:
    proc = subprocess.run(["uv", "run", "python", *args], cwd=cwd)
    return proc.returncode


def combine_shards(
    shard_dir: Path, authoritative_ids: set[str]
) -> tuple[list[dict], list["np.ndarray"], list[Path]]:
    """Combine every independent shard's items+embeddings found under
    shard_dir into one deduped set, dropping anything the authoritative
    clean pass rejected and any listing_id embedded by more than one
    machine. Any number of shard pairs (2, 3, 4, 6, ...) is supported --
    shard_dir is discovered by a plain glob, with no count assumed.

    Robust to partial failure: a machine that never started, crashed
    mid-run, or produced a broken pair simply leaves a gap here rather than
    crashing the whole combine. Every one of these is caught and skipped
    with a printed reason so the survivors still combine cleanly:
      - shard missing entirely (no matching files at all -- never appears
        in the glob, so there is nothing to special-case)
      - .json present but its matching .npy is missing (or vice versa,
        which manifests as no matching .json to glob on in the first place)
      - .json is not valid JSON, or is valid JSON missing the "items" key
      - .npy is not a valid/loadable numpy file
      - items/embeddings length mismatch between the two files

    Returns (items, embeddings, shard_files) -- shard_files is every
    discovered shard's .json path (used by the caller for the commit
    message / logging), independent of how many were actually usable.
    """
    shard_files = sorted(shard_dir.glob("*.json"))
    seen_listing_ids: set[str] = set()
    all_embeddings: list["np.ndarray"] = []
    all_items: list[dict] = []

    for json_path in shard_files:
        npy_path = json_path.with_suffix(".npy")
        if not npy_path.exists():
            print(f"  skipping {json_path.stem}: missing matching .npy file")
            continue
        try:
            with json_path.open(encoding="utf-8") as f:
                payload = json.load(f)
            items = payload["items"]
            embeddings = np.load(npy_path)
        except (json.JSONDecodeError, KeyError, ValueError, OSError, EOFError) as exc:
            print(f"  skipping {json_path.stem}: corrupt/unreadable shard files ({exc!r})")
            continue
        if len(items) != len(embeddings):
            print(f"  skipping {json_path.stem}: items/embeddings length mismatch ({len(items)} vs {len(embeddings)})")
            continue

        kept = 0
        for item, vec in zip(items, embeddings):
            lid = item.get("listing_id") if isinstance(item, dict) else None
            if lid is None:
                continue  # malformed item entry -- skip rather than crash
            if lid not in authoritative_ids:
                continue  # dropped by the fresh clean pass (duplicate/relist/bad row)
            if lid in seen_listing_ids:
                continue  # already embedded by an earlier shard in this loop
            seen_listing_ids.add(lid)
            all_items.append(item)
            all_embeddings.append(vec)
            kept += 1
        print(f"  {json_path.stem}: {kept}/{len(items)} items kept "
              f"(dropped: not-in-authoritative-set or cross-machine duplicate)")

    return all_items, all_embeddings, shard_files


def main() -> int:
    print("Pulling latest combined raw data...")
    pull = git("pull", "--no-edit", "-q")
    if pull.returncode != 0:
        print(f"git pull failed:\n{pull.stdout}\n{pull.stderr}")
        return 1

    print("\nRunning the one authoritative clean_data.py pass over everyone's combined raw data...")
    subprocess.run(["uv", "sync", "-q"], cwd=SCRAPER_DIR)
    rc = run_uv(["clean_data.py"], cwd=SCRAPER_DIR)
    if rc != 0:
        print(f"clean_data.py failed (exit {rc}) -- aborting, not touching independent shards")
        return 1

    with CLEAN_PATH.open(encoding="utf-8") as f:
        authoritative_ids = {json.loads(line)["listing_id"] for line in f if line.strip()}
    print(f"authoritative clean dataset: {len(authoritative_ids)} listings")

    discovered = sorted(INDEPENDENT_SHARD_DIR.glob("*.json"))
    if not discovered:
        print(f"No independent shards found under {INDEPENDENT_SHARD_DIR} -- nothing to combine")
        return 1
    print(f"\nFound {len(discovered)} independent shard(s): {[p.stem for p in discovered]}")

    all_items, all_embeddings, shard_files = combine_shards(INDEPENDENT_SHARD_DIR, authoritative_ids)

    if not all_items:
        print("\nNo items survived combination -- aborting without touching data/dino_index/")
        return 1

    combined_embeddings = np.stack(all_embeddings).astype(np.float32)
    print(f"\nCombined index will have {len(all_items)} vectors "
          f"(covers {len(all_items)}/{len(authoritative_ids)} of the authoritative dataset -- "
          f"the rest were scraped by a machine that never ran the independent embedding step).")

    from dino_retrieval import build_index, save_index  # backend/ is on sys.path above

    index = build_index(combined_embeddings)
    save_index(index, all_items)
    print(f"wrote {index.ntotal} vectors (dim {index.d}) to data/dino_index/")

    print("\nCommitting the final combined dataset + index...")
    git("add", "data/truckpaper_clean.jsonl", "data/base_prices.json")
    # comps.faiss may now be split into comps.part01.faiss etc (see
    # dino_retrieval.save_index) to stay under GitHub's 100MB push limit --
    # glob for whatever parts actually got written rather than assuming one.
    index_files = sorted((REPO_ROOT / "data" / "dino_index").glob("comps*.faiss"))
    git("add", "-f", *[str(p.relative_to(REPO_ROOT)) for p in index_files], "data/dino_index/comps_meta.json")
    commit = git("commit", "-q", "-m",
                 f"Combine {len(shard_files)} independent shards into final dataset + DINO index "
                 f"({len(all_items)} embedded / {len(authoritative_ids)} total listings)")
    if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr):
        print(f"commit failed:\n{commit.stdout}\n{commit.stderr}")
        return 1
    push = git("push", "-q")
    if push.returncode != 0:
        print(f"push failed -- run `git push` by hand:\n{push.stdout}\n{push.stderr}")
        return 1

    print("\nDone. data/truckpaper_clean.jsonl, data/base_prices.json, and data/dino_index/ "
          "are now the shared, authoritative combined result -- everyone should `git pull`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
