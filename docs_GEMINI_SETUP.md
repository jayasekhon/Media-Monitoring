# Setup: Google AI Studio / Gemini (the LLM-backed judgment layer)

This is what does the actual analytical work — scoring significance,
writing original summaries, and reasoning about escalation risk — closer
to what the original prompt asked of a human analyst.

**This replaces an earlier GitHub Models-based version of this layer.**
GitHub Models was fully retired on July 30, 2026 (confirmed via GitHub's
own changelog) — if you're reading old notes or an old zip that mentions
`GITHUB_TOKEN` and `permissions: models: read` for this purpose, that no
longer works and never will again. This doc describes the current setup.

**Not zero-signup, but still free.** Unlike GitHub Models, this needs a
Gemini API key from a Google account — free, no credit card — stored as
a repo secret.

## 1. Get a free API key

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
   and sign in with a Google account.
2. Click "Get API Key" → "Create API key". No credit card required.
3. Copy the key.

## 2. Add it as a repo secret

1. In your repo: **Settings → Secrets and variables → Actions → New
   repository secret**.
2. Name: `GEMINI_API_KEY`. Value: the key from step 1.
3. Save.

That's the whole setup — `.github/workflows/daily.yml` already passes
this secret into the build step's environment, and `scripts/llm_client.py`
picks it up automatically.

## Model and free-tier limits — check before assuming

As of this writing, Google's free tier covers **Flash-class models only**
(e.g. `gemini-3.6-flash`, `gemini-3.5-flash`, `gemini-3.5-flash-lite`,
`gemini-2.5-flash`) — Pro-series models are paid-only. Rate limits vary
by model, roughly in the 5-15 requests/minute and 100-1,500
requests/day range for Flash models.

**Real example of why you shouldn't trust any of the above for long:**
this file originally shipped with `gemini-2.5-flash` as the default,
based on a third-party cheatsheet. The real endpoint returned `404 Not
Found` on every call. Google's own current docs example uses
`gemini-3.6-flash` instead — the model generation had simply moved on.
`scripts/llm_client.py` now defaults to `gemini-3.6-flash` and treats any
404 (specifically, not other error codes) as "wrong model ID," failing
fast with a pointer to Google's docs rather than burning retries on a
call that will never succeed. If you hit this again, that's the fix —
check [ai.google.dev/gemini-api/docs/openai](https://ai.google.dev/gemini-api/docs/openai)
for the current example model, or set `GEMINI_MODEL`.

**This entire space has proven to change fast** — GitHub Models existed,
seemed stable, and was fully gone within a year. Before assuming any of
the above is still accurate:
- Check [ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing)
  for what's currently free.
- Check your own project's live limits in AI Studio's rate-limit view.

Override the model via a `GEMINI_MODEL` environment variable (add to the
workflow step's `env:` block) if the default in `scripts/llm_client.py`
(`gemini-2.5-flash`) stops being free-tier-eligible or gets renamed.

## Call budget and pacing

Same shape as before: one call per region with candidate material (up to
6) plus one cross-region synthesis call, ≈7 calls/run, once daily —
comfortably under even the lower end of Gemini's free-tier daily quota.

Because Gemini's free tier can be as low as ~5 requests/minute for some
models, and all ~7 calls happen in one job run, `build_edition.py` waits
`LLM_CALL_SPACING_SECONDS` (default 8s) between calls to avoid tripping
the per-minute limit outright, on top of the retry/backoff already in
`llm_client.py` for when spacing alone isn't enough.

## Testing locally

```bash
export GEMINI_API_KEY=<your key>
python scripts/build_edition.py --date 2026-08-22
```

Watch the console output — it prints which path each region actually
took, and `::warning::` lines for anything that fell back.

## Forcing pure rule-based generation

```bash
python scripts/build_edition.py --no-llm
```

## What to watch for after the first few live runs

- **`source_notes.limitations`** on the edition itself — the honest,
  per-run report of which regions used Gemini vs. fell back. Check this
  before digging into Actions logs.
- **If every region falls back identically** (not a partial/rate-limited
  pattern), that's a systemic issue, not quota exhaustion — check the
  raw step log (not just the Annotations summary panel, which caps how
  many warnings it displays) for the actual error. A `410 Gone` or `404`
  means the endpoint or model ID has changed since this was written; a
  `401`/`403` means the key or its permissions are wrong.
- **Data privacy note:** on the free tier, Google may use prompts and
  outputs to improve their models (this is standard for free-tier
  Gemini API usage). If that's a concern for how this briefing's
  underlying source material should be handled, that's a reason to
  either accept rule-based-only generation or move to a paid tier that
  opts out of data use — not something this pipeline can route around.
