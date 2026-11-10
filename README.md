# GitHub Workflows

Reusable GitHub Actions workflows for repositories in the Sentimony organization and
beyond.

## Auto Reviewer

The reusable workflow posts Claude reviews for eligible pull requests after CI. Its
`Auto Reviewer` commit status is advisory. The caller workflow name identifies the
workflow; `required-ci-job` names the CI job checked by the gate. These are separate
names.

The runner installs Claude Code from the native `latest` channel on each review run,
checks that executable with `--version`, records the version in the step summary,
and passes its path to the pinned
`anthropics/claude-code-action`. The CLI version can change between runs; the action SHA
remains unchanged. Installation retries up to three times and stops the review if every
attempt fails. The review duration starts after installation and immediately before
inference.

The cr-request skill is installed with `npx -y --prefer-online skl-x@latest add
<source>/tree/<ref> -s cr-request -a claude-code -g -y -m sm`, using the runner's Node.
The `skl-x` version moves with each release; the skill itself comes from the newest
`vX.Y.Z` tag of `sentimony/agentic-workflow` or the `REVIEW_SKILLS_REF` commit, must
install as a plain directory without symbolic links, and must name its version.

Published comments show readiness first, then the verdict table, a collapsed Details
block, and a Used footer. The footer links skill and workflow versions in superscript,
groups model and effort, and shows measured duration, tokens and cost. The public
comment omits Next step; the reviewer's internal draft retains its validated contract.
History and cycle summaries continue to read earlier review formats.

### Contract

