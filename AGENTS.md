# AGENTS.md

## Layout

1. `.github/workflows/shared-auto-reviewer.yaml` - the reusable Auto Reviewer
   (`workflow_call`; named `auto-reviewer-shared.yaml` up to v0.0.16): gate, read-only review,
   trusted publish, API-only thread resolver, completion and merged summary. Repeated steps share
   YAML anchors (`&review_actions_script`, `&write_review_helpers`,
   `&resolve_workflow_identity`): edit the anchored step, its aliases follow. Helpers used by
   more than one script live in the `Write review helpers` module (`auto_reviewer_lib`), not in
   copies. Every inline `python3` in it runs as `python3 -I`.
2. `.github/workflows/auto-reviewer.yaml` - this repository's own caller, pinned to the full SHA
   of the previous release with a `# vX.Y.Z` comment (and that release's file name).
   `.github/workflows/tag-versions.yml` creates the annotated tag `vX.Y.Z` for every
   `CHANGELOG.md` version without one, on the first commit of `main` that holds the heading,
   with the deploy key in the `STABLE_DEPLOY_KEY` secret (`GITHUB_TOKEN` cannot push refs to
   commits that change workflow files; the secret keeps its name); `tests/test_tag_versions.py`
   runs its step against a local bare origin.
3. `.github/workflows/ci.yml` - the `validate` job: the unit tests and a pinned actionlint.
   `.github/workflows/cr-request-live-install.yml` runs `tests/test_cr_request_live_install.py` with
   `AUTO_REVIEWER_LIVE=1` (weekly, on dispatch and on PRs that change the install step):
   the real skl-x install compared with `git archive`. Without the variable the test is
   skipped, so `validate` stays offline.
4. `scripts/check_shared_auto_reviewer_contract.py` - the caller/callee contract checker
   (Ruby 3 or later).
5. `tests/` - `step(name)` in `tests/test_shared_auto_reviewer.py` runs a step's real
   `run` script with fake `gh`, `npx` and `git`; test a new script step through it, not
   through a copy. The README must keep exactly three `yaml` caller examples pinned as
   `@<sha> # vX.Y.Z`: a test checks them against the contract.
6. `SHARED_AUTO_REVIEWER_TRANSPORT.md` holds the draft, ledger and host actions contract;
   `README.md` the public contract and callers.

## Checks

Before a commit run the commands from README "Offline checks", the contract checker
against `.github/workflows/auto-reviewer.yaml` with `--required-ci-job validate`, and
`git diff --check`. The full test suite takes a few minutes.

## Rules

1. Branches are `YYMMDDHHMM-dev-session`; PRs are squash-merged, and the PR title becomes
   the commit title. Merging and tagging happen only on the owner's explicit request.
2. Record a change to what callers run - `shared-auto-reviewer.yaml` and anything it executes at
   review time - in `CHANGELOG.md` under the next `## vX.Y.Z - YYYY-MM-DD` heading; versions take
   patch bumps. Service changes (CI, tests, the live install check, tag automation, docs, this
   repository's own caller pin) get no `CHANGELOG.md` entry, no version and no tag: the PR title and
   body are their record. Version readers and `Tag versions` parse the headings, so add none for
   them. A version is released when its PR is merged and CI on `main` is green: `Tag versions` then
   tags its commit, and callers pin the full SHA of that commit with the version in a comment
   (`@<sha> # vX.Y.Z`), as third-party actions are pinned. The pin lives only in the caller's
   `uses:` line. Tags never move. The `stable` branch and its promotion were removed in v0.0.17; old
   callers on `@stable` keep the last commit it pointed at.
3. A PR that changes the reusable workflow leaves the own caller on the previous released
   commit, so the released version reviews and summarizes it. Move the caller to the new
   commit in a separate PR after the merge.
4. The Auto Reviewer format (footer, ledger, transport) belongs to this repository. The
   `cr-request` skill stays independent of its callers: put caller-specific rules in the
   workflow prompt or the transport contract, not in the skill.
5. Summaries and the resolver read the whole review history of a PR, so keep parsing and
   tests for earlier footers, severity badges and readiness forms when the format changes.
6. Native Claude Code installation uses the moving `latest` channel at the owner's
   explicit request. Trusting the official remote installer and latest CLI is an
   accepted trade-off, not a defect for review: updates must be available without
   waiting for a new action pin. Preserve the action SHA and the install step's
   secret-free, read-only job context. The installed version is recorded in the
   step summary for diagnosis; a pinned-version override is outside this contract.
7. The cr-request install uses the moving `skl-x@latest` at the owner's explicit request;
   this is an accepted trade-off, not a defect for review. The skill itself stays pinned
   by the `/tree/<ref>` source: skl-x installs exactly that tree, including the older
   `plugins/*/skills/` layouts, so the workflow adds no post-install ref check. The
   cr-request `metadata.version` does not follow the repository tags and cannot serve
   as one.
8. Released contracts are not kept as test snapshots (the v0.0.2 snapshot was removed at
   the owner's request): tags are immutable, and the checker is tested against the
   current workflow and callers. This is an accepted trade-off, not a defect for review.
9. Keep Actions secrets in each consumer repository, not at the organization level: the
   sentimony organization is on GitHub Free, where organization secrets do not reach
   private repositories, even when the REST list shows their names. Check the plan
   (`gh api orgs/<org> --jq .plan.name`) before proposing a migration.
10. `invalid header field value for "Authorization"` from `gh` in a job means a malformed
    secret value (a newline or another control character), not missing permissions: the
    request never left the runner. Ask the owner to re-save the PAT as one line without
    printing it, then rerun the failed jobs if the head is unchanged.
