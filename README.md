# GitHub Workflows

Reusable GitHub Actions workflows for repositories in the Sentimony organization and
beyond.

## Auto Reviewer

The reusable workflow posts Claude reviews for eligible pull requests after CI. Its
`Auto Reviewer` commit status is advisory. The caller workflow name identifies the
workflow; `required-ci-job` names the CI job checked by the gate. These are separate
names.

### Contract

1. `required-ci-job` is a required string. It must match `[A-Za-z_][A-Za-z0-9_-]*`.
2. `pr` and `pr_number` are optional strings with empty defaults for manual dispatch.
3. Bind both required secrets explicitly: `YNGWALD_PR_TOKEN` and
   `CLAUDE_CODE_OAUTH_TOKEN`. Secret names alone do not guarantee nonempty values for fork
   or bot events. Existing eligibility guards remain in place; live startup validation of
   secret availability remains a gap.

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

Squash-merge the central workflow into `main`, then create the `v0.0.1` tag on the
merged commit, push it to origin, and confirm it resolves before adopting it in either
caller. The examples and caller changes are preparatory until that tag exists; live
equivalence requires runs against the tagged callee. `workflow_run` uses the caller
workflow on its default branch.

Release tags are immutable: never move or recreate one. For later changes, create a new
patch tag on the merged central commit, push it to origin, confirm it resolves, and
update both callers to that exact tag before merging their changes.

The [transport contract](AUTO_REVIEWER_TRANSPORT.md) defines the review draft, ledger,
and host actions.

Agentic workflow (`validate`):

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
      pr_number:
        description: Legacy PR number
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
    uses: sentimony/github-workflows/.github/workflows/auto-reviewer.yaml@v0.0.1
    with:
      required-ci-job: validate
      pr: ${{ inputs.pr || '' }}
      pr_number: ${{ inputs.pr_number || '' }}
    secrets:
      YNGWALD_PR_TOKEN: ${{ secrets.YNGWALD_PR_TOKEN }}
      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

SoundCloud catalog (`check`):

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
      pr_number:
        description: Legacy PR number
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
    uses: sentimony/github-workflows/.github/workflows/auto-reviewer.yaml@v0.0.1
    with:
      required-ci-job: check
      pr: ${{ inputs.pr || '' }}
      pr_number: ${{ inputs.pr_number || '' }}
    secrets:
      YNGWALD_PR_TOKEN: ${{ secrets.YNGWALD_PR_TOKEN }}
      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

## Offline checks

Run `python3 -m unittest discover -s scripts -p 'test_*.py'` and
`actionlint .github/workflows/*.yaml .github/workflows/*.yml`. To validate a caller
against this checkout, run:

```sh
python3 scripts/check_auto_reviewer_contract.py --workflow .github/workflows/auto-reviewer.yaml --caller path/to/caller.yaml --required-ci-job validate
```

Use `check` for the SoundCloud caller. The checker needs Ruby 3 or later; set `RUBY` to
its executable when needed. It reads the callee schema and job permissions. It does not
verify the caller's `uses` target, ref, or pin; verify those separately. It also does not
contact GitHub, inspect secret expression internals, or verify repository secret
availability and runtime values.
