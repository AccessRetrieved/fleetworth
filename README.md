# FleetWorth

Point your camera at a used truck, walk around it for a minute, and get back a real, grounded price estimate — with a confidence score, an explainable breakdown, and the honesty to say "I need a clearer shot of the tires" instead of guessing.

Built for Kamion in the 54 Hackathon FA26.

## 1. Inspiration 🧠

Used commercial trucks are a massive, opaque market — fleet managers, owner-operators, and dealers routinely price trucks off gut instinct, a quick call to a broker, or a stale spreadsheet. Meanwhile, every truck listing site is sitting on thousands of real, priced, photographed comparable vehicles that nobody is using programmatically. We wanted to answer a simple question the brief posed directly: can you get a trustworthy price out of nothing but photos and a video, without just asking an LLM to hallucinate a number? The answer had to be grounded in real sale data, not vibes — and it had to know when it didn't know.

## 2. What it does ⚙️

- Open the app on your phone or laptop and it drops you straight into a **live guided camera session** — no upload picker, no mode toggle. Walk around the truck while it prompts you for angles and auto-snaps photos, then hit stop and submit.
- Every photo runs through a **vision-language model (GPT-4o-mini)** that extracts make, model, year estimate, condition, tire condition, and localized damage — each fused across however many views you captured (majority vote on identity, worst-case on condition, union on damage).
- In parallel, every photo is embedded with a **pretrained DINOv2 vision transformer** and matched via **FAISS cosine similarity** against a database of real scraped truck listings. Instead of an LLM guessing a price, we find the actual most visually similar real trucks that sold, and price off of them.
- The two signals — VLM-extracted make/model/year lookup and DINO visual-neighborhood pricing — are cross-checked against each other. When they agree, confidence goes up. When they disagree, the range widens and the disagreement is surfaced in the breakdown.
- Any photo with visible damage gets a **red bounding box drawn around it** for the demo/review record — a visual "here's why the price moved."
- If something's missing — no usable shot of the tires, a blurry photo, a subject that isn't even a truck — the app doesn't force a number. It returns a **"needs more info"** response naming exactly what's missing, matching the challenge brief's own bar for "knowing its limits."
- Headline output is always a **price range + confidence score**, never a single fake-precise number.

## 3. How we built it 👷🔧

**Data pipeline** — a Playwright-driven scraper pulls real listings (make, model, year, price, mileage, images) from TruckPaper.com, respecting rate limits and `robots.txt`, and explicitly excluding auction listings since opening bids aren't market prices. A cleaning stage normalizes make/model naming, collapses trim variants into model families, dedupes exact repeats, and fuzzy-matches relists (same title/price/mileage/location under a new listing ID) so one truck doesn't get counted as five comps. What started at 375 listings grew to nearly 2,000 through the week, and is growing further tonight via an unattended overnight scrape.

**Backend** — FastAPI serves a single `/predict` endpoint. Photos go through the VLM extraction + fusion pipeline; in parallel, the same photos are embedded with DINOv2 and queried against a FAISS `IndexFlatIP` index built from every scraped comp image. Retrieval results from multiple views are fused with a recurrence bonus (a comp that shows up near the top for more than one angle is trusted more), then converted into a similarity-weighted price via an interpolated weighted median, cross-checked against the traditional make/model/year lookup table.

**Frontend** — a mobile-first, live-camera guided capture flow (Flask/vanilla JS) that never asks the user to pick a mode or upload a file; it just opens the camera and walks them through it.

**Multi-machine overnight scaling** — since scrape speed wasn't actually the bottleneck, breadth was: we built a cross-platform (Mac / Ubuntu / Windows) orchestration script in plain Python (not bash, for Windows compatibility) that lets multiple machines — on completely different networks — scrape their own assigned keyword sets in parallel, then coordinate purely through git: a union-merge driver lets concurrent appends to the raw dataset merge without conflicts, commit messages act as phase-completion signals, and DINO index shards are built independently per machine, sha256-verified, and merged by a coordinator — all through nothing but `git push`/`git pull` against a shared GitHub remote, with automatic restart-on-crash so an unattended 8-hour run survives a scraper hiccup or a dropped machine.

## 4. Challenges we ran into 💀

