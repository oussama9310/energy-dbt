# Working rules for this project
- I am learning data engineering. Explain the *why* behind every model, test and config you write.
- Before writing a model, state its grain (what one row represents) and its unique key.
- Never modify the raw schema or raw files.
- Prefer small, single-purpose models (staging → intermediate → marts).
- After changes, suggest the checks I should run to verify the result (row counts, uniqueness, nulls).
- Don't run commands that delete data without asking me first.
- Never rely on type auto-detection: state the delimiter, null strings and sample size explicitly in every load.
- Before every commit, show me the list of files being committed (git status and git diff --cached --stat), and flag any generated, cache, data or personal-path files.
