"""
llm_client.py — thin client for Google AI Studio's Gemini API, via its
OpenAI-compatible endpoint (so the request/response shape matches what
you'd expect from any OpenAI-style chat completions API).

Free tier, no credit card required — but NOT zero-signup like GitHub
Models was: you need a free Gemini API key from
https://aistudio.google.com/apikey, stored as a GEMINI_API_KEY secret.
See docs_GEMINI_SETUP.md.

This replaces an earlier GitHub Models-based version of this file —
GitHub Models was fully retired on July 30, 2026. Kept as its own
module (rather than folded into llm_pipeline.py) so a future provider
swap only touches this one file again.
"""
import json
import os
import re
import time

import requests

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
# Free-tier-eligible as of this writing — Pro-series models moved to
# paid-only in April 2026. Override via GEMINI_MODEL env var if this
# drifts; check https://ai.google.dev/gemini-api/docs/pricing for what's
# currently free before assuming this still is.
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
REQUEST_TIMEOUT = 60
MAX_RETRIES = 3


class LLMCallError(Exception):
    """Raised for any failure calling the Gemini API — missing key, rate
    limit exhausted after retries, malformed response, invalid JSON.
    Callers should catch this specifically and fall back to rule-based
    generation rather than let one bad call break the whole edition.
    """


def _get_token() -> str:
    """Named _get_token for continuity with the rest of the pipeline
    (build_edition.py checks for this to decide whether to attempt LLM
    generation at all) — despite the name, this returns a Gemini API key,
    not a GitHub token.
    """
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise LLMCallError(
            "No GEMINI_API_KEY in environment — get a free key at "
            "https://aistudio.google.com/apikey and set it as a repo "
            "secret (see docs_GEMINI_SETUP.md)."
        )
    return key


def _extract_json(text: str) -> dict:
    """Models sometimes wrap JSON in code fences despite being told not
    to — strip those before parsing."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    return json.loads(text)


def call_json(system_prompt: str, user_prompt: str, model: str = None, temperature: float = 0.3) -> dict:
    """Calls the Gemini API with a system+user prompt pair, expecting a
    single JSON object back as the entire response content. Retries on
    rate limiting (HTTP 429) with backoff. Raises LLMCallError on any
    failure after retries are exhausted — never returns a partial or
    guessed result.
    """
    key = _get_token()
    model = model or DEFAULT_MODEL
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
    }

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(GEMINI_ENDPOINT, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429:
                wait = min(30, 2 ** attempt)
                print(f"::warning::Gemini API rate-limited (attempt {attempt}/{MAX_RETRIES}), waiting {wait}s")
                last_error = LLMCallError(f"Rate limited: {resp.text[:300]}")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return _extract_json(content)
        except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError) as e:
            last_error = LLMCallError(f"Gemini API call failed (attempt {attempt}/{MAX_RETRIES}): {e}")
            print(f"::warning::{last_error}")
            time.sleep(min(10, 2 * attempt))

    raise last_error or LLMCallError("Unknown failure calling the Gemini API")
