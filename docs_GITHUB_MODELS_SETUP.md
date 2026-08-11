# Setup: GitHub Models (the LLM-backed judgment layer)

This is what does the actual analytical work now — scoring significance,
writing original summaries, and reasoning about escalation risk — closer
to what the original prompt asked of a human analyst. It costs nothing
beyond your existing GitHub account and stays inside GitHub's free
per-account rate limits for a run this size (roughly 6-7 calls/day: one
per region with candidate material, plus one cross-region synthesis
call).

**Nothing to sign up for.** It authenticates with the same `GITHUB_TOKEN`
your Actions workflow already has — no separate API key, no new secret.

## How it's wired in

1. `.github/workflows/daily.yml` now requests `permissions: models: read`
   at the workflow level, and passes `GITHUB_TOKEN` into the "Build
   today's edition" step's environment.
2. `scripts/build_edition.py` checks for that token at the start of a
   run. If present, it tries an LLM call for each region with candidate
   material, then one more for cross-region synthesis (top five, top
   story, escalation risks).
3. **Every one of those calls has a rule-based fallback.** If a call
   fails — rate limited, network error, malformed JSON back from the
   model — that specific region (or the synthesis step) falls back to
   the original keyword-scoring/extractive-summary logic instead of
   breaking the run. Check `source_notes.limitations` on any edition to
   see exactly which regions used which path that day.

## Choosing a model

Default is `openai/gpt-4o-mini`, set in `scripts/llm_client.py`. Override
by setting a `DAILY_BELLE_MODEL` environment variable (add it to the
workflow step's `env:` block) — e.g. `openai/gpt-4o` for higher quality
at a lower daily request allowance, or a Llama/Mistral model if you'd
rather avoid OpenAI models specifically. Model IDs and current free-tier
limits do change — check
[GitHub's Models documentation](https://docs.github.com/en/github-models)
and the [Models marketplace](https://github.com/marketplace/models) in
your own account for what's currently available before assuming the
default still exists.

## Testing locally

The workflow's `GITHUB_TOKEN` only exists inside Actions. To test this
locally:

1. Create a fine-grained Personal Access Token with the **Models: read**
   permission (Settings → Developer settings → Personal access tokens).
2. `export GITHUB_TOKEN=<your token>`
3. `python scripts/build_edition.py --date 2026-08-18`

Watch the console output — it prints which path each region actually
took (`GITHUB_TOKEN found — will attempt LLM-backed generation...` at
the start, and `::warning::` lines for any call that fell back).

## Forcing pure rule-based generation

```bash
python scripts/build_edition.py --no-llm
```

Useful for comparing output quality, or if you want to temporarily
disable LLM calls without touching the workflow file or revoking the
token.

## What to actually watch for after the first few live runs

- **`source_notes.limitations`** — the honest self-report of what
  happened. If most regions are consistently falling back to rule-based,
  something's wrong with the token/permission setup or you're hitting
  rate limits — check the Actions log for the specific `::warning::`
  reason.
- **Body text quality** — this is the whole point of this layer. Real
  paraphrased writing, not extractive stitching, should be visible
  immediately in any region that used the LLM path.
- **Escalation risks** — the rule-based version used fixed templates
  keyed on keyword category; the LLM version should show actual
  forward-looking reasoning distinct from the top-five items. If it
  reads generic or just repeats top-five content, the prompt in
  `scripts/llm_pipeline.py` (`_synthesis_user_prompt`) may need
  tightening.
- **GitHub Models rate limits are per-account, not per-repo.** If you run
  other things through GitHub Models on the same account, this pipeline
  shares that daily allowance.
