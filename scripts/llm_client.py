"""
llm_client.py — thin client for GitHub Models' OpenAI-compatible chat
completions endpoint.

Free within GitHub's per-account rate limits, and authenticates with the
same GITHUB_TOKEN your Actions workflow already has — no separate signup,
no separate secret. See docs_GITHUB_MODELS_SETUP.md for setup and current
limits.

This is the ONE module in the pipeline that calls out to a hosted model.
Everything else (fetch.py, analyze.py) stays deterministic and free of
any AI dependency, so the pipeline can always fall back to pure
rule-based generation if a call here fails or GITHUB_TOKEN isn't set —
see llm_pipeline.py for how that fallback is wired in.
"""
import json
import os
import re
import time

import requests

GITHUB_MODELS_ENDPOINT = "https://models.github.ai/inference/chat/completions"
DEFAULT_MODEL = os.environ.get("DAILY_BELLE_MODEL", "openai/gpt-4o-mini")
REQUEST_TIMEOUT = 60
MAX_RETRIES = 3


class LLMCallError(Exception):
    """Raised for any failure calling GitHub Models — missing token, rate
    limit exhausted after retries, malformed response, invalid JSON.
    Callers should catch this specifically and fall back to rule-based
    generation rather than let one bad call break the whole edition.
    """


def _get_token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise LLMCallError(
            "No GITHUB_TOKEN/GH_TOKEN in environment — GitHub Models calls "
            "require it. Locally, export a personal access token with "
            "'models: read' scope; in Actions, pass "
            "secrets.GITHUB_TOKEN into the step's env and add "
            "'permissions: models: read' to the workflow."
        )
    return token


def _extract_json(text: str) -> dict:
    """Models sometimes wrap JSON in code fences despite being told not
    to — strip those before parsing."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    return json.loads(text)


def call_json(system_prompt: str, user_prompt: str, model: str = None, temperature: float = 0.3) -> dict:
    """Calls GitHub Models with a system+user prompt pair, expecting a
    single JSON object back as the entire response content. Retries on
    rate limiting (HTTP 429) with backoff. Raises LLMCallError on any
    failure after retries are exhausted — never returns a partial or
    guessed result.
    """
    token = _get_token()
    model = model or DEFAULT_MODEL
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
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
            resp = requests.post(GITHUB_MODELS_ENDPOINT, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429:
                wait = min(30, 2 ** attempt)
                print(f"::warning::GitHub Models rate-limited (attempt {attempt}/{MAX_RETRIES}), waiting {wait}s")
                last_error = LLMCallError(f"Rate limited: {resp.text[:300]}")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return _extract_json(content)
        except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError) as e:
            last_error = LLMCallError(f"GitHub Models call failed (attempt {attempt}/{MAX_RETRIES}): {e}")
            print(f"::warning::{last_error}")
            time.sleep(min(10, 2 * attempt))

    raise last_error or LLMCallError("Unknown failure calling GitHub Models")
