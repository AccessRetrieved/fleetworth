"""Real ASGI/multipart requests with pipeline stubbed; no API key or network."""
import asyncio
import json
import os
import threading
import time
import uuid

import pytest

import main


async def request(body=b"", path="/predict", headers=None):
    messages = []
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        messages.append(message)

    await main.app({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET" if path == "/health" else "POST", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": headers or [(b"content-type", b"multipart/form-data; boundary=test")],
        "client": ("127.0.0.1", 1), "server": ("test", 80),
    }, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in messages)
    return status, json.loads(payload)


def multipart(count=3, photo=b"photo", video=None):
    fields = [("photos", "image/jpeg", photo)] * count
    if video is not None:
        fields.append(("video", "video/webm;codecs=vp9", video))
    return b"".join(
        f'--test\r\nContent-Disposition: form-data; name="{name}"; filename="test"\r\nContent-Type: {mime}\r\n\r\n'.encode()
        + data + b"\r\n" for name, mime, data in fields
    ) + b"--test--\r\n"


@pytest.fixture(autouse=True)
def isolated_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(main, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(main, "run_pipeline", lambda photos, **kwargs: {"status": "priced", "photo_count": len(photos)})


def test_three_photos_without_video_succeed():
    status, result = asyncio.run(request(multipart()))
    assert status == 200
    assert result["photo_count"] == 3
    assert not main.UPLOAD_DIR.exists()


def test_live_video_is_streamed_and_preserved():
    status, result = asyncio.run(request(multipart(video=b"recording")))
    assert status == 200
    assert (main.UPLOAD_DIR / result["submission_id"] / "video.webm").read_bytes() == b"recording"


@pytest.mark.parametrize("count", [2, 8])
def test_photo_count_rejected_before_pipeline(monkeypatch, count):
    monkeypatch.setattr(main, "run_pipeline", lambda *a, **k: pytest.fail("Unexpected extraction"))
    assert asyncio.run(request(multipart(count=count)))[0] == 400


@pytest.mark.parametrize("kind", ["photo", "video", "total", "body", "declared_body"])
def test_limits_reject_without_extraction_or_retained_files(monkeypatch, kind):
    monkeypatch.setattr(main, "run_pipeline", lambda *a, **k: pytest.fail("Unexpected extraction"))
    key = {"photo": "MAX_PHOTO_BYTES", "video": "MAX_VIDEO_BYTES", "total": "MAX_SUBMISSION_BYTES", "body": "MAX_BODY_BYTES", "declared_body": "MAX_BODY_BYTES"}[kind]
    monkeypatch.setattr(main, key, 2)
    headers = [(b"content-length", b"999")] if kind == "declared_body" else None
    assert asyncio.run(request(multipart(video=b"video"), headers=headers))[0] == 413
    assert not main.UPLOAD_DIR.exists() or not list(main.UPLOAD_DIR.iterdir())


def test_exact_size_boundaries_are_accepted(monkeypatch):
    monkeypatch.setattr(main, "MAX_PHOTO_BYTES", 5)
    monkeypatch.setattr(main, "MAX_VIDEO_BYTES", 5)
    monkeypatch.setattr(main, "MAX_SUBMISSION_BYTES", 20)
    assert asyncio.run(request(multipart(video=b"video")))[0] == 200


@pytest.mark.parametrize("status", ["needs_more_info", "not_a_truck", "service_error"])
def test_failed_assessment_leaves_no_evidence(monkeypatch, status):
    monkeypatch.setattr(main, "run_pipeline", lambda *a, **k: {"status": status, "message": "Retry later"})
    code, _ = asyncio.run(request(multipart(video=b"video")))
    assert code == (503 if status == "service_error" else 200)
    assert not list(main.UPLOAD_DIR.iterdir())


def test_unexpected_error_is_generic_and_cleans_up(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("private upstream details")
    monkeypatch.setattr(main, "run_pipeline", fail)
    status, result = asyncio.run(request(multipart(video=b"video")))
    assert status == 502
    assert "private" not in result["detail"]
    assert not list(main.UPLOAD_DIR.iterdir())


def test_health_responds_while_pipeline_is_blocked(monkeypatch):
    started, release = threading.Event(), threading.Event()
    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return {"status": "priced"}
    monkeypatch.setattr(main, "run_pipeline", blocked)
    async def scenario():
        prediction = asyncio.create_task(request(multipart()))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            assert (await asyncio.wait_for(request(path="/health"), 1))[0] == 200
        finally:
            release.set()
            await prediction
    asyncio.run(scenario())


def test_busy_service_rejects_excess_work():
    main.PREDICTION_SLOTS.acquire()
    main.PREDICTION_SLOTS.acquire()
    try:
        assert asyncio.run(request(multipart()))[0] == 503
    finally:
        main.PREDICTION_SLOTS.release()
        main.PREDICTION_SLOTS.release()


def test_retention_only_removes_expired_submission_directories():
    old = main.UPLOAD_DIR / str(uuid.uuid4())
    recent = main.UPLOAD_DIR / str(uuid.uuid4())
    unrelated = main.UPLOAD_DIR / "keep"
    for path in (old, recent, unrelated):
        path.mkdir(parents=True)
    expired = time.time() - main.RETENTION_SECONDS - 1
    os.utime(old, (expired, expired))
    os.utime(unrelated, (expired, expired))
    main.cleanup_expired_evidence()
    assert not old.exists()
    assert recent.exists() and unrelated.exists()
