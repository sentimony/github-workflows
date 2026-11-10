# Changelog

One release tag covers every workflow in this repository. Each release lists its changes
per workflow, newest first.

## Unreleased

### auto-reviewer-shared

1. The review actions script is defined once: `Complete review actions` reuses the
   `Publish review actions` script through a YAML anchor, and `PHASE` still selects
   PREPARE or COMPLETE behavior. The workflow shrinks by 468 lines.
2. The ledger lists repository issues once per run instead of once per accepted deferred
   finding.

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