- **DINO was surprisingly year-blind out of the box.** Leave-one-out evaluation showed ~30% pricing error, and root-causing it took real diagnostic work — it turned out the visual embeddings were much better at recognizing *what* truck it was than *how old* it was, since two trucks a decade apart can look nearly identical. Model-family mattered less than expected; year mattered far more.
- **More data alone didn't fix it.** We proved this empirically — scaling the dataset from 375 to 1,631 listings barely moved the error needle (30.0% → 31.4%, flat), which redirected the whole team's effort from "just scrape more" toward algorithmic fixes: year-aware re-ranking, a model-family recurrence bonus, and switching from a naive weighted mean to an interpolated weighted median.
- **Ethics-driven scraping constraints.** Two additional listing sites we scoped out turned out to be dead ends we chose not to route around: one had an active CAPTCHA/human-verification wall, and another's `robots.txt` explicitly disallowed the exact search pages we needed. We treated both as hard no's rather than problems to engineer past.
- **Coordinating three machines on three different networks with no shared LAN, no VPN, and no rsync.** Git itself became the only viable relay — which meant designing conflict-free concurrent writes (a union-merge git attribute), phase signaling via commit messages, and checksum verification for a merge step that had no human awake to supervise it.
- **Two branches of DINO work quietly grew unrelated git histories.** A multi-machine sharding branch and a pricing-formula-improvement branch diverged so far they no longer even shared a common ancestor commit — discovered only when a routine merge attempt returned nothing from `git merge-base`. Reconciling that required hand-porting changes file by file rather than trusting an automatic merge.
- **Small but real production bugs surfaced only by testing on real devices** — Chrome's actual `MediaRecorder` output (`video/webm;codecs=vp9,opus`) failed our exact-match content-type check; a naive perceptual-duplicate-photo detector needed real-world threshold calibration after both false positives and false negatives showed up on live capture sessions.

## 5. Accomplishments that we're proud of 🎉

- A pricing pipeline that's **actually grounded in real sale data**, not an LLM's confident guess — every price traces back to real comparable listings you can point to.
- Took DINO's visual-retrieval pricing error from ~28% down to ~27.3% on image alone, and to **~21.6%** once combined with a realistic year estimate, all verified through rigorous leave-one-out evaluation rather than vibes.
- A genuinely resilient, unattended overnight pipeline — tested against crash/restart, conflicting concurrent writes, and dropped machines — running live across three machines on three separate networks tonight.
- A pricing model that **knows what it doesn't know**: refusing to price a non-truck, flagging missing critical views by content (not by a rigid photo checklist), and widening its confidence range instead of faking precision.
- A demo-ready "why this price" story: not just a number, but real comparable trucks, a visual damage callout, and an explainable breakdown a judge can poke holes in.

## 6. What we learned 📝

- **A bigger dataset is not automatically a better dataset.** Diagnosing *why* an error rate is high beats blindly scaling data — the fix here was algorithmic (year-awareness), not volumetric.
- **Grounded retrieval beats generative guessing** for anything with a real market — DINO + FAISS against real comps gave us something an LLM's raw price guess never could: an actual, inspectable, real-world comparable to point to.
- **"Knowing its limits" is a feature, not an edge case** — designing the "needs more info" path as a first-class, intentional response (not an error state) changed how we thought about the whole UX.
- **Coordination constraints shape architecture.** Once real machines on real separate networks entered the picture, the elegant plan (rsync, shared LAN) had to be thrown out in favor of the boring, robust one (git as a message bus) — and the boring one turned out to be the right call.
- **Test on real hardware, early.** Several of our sharpest bugs (video codec strings, perceptual-hash thresholds) only existed in the gap between "works in theory" and "works on an actual phone camera in an actual browser."

## 7. What's next for FleetWorth

- Merge the DINO visual-retrieval pricing pipeline into `main` (currently on parallel branches) alongside tonight's freshly expanded, multi-machine-scraped dataset.
- Mileage-aware bucketing — right now, the base-price lookup blends low- and high-mileage trucks of the same year into one bucket; splitting on mileage band is a clear, flagged accuracy win.
- The stretch learned-regression model (XGBoost/LightGBM over VLM + DINO features) to see if it can beat the current interpretable formula — and only ship it if it actually does.
- Broader make/model coverage as the scraped dataset keeps growing past this weekend's ~15k-listing target.
- A sharper "VLM vs. DINO disagreement" UX — right now disagreement widens the range; showing *both* signals side-by-side would make the explainability story even stronger for a judge poking at edge cases.

---

## Tech Stack

`Python` `FastAPI` `Flask` `PyTorch` `DINOv2` `FAISS` `OpenCV` `Playwright` `GPT-4o-mini` `JavaScript` `HTML/CSS` `uv` `pytest` `Git` `GitHub`
