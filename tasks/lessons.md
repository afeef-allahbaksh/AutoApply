# Lessons Learned

1. **Custom question answers must be concise** — Answer like a human would on a form. "June 2026" not "I would be available to start immediately after my graduation in June 2026. Given my recent experience...". No essays, no filler, no "excited about the opportunity" language. 1-2 sentences max for open-ended, bare values for factual questions.

2. **Read description divs for real question text** — Greenhouse forms use a pattern where the label is a section header (e.g. "(Optional) Personal Preferences") and the actual question (e.g. "How do you pronounce your name?") is in a `div.question-description` referenced by `aria-describedby`. Always check `#{q_id}-description` for the real question before falling back to the label.

4. **Never fill a field with non-answers** — If the model can't answer or there's no real question, leave the field blank. Filling fields with "I don't see a question" or explanations is a dead giveaway for automated applications. Use a SKIP sentinel so the model can signal "don't fill this" cleanly.

5. **Smart optional question handling** — Don't blanket-skip all optional questions. Three tiers: (a) always skip purely personal/subjective ones (pronounce name, preferences, pronouns), (b) skip data-dependent ones only if user lacks the data (website, portfolio, referral), (c) still answer job-relevant optional ones (start date, timelines). Don't assume a field is useless — website/portfolio/referral strengthen applications when the user has them.

3. **`apply` must be self-contained** — The `apply` command should auto-generate a tailored resume if one doesn't already exist for the job, rather than requiring `optimize` to be run separately. Every command should work standalone without requiring the user to remember prerequisite steps.
