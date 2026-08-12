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
# Free-tier-eligible as of this writing, per Google's own OpenAI-
# compatibility docs (ai.google.dev/gemini-api/docs/openai) — an earlier
# version of this file used "gemini-2.5-flash" based on a third-party
# source and got 404s from the real endpoint; this repo's own history is
# proof that model IDs here are not stable. Override via GEMINI_MODEL env
# var, and if you get a 404 (not 401/403/429) on this endpoint, that's
# almost always a stale/wrong model ID, not an auth or quota problem —
# check https://ai.google.dev/gemini-api/docs/openai and/or the model
# list in your AI Studio project before assuming anything else is wrong.
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
REQUEST_TIMEOUT = 60
MAX_RETRIES = 3

# Once a call exhausts all its retries on 429s, quota is almost certainly
# gone for the rest of this run (a daily/quota-window limit doesn't clear
# in the few seconds retry backoff covers) — retrying every subsequent
# region call the same way just burns ~14s of wasted backoff per region
# proving the same thing again. This flag is set the first time that
# happens, and checked before any later call even attempts a request.
_quota_exhausted = False


class LLMCallError(Exception):
    """Raised for any failure calling the Gemini API — missing key, rate
    limit exhausted after retries, malformed response, invalid JSON.
    Callers should catch this specifically and fall back to rule-based
    generation rather than let one bad call break the whole edition.
    """


def reset_quota_state():
    """Resets the _quota_exhausted latch. Not needed for normal daily
    runs (each is a fresh process, so the flag starts False naturally) —
    provided for tests or any other case that calls call_json() more
    than once within the same Python process.
    """
    global _quota_exhausted
    _quota_exhausted = False


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

    If a previous call this run already exhausted its retries on 429s
    (see _quota_exhausted above), this raises immediately without
    attempting a request at all — the quota isn't coming back in the
    next few seconds, so there's nothing to gain from trying again, only
    ~14s of wasted retry backoff per remaining call.
    """
    global _quota_exhausted
    if _quota_exhausted:
        raise LLMCallError(
            "Skipped — a previous call this run exhausted its retries on HTTP 429 (quota exceeded), "
            "so further Gemini calls this run are being skipped rather than each wasting a full retry cycle."
        )

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
            if resp.status_code == 404:
                # Almost always a stale/wrong model ID, not a real "not
                # found" in the usual sense — surface that directly rather
                # than making someone rediscover it via trial and error.
                last_error = LLMCallError(
                    f"404 from Gemini API with model='{model}' — this almost always means the model ID "
                    "is wrong or no longer exists, not an auth/quota problem. Check "
                    "https://ai.google.dev/gemini-api/docs/openai for a current example model ID, or set "
                    "GEMINI_MODEL to override. Response: " + resp.text[:200]
                )
                print(f"::warning::{last_error}")
                break  # retrying with the same bad model ID won't help
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return _extract_json(content)
        except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError) as e:
            last_error = LLMCallError(f"Gemini API call failed (attempt {attempt}/{MAX_RETRIES}): {e}")
            print(f"::warning::{last_error}")
            time.sleep(min(10, 2 * attempt))

    if isinstance(last_error, LLMCallError) and "Rate limited" in str(last_error):
        _quota_exhausted = True
        print("::warning::Gemini quota appears exhausted for this run — skipping remaining LLM calls, falling back to rule-based for the rest of this run.")

    raise last_error or LLMCallError("Unknown failure calling the Gemini API")
