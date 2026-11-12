# Changelog

One release tag covers every workflow in this repository. Each release lists its changes
per workflow, newest first.

## v0.0.7

### auto-reviewer-shared

1. The review body's closing lines are deterministic. `Add review duration` drops any
   trailing `Reviewed with:` and `Next step:` lines the model wrote and appends the
   canonical signature (workflow ref, skill version, measured duration, usage) itself, so a
   missing or malformed trailer no longer fails Publish with "The review body does not end
   with the signature and Next step lines". A valid model `Next step` is kept; otherwise it
   is derived from the Summary table and readiness (`/verify -> /finalize squash` only for
   a plain Ready with no findings, else `/cr-received PR #<n>`). The published format is unchanged. Fixes #11.
2. Missing `</details>`: a Details block the agent opened but did not close is closed by the
   same step before the closing lines.
3. One retry: `Pre-validate review draft` runs the publish job's own review-actions validator
   (one shared definition) and a Details/readiness/Summary table check on the draft. On any
   error it writes `.auto-reviewer/retry-error.txt` and a second `claude-code-action` run
   (same prompt, which tells it to fix only the listed errors in the existing files) repairs
   the draft; the closing lines, usage (summed over both runs) and inline comments are then
   produced again. Publish validation is unchanged and still decides.

## v0.0.6

### auto-reviewer-shared

1. New input `reviewer` (default `yngwald`) replaces every hard-coded reviewer login: the
   gate, review-request filter, concurrency group, thread resolver, merged summary and the
   token-owner check.
2. New optional secret `AUTO_REVIEWER_TOKEN`; `AUTO_REVIEWER_YNGWALD` is now optional.
   The token is `AUTO_REVIEWER_TOKEN` when set, else `AUTO_REVIEWER_YNGWALD`; the gate (and
   the merged summary) fails with a clear message when neither is set.
3. New inputs `required-ci-jobs` (newline- or comma-separated), `skippable-ci-jobs`,
   `ci-events` (default `push,workflow_dispatch`) and `ci-workflow` (default `ci.yml`);
   `required-ci-job` is no longer required, and the gate fails when no job is named. Job
   names may now contain spaces, dots, commas (in newline-separated lists), parentheses and slashes.
4. New opt-in input `review-changed-head` (default `false`): a new head after a clean
   review is reviewed when the PR's own diff changed since the last reviewed head, and
   skipped when only the base branch moved. The round limit still applies.
5. The contract checker accepts any CI job name, several `--required-ci-job` values and
   either CI job input, and requires one of the two reviewer token secrets. The README
   gains a caller example for another reviewer account.
6. All changes are backward compatible: callers of `v0.0.5` need no edits.

## v0.0.5 - 2026-11-10

### auto-reviewer-shared

1. The review actions script is defined once: `Complete review actions` reuses the
   `Publish review actions` script through a YAML anchor, and `PHASE` still selects
   PREPARE or COMPLETE behavior. This alone removes 472 lines; the whole release shrinks
   the workflow from 2852 to 2210 lines.
2. The helpers shared by the merged summary and the review actions scripts (API access,
   ledger validation, thread lookup, disposition markers and the review footer patterns) are
   defined once: a `Write review helpers` step writes them as a module to `$RUNNER_TEMP`, and
   both scripts import it and call `configure()`.
3. The ledger lists repository issues once per run instead of once per accepted deferred
   finding.
4. `Validate review body` parses the Summary table and readiness once, in the renderer,
   with the same strict row grammar. The inline comments and review actions JSON are
   checked for token-like strings only after decoding, and invalid JSON now sets a failure
   status.
5. The review job installs cr-request with `skl-x@latest` from the same released tag or
   `REVIEW_SKILLS_REF` commit, instead of fetching the repository with git; the installed
   skill must still be a plain directory with no symbolic links.
6. Smaller cleanups: one failure reporter shared by the publish and complete jobs, no
   duplicate `is_positive`/`nonempty` helpers or nested `datetime` import, and one
   `Reviewed with:` prefix pattern in the merged summary. Unused imports, the unread
   `ledger_outcome` output, a no-op `mkdir`, a repeated `export` and a second fetch of the
   published review after `guard()` are gone, and the renderer defines its Readiness
   pattern once.
7. The review work directory is `.auto-reviewer/` instead of `.auto-review/`; a PR that tracks
   files in `.auto-reviewer/` fails the review job.
8. Tests no longer keep the v0.0.2 contract snapshot, and the four thread badge styles share
   one test. The README caller examples are collapsible sections.

## v0.0.4 - 2026-11-10

### auto-reviewer-shared

1. Breaking: the reusable workflow moved from `auto-reviewer.yaml` to
   `auto-reviewer-shared.yaml` and is named `Auto Reviewer (Shared)`. Callers update the
   path in `uses:`.
2. Breaking: the legacy `pr_number` input is removed; manual dispatch passes `pr`.
3. The review job installs the latest Claude Code before the review action and records the
   installed version.
4. Reviews lead with readiness, link the workflow and skill versions in the footer, use
   concentric circles for severity, and keep quoted code and unmatched backticks intact.

### Repository

1. This repository's caller `auto-reviewer.yaml` (`Auto Reviewer`) replaces
   `self-auto-reviewer.yaml` and targets `auto-reviewer-shared.yaml@v0.0.4`.

## v0.0.3 - 2026-11-09

### auto-reviewer

1. Breaking: the secret input `YNGWALD_PR_TOKEN` is renamed to `AUTO_REVIEWER_YNGWALD`.
   Callers update both the version pin and the input key.
2. Review cycle summaries are compact, usage totals are formatted, and the footer names the
   called workflow reference in separate code spans.

### Repository

1. The self-review caller is named `Self Auto Reviewer`.
2. Workflow tests and fixtures moved from `scripts/` to `tests/`.

## v0.0.2 - 2026-11-09

### auto-reviewer

1. Review footers are formatted, and earlier review history is preserved.

### Repository

1. Self review: `self-auto-reviewer.yaml` reviews this repository's PRs with
   repository-specific credentials.
2. CI no longer runs on tag pushes.

## v0.0.1 - 2026-11-09

### auto-reviewer

1. First release of the reusable Auto Reviewer workflow: a cr-request PR review posted as
   yngwald after green CI, with caller CI adapters and configurable automatic round limits.
2. The transport contract (`AUTO_REVIEWER_TRANSPORT.md`) defines the review draft, ledger
   and integration contracts.

### Repository

1. CI validates the reusable workflow contract offline.
