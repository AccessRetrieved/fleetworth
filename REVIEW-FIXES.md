# Current review resolution — 2026-09-13

Follow-up: live video had no bitrate, duration, or byte budget, so the 100 MiB
video ceiling could reject a normal six-photo walkaround. New recordings target
1.5 Mbps and finalize at five minutes/80 MiB of chunks. The backend accepts up
to 256 MiB video plus 70 MiB photos, including legacy recordings; final size
checks and file-specific errors cover browsers that exceed the bitrate hint.
See the [MediaStream Recording specification](https://www.w3.org/TR/mediastream-recording/)
for encoder-hint semantics. Photos and the complete session video are preserved.

Rechecked the Critical/Major findings in `code-review.md` and `CODE-REVIEW2.md`
against the current checkout before changing code. The original reports remain
historical snapshots; their line numbers and descriptions are not current contracts.

| Finding | Current resolution |
| --- | --- |
| API failures become non-truck observations / invented condition | Fixed. Explicit failure records cannot enter subject votes, coverage, fusion, annotation, or retrieval. Insufficient successful extractions return a retryable service error (HTTP 503). Genuine non-truck observations still refuse assessment. |
| Async server blocked by synchronous inference and disk work | Fixed. Synchronous route uses Starlette's thread pool; two prediction slots per worker bound expensive work. Health requests complete during deliberately blocked inference. |
| Unbounded uploads and indefinite video retention | Fixed. 3–7 photos, per-file/aggregate/body limits, chunked video writes, failure cleanup, and startup/hourly 24-hour retention cleanup. Videos are optional for photo-only submissions. |
| Torch/FAISS native abort on macOS | Fixed by separating native runtimes. FAISS index conversion happens in a fresh process; cached NumPy vectors provide exact cosine search on macOS. Existing split/single FAISS files remain compatible. Native FAISS stays in use on other platforms. No duplicate-runtime suppression or dependency downgrade. |
| Single-comp maximum confidence / visual dispersion ignored | Fixed. Lookup support and price spread lower confidence; adverse visual spread widens uncertainty even with matching median prices. Regression tests vary support and spread independently. These remain heuristic ranges, not empirically calibrated intervals. |
| FastAPI error detail hidden by frontend | Already fixed before this change. Verified and retained; validation errors now also identify their field. |
| Non-truck result renders as a crash | Already fixed before this change. Verified the dedicated “Unable to assess” view remains distinct from technical failures. |

## User-reported flow fixes

- Photo selections accumulate rather than replacing the existing selection.
  Review keeps **Add photos** available, with a three-photo minimum and seven-photo ceiling.
- **Start over** spans the review grid and is centered at desktop/mobile widths.
- Photo-only requests no longer fail with 422 for the missing `video` field.
- **Stop** opens review immediately, including paused or zero-photo sessions;
  submitting/resuming waits for video finalization. A late stop event cannot
  resurrect a discarded recording.
- Coverage recovery keeps prior photos and restarts frame analysis. At seven
  views, review asks the user to remove a less useful view before adding detail.
- A missed local vehicle detection permits explicitly labelled manual capture
  if sharpness/lighting checks pass. Automatic capture still requires vehicle
  detection. Runtime detector failures also fall back to manual capture.
  This does not bypass backend subject/authenticity/coverage checks.

Also addressed two nearby Minor issues: malformed identity fields are rejected
before fusion, and unexpected API exceptions are logged without returning raw
internal exception text. Low identity confidence is no longer itself a vote
that the subject is not a truck.

## Verification

- `backend/.venv/bin/python -m pytest -q backend/tests`: 89 passed after integrating the concurrent pricing changes, including six photos plus a 101 MiB recording.
- `node --test frontend/tests/capture.test.cjs`: 13 passed, including bitrate, automatic stop, preflight rejection, and preserving photos while replacing an oversized video.
- JavaScript syntax, Flask route, Python compilation, and `git diff --check` pass.
- Browser: one-photo then two-photo selection retains three views and enables
  submission; centered desktop review and 390px mobile layout inspected.
  Browser-injected screenshot thumbnails did not decode, so this check verifies
  the flow/layout, not image rendering. Recorder lifecycle is covered with
  delayed media-event test doubles; real-camera capture is not fully verified.
- No paid OpenAI calls, model downloads, scraper run, or data/index rebuild.

The stale backend on port 8000 was gracefully restarted with `--reload`.
Live verification confirms its schema requires only `photos`; three blank
test photos without a video return HTTP 200 / `needs_more_info` (blur gate),
with no paid extraction calls. Existing photo selections can be retried.

Other Minor findings and uploaded-video frame extraction are outside this fix.
The existing video-only upload control still needs a separate extraction path;
the `/predict` photo pipeline requires photos and never samples session video.
