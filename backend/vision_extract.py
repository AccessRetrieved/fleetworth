"""
Phase 2b — Vision extraction module.

Given a truck image (URL or local path), calls OpenAI's vision API and
returns the structured-JSON schema from PLAN.md Phase 2:
  make, model, year_estimate, trim, condition, visible_damage,
  tire_condition, confidence

The vision API only extracts *what it sees* — never a price. Pricing is
computed separately (Phase 4) from the scraped comps data.
"""
import base64
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MODEL = "gpt-4o-mini"

EXTRACTION_PROMPT = """Given this image, return ONLY valid JSON (no markdown, no commentary) matching exactly this schema:
{
  "is_truck": boolean,
  "is_real_photo": boolean,
  "make": string,
  "model": string,
  "year_estimate": string,
  "trim": string,
  "condition": "excellent" | "good" | "fair" | "poor",
  "visible_damage": [string],
  "tires_visible": boolean,
  "tire_condition": "new" | "worn" | "bald",
  "confidence": float between 0 and 1
}

Rules:
- is_truck: true only if the primary subject is an actual truck (pickup, semi tractor, box truck, dump truck, service truck, etc). False for any other subject (car, person, building, animal, random object, blank/black image, etc).
- is_real_photo: true only if this is a real-world photograph of a physical truck. False if the image is a video game screenshot, CGI render, 3D model, illustration/drawing, toy/model truck, or a photo of a screen/poster/advertisement showing a truck. Be strict here — a photorealistic render or game screenshot still counts as false.
- If is_truck or is_real_photo is false, still fill in the other fields with your best guess or "unknown", but set confidence low (below 0.2).
- year_estimate may be a single year or a range (e.g. "2018-2020") if you're not certain.
- visible_damage should be an empty array if no damage is visible, otherwise short descriptive strings (e.g. "rust on rear fender", "cracked side mirror", "dent on tailgate").
- tires_visible: true only if at least one tire/wheel is clearly visible and you can actually judge its condition from this photo. If tires aren't visible or are too small/obscured to assess, set this false — in that case tire_condition should still be your best guess, but it will be ignored by the pipeline.
- confidence reflects how confident you are in make/model/year identification, not condition.
- If you cannot identify the make/model at all, use "unknown" for those fields and lower confidence accordingly.
- Return ONLY the JSON object, nothing else."""

REQUIRED_KEYS = {
    "is_truck", "is_real_photo", "make", "model", "year_estimate", "trim",
    "condition", "visible_damage", "tires_visible", "tire_condition", "confidence",
}
VALID_CONDITION = {"excellent", "good", "fair", "poor"}
VALID_TIRE = {"new", "worn", "bald"}


class ExtractionError(Exception):
    pass


def _client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ExtractionError("OPENAI_API_KEY not set (check .env)")
    return OpenAI(api_key=api_key)


def _image_content(image: str | bytes) -> dict:
    """Build the image content block. Accepts a URL, a local file path, or
    raw image bytes (e.g. from an uploaded file)."""
    if isinstance(image, bytes):
        b64 = base64.b64encode(image).decode()
        return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
    if image.startswith("http://") or image.startswith("https://"):
        return {"type": "image_url", "image_url": {"url": image}}
    data = Path(image).read_bytes()
    b64 = base64.b64encode(data).decode()
    ext = Path(image).suffix.lstrip(".") or "jpeg"
    return {"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{b64}"}}


def _parse_and_validate(raw_text: str) -> dict:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    data = json.loads(text)  # raises json.JSONDecodeError on malformed output

    missing = REQUIRED_KEYS - data.keys()
    if missing:
        raise ExtractionError(f"missing keys: {missing}")
    if data["condition"] not in VALID_CONDITION:
        raise ExtractionError(f"invalid condition: {data['condition']!r}")
    if data["tire_condition"] not in VALID_TIRE:
        raise ExtractionError(f"invalid tire_condition: {data['tire_condition']!r}")
    if not isinstance(data["visible_damage"], list):
        raise ExtractionError("visible_damage must be a list")
    if not isinstance(data["is_truck"], bool):
        raise ExtractionError("is_truck must be a boolean")
    if not isinstance(data["is_real_photo"], bool):
        raise ExtractionError("is_real_photo must be a boolean")
    if not isinstance(data["tires_visible"], bool):
        raise ExtractionError("tires_visible must be a boolean")
    conf = float(data["confidence"])
    if not (0.0 <= conf <= 1.0):
        raise ExtractionError(f"confidence out of range: {conf}")
    data["confidence"] = conf

    return data


def _fallback_unknown(reason: str) -> dict:
    return {
        "is_truck": False,
        "is_real_photo": False,
        "make": "unknown",
        "model": "unknown",
        "year_estimate": "unknown",
        "trim": "unknown",
        "condition": "fair",
        "visible_damage": [],
        "tires_visible": False,
        "tire_condition": "worn",
        "confidence": 0.0,
        "_error": reason,
    }


def extract_from_image(image: str | bytes, client: OpenAI | None = None) -> dict:
    """Run structured extraction on a single image. Retries once on malformed
    JSON, then falls back to an 'unknown' record so the pipeline never crashes
    on a bad frame (Phase 2 requirement)."""
    client = client or _client()

    last_error = None
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": EXTRACTION_PROMPT},
                            _image_content(image),
                        ],
                    }
                ],
                max_tokens=500,
                temperature=0,
            )
            raw_text = response.choices[0].message.content
            return _parse_and_validate(raw_text)
        except (json.JSONDecodeError, ExtractionError, KeyError, ValueError) as e:
            last_error = e
            continue
        except Exception as e:  # API errors: timeout, rate limit, etc.
            last_error = e
            break

    return _fallback_unknown(f"{type(last_error).__name__}: {last_error}")
