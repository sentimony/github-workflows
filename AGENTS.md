# AGENTS.md

## Layout

1. `.github/workflows/auto-reviewer-shared.yaml` - the reusable Auto Reviewer
   (`workflow_call`): gate, read-only review, trusted publish, API-only thread resolver,
   completion and merged summary. Repeated steps share YAML anchors
   (`&review_actions_script`, `&write_review_helpers`): edit the anchored step, its aliases
   follow. Helpers used by more than one script live in the `Write review helpers` module
   (`auto_reviewer_lib`), not in copies. Every inline `python3` in it runs as
   `python3 -I`.
2. `.github/workflows/auto-reviewer.yaml` - this repository's own caller, on `@stable`.
   `.github/workflows/promote-stable.yml` fast-forwards the `stable` branch to a released
   commit of `main` with the deploy key in the `STABLE_DEPLOY_KEY` secret (`GITHUB_TOKEN`
   cannot push commits that change workflow files); `tests/test_promote_stable.py` runs
   its step against a local bare origin.
3. `.github/workflows/ci.yml` - the `validate` job: the unit tests and a pinned actionlint.
   `.github/workflows/skl-x-live.yml` runs `tests/test_skl_x_live.py` with
   `AUTO_REVIEWER_LIVE=1` (weekly, on dispatch and on PRs that change the install step):
   the real skl-x install compared with `git archive`. Without the variable the test is
   skipped, so `validate` stays offline.
4. `scripts/check_auto_reviewer_shared_contract.py` - the caller/callee contract checker
   (Ruby 3 or later).
5. `tests/` - `step(name)` in `tests/test_auto_reviewer_shared.py` runs a step's real
   `run` script with fake `gh`, `npx` and `git`; test a new script step through it, not
   through a copy. The README must keep exactly three `yaml` caller examples on `@stable`:
   a test checks them against the contract.
6. `AUTO_REVIEWER_SHARED_TRANSPORT.md` holds the draft, ledger and host actions contract;
   `README.md` the public contract and callers.

## Checks

Before a commit run the commands from README "Offline checks", the contract checker
against `.github/workflows/auto-reviewer.yaml` with `--required-ci-job validate`, and
`git diff --check`. The full test suite takes a few minutes.

## Rules

1. Branches are `YYMMDDHHMM-dev-session`; PRs are squash-merged, and the PR title becomes
   the commit title. Merging and tagging happen only on the owner's explicit request.
2. Record every change in `CHANGELOG.md` under the next `## vX.Y.Z - YYYY-MM-DD` heading;
   versions take patch bumps. A version is released when its PR is merged and CI on `main`
   is green: `Promote stable` then moves the `stable` branch to that commit, and callers on
   `@stable` follow it. The moving branch is the owner's explicit choice, an accepted
   trade-off and not a defect for review: a release reaches every caller, with its secrets,
   without a change in the caller. A caller may still pin a full SHA with the version in a
   comment (`@<sha> # vX.Y.Z`). No tag is needed; one goes on only at the owner's request,
   and tags never move.
3. A PR that changes the reusable workflow is reviewed and summarized by the released
   version: the own caller is on `@stable`, which moves only after the merge. `stable`
   moves forward only through `Promote stable`; moving it back is a manual push by an admin
   after the owner's word.
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