1. `required-ci-job` is a required string. It must match `[A-Za-z_][A-Za-z0-9_-]*`.
2. `pr` is the only optional PR-number string, with an empty default for manual dispatch.
3. The unreleased source requires secret inputs `AUTO_REVIEWER_YNGWALD` and
   `CLAUDE_CODE_OAUTH_TOKEN`, each bound to the repository secret of the same name.
   The old `YNGWALD_PR_TOKEN` input is removed from the source contract. The caller
   examples target the upcoming `v0.0.4` release described under [Callers](#callers).
   Secret names alone do not guarantee nonempty values for fork or bot events. Existing
   eligibility guards remain in place; live startup validation of secret availability
   remains a gap.

The caller must declare an explicit permission ceiling covering the union of permissions
used by the callee jobs. The caller job must omit `concurrency`, `runs-on`, and `steps`.
Internal jobs have narrower permissions; only the API-only thread resolver has
`contents: write`. The resolver uses the GitHub API and does not check out code.

Set repository variables in the caller repository; the callee reads that context:

1. `CLAUDE_REVIEW_MAX_ROUNDS`: empty defaults to 10; otherwise use a positive integer matching
   `0*[1-9][0-9]*`. Leading zeroes are accepted. An invalid value fails even on manual runs.
2. `CLAUDE_REVIEW_MODEL`: empty defaults to `claude-sonnet-5-5`.
3. `CLAUDE_REVIEW_EFFORT`: empty defaults to `medium`; allowed values are `low`, `medium`, `high`.
4. `REVIEW_SKILLS_REF`: optional full commit SHA pin for the review skills; empty selects
   the latest released tag.

At most one `auto-review-rounds-<number>` PR label may set a positive integer override for
the repository round limit. Invalid or multiple matching labels fail the gate. A manual
dispatch or a review request for yngwald bypasses the automatic round count, while
configuration validation still applies.

### Callers

Callers target `auto-reviewer-shared.yaml@v0.0.4`. Publish that tag before using these examples; GitHub cannot
resolve a reusable workflow at a tag that does not yet exist.
`workflow_run` uses the caller workflow
definition on its default branch; pull request events use the PR branch copy.
`workflow_dispatch` becomes available once the workflow exists on the default branch.
Live equivalence requires runs against the tagged callee.

Release tags are immutable: never move or recreate one. For later changes, create a new
patch tag on the merged central commit, push it to origin and confirm it resolves. Record
each change in the same PR under the next patch tag's section of
[CHANGELOG.md](CHANGELOG.md), and add the release date to that heading when tagging.

This repository's `.github/workflows/auto-reviewer.yaml`, named `Auto Reviewer`,
targets `.github/workflows/auto-reviewer-shared.yaml@v0.0.4` with the `validate`
CI job. The reusable workflow is named `Auto Reviewer (Shared)`. The caller requires
the published `v0.0.4` tag; the preceding review phase used the released `v0.0.3`
workflow to review the proposed changes, which does not prove the new engine's runtime.
Configure Actions secrets in each consumer repository:

1. Create `AUTO_REVIEWER_YNGWALD` and `CLAUDE_CODE_OAUTH_TOKEN`, with separate token
   values for each repository.
2. Update the caller to read `secrets.AUTO_REVIEWER_YNGWALD`, as shown below.
3. Delete the old repository secret `YNGWALD_PR_TOKEN` after all workflows in that
   repository use the new secret name.

For `v0.0.3`, bind the input directly to the repository secret of the same name:
`AUTO_REVIEWER_YNGWALD: ${{ secrets.AUTO_REVIEWER_YNGWALD }}`.

When migrating from `v0.0.1` or `v0.0.2`, update both the caller's version pin and the
left-side input key. Those immutable releases retain `YNGWALD_PR_TOKEN` as their input;
updating only the key while still calling either release fails workflow validation.
Consumers using those releases map the legacy input to `secrets.AUTO_REVIEWER_YNGWALD`;
they do not need a repository secret named `YNGWALD_PR_TOKEN`.

CI runs on branch pushes, pull requests, and manual dispatch. Tag pushes do not trigger it.

The [transport contract](AUTO_REVIEWER_SHARED_TRANSPORT.md) defines the review draft, ledger,
and host actions.

<details>
<summary>Agentic workflow (<code>validate</code>)</summary>

```yaml
name: Auto Reviewer
on:
  workflow_run:
    workflows: [CI]
    types: [completed]
  pull_request:
    types: [opened, reopened, ready_for_review, review_requested, closed]
  workflow_dispatch:
    inputs:
      pr:
        description: PR number
        required: false
        type: string
permissions:
  actions: read
  contents: write
  pull-requests: write
  issues: read
  statuses: write
jobs:
  review:
    uses: sentimony/github-workflows/.github/workflows/auto-reviewer-shared.yaml@v0.0.4
    with:
      required-ci-job: validate
      pr: ${{ inputs.pr || '' }}
    secrets:
      AUTO_REVIEWER_YNGWALD: ${{ secrets.AUTO_REVIEWER_YNGWALD }}
      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

</details>

<details>
<summary>SoundCloud catalog (<code>check</code>)</summary>

```yaml
name: Auto Reviewer
on:
  workflow_run:
    workflows: [CI]
    types: [completed]
  pull_request:
    types: [opened, reopened, ready_for_review, review_requested, closed]
  workflow_dispatch:
    inputs:
      pr:
        description: PR number
        required: false
        type: string
permissions:
  actions: read
  contents: write
  pull-requests: write
  issues: read
  statuses: write
jobs:
  review:
    uses: sentimony/github-workflows/.github/workflows/auto-reviewer-shared.yaml@v0.0.4
    with:
      required-ci-job: check
      pr: ${{ inputs.pr || '' }}
    secrets:
      AUTO_REVIEWER_YNGWALD: ${{ secrets.AUTO_REVIEWER_YNGWALD }}
      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

</details>

## Offline checks

From the repository root, run `python3 -m unittest discover -s tests -p 'test_*.py'` and
`actionlint .github/workflows/*.yaml .github/workflows/*.yml`. To validate a caller
against this checkout, run:

```sh
python3 scripts/check_auto_reviewer_shared_contract.py --workflow .github/workflows/auto-reviewer-shared.yaml --caller path/to/caller.yaml --required-ci-job validate
```

Use `check` for the SoundCloud caller. The checker needs Ruby 3 or later; set `RUBY` to
its executable when needed. It reads the callee schema and job permissions. It does not
verify the caller's `uses` target, ref, or pin; verify those separately. It also does not
contact GitHub, inspect secret expression internals, or verify repository secret
availability and runtime values.
