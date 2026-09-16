# FleetWorth

Point your camera at a used truck, walk around it for a minute, and get back a real, grounded price estimate based on the market — with a confidence score and an explainable breakdown.

Built for Kamion in the 54 Hackathon FA26.

## Inspiration 🧠

- Used truck pricing today is hard due to evidence forging
- Truck listing sites already have thousands of real, priced, photographed comps — nobody uses them programmatically
- Goal: a trustworthy price from photos/video alone, grounded in real sale data — not an LLM hallucinating a number, no more forging mileage/VINs.

## What it does ⚙️

- Live guided camera session — no upload picker, no mode toggle, just walk around the truck
- GPT-4o-mini VLM extracts make, model, year, condition, tire condition, and damage from each photo, fused across views
- DINOv2 embeddings + FAISS similarity search find the most visually similar real trucks that actually sold
- Cross-checks the two signals (VLM lookup vs. visual comps) — agreement tightens confidence, disagreement widens the range
- Damage gets flagged with bounding boxes for the breakdown
- Missing/blurry/non-truck input → app says "needs more info" instead of forcing a number
- Output is always a price range + confidence, never a fake-precise single number

## How we built it 👷🔧

- **Data:** Playwright scraper pulls real listings from TruckPaper.com (respecting robots.txt, excluding auctions); cleaned, deduped, and relist-matched; grew from 375 → ~67,563 listings
- **Backend:** FastAPI `/predict` endpoint — VLM extraction pipeline runs in parallel with DINOv2 embedding + FAISS retrieval against scraped comps; multi-view results fused with a recurrence bonus, priced via interpolated weighted median
- **Frontend:** Flask/vanilla JS, mobile-first live camera capture
- **Scaling:** custom Python orchestration script (cross-platform) let 3 machines on 3 networks scrape in parallel, coordinating entirely through git (union-merge for concurrent writes, commit messages as phase signals, sha256-verified index shard merges), with auto-restart on crash

## Challenges we ran into 💀

- Time restrictions of the hackathon; not enough time to scrape & make embedding & rebuild dino index
  - Solved by using database sharding, split training/embedding into concurrent tasks on 5 different machines.
- DINO was year-blind — good at identifying the truck, bad at telling how old it was
- More data didn't help: 375 → 1,631 listings barely moved error (30.0% → 31.4%) — redirected effort to algorithmic fixes instead
  - turns out it was because we didn't remove SUVs/Pickup trucks from the training dataset. Removing those helped.
- Two scrape sites were hard no's (CAPTCHA wall, robots.txt disallow)
- No shared LAN/VPN/rsync across 3 machines — had to build conflict-free coordination over git alone
- Two DINO branches diverged with no common ancestor commit — required hand-porting changes
- Real-device bugs only showed up on real devices: Chrome's actual MediaRecorder codec string, perceptual-duplicate-photo thresholds

## Accomplishments 🎉

- Pricing is actually grounded in real comps, not a confident LLM guess
0 Cut visual-retrieval pricing error from ~51% to ~16% (image alone), ~13.6% combined with noisy year estimate
- Unattended overnight auto-scraping script across 3 machines, synced with git commit messages
  - due to different LAN constraints, machines communicated with each other using git commit messages🤡
- Model knows what it doesn't know — refuses to price non-trucks, flags missing views, widens confidence instead of faking precision
- Demo tells a "why this price" story: real comps, damage callouts, explainable breakdown

## What we learned 📝

- Solutions for coordination constraints (separate networks no LAN) forced us to use git commit message to sync scraping + embedding status between machines

## What's next for FleetWorth

- Try a learned regression model (XGBoost/LightGBM)
- Increase dataset towards 15k - 100k listings

---

## Tech Stack

`Python` `FastAPI` `Flask` `PyTorch` `DINOv2` `FAISS` `OpenCV` `Playwright` `GPT-4o-mini` `JavaScript` `HTML/CSS` `uv` `pytest` `Git` `GitHub`
