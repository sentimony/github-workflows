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
3. The unreleased source requires secret inputs `AUTO_REVIEWER_YNGWALD` and
   `CLAUDE_CODE_OAUTH_TOKEN`, each bound to the repository secret of the same name.
   The old `YNGWALD_PR_TOKEN` input is removed from the source contract. The published
   caller examples below still use the `v0.0.2` contract described under [Callers](#callers).
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

Callers use the published `v0.0.2` tag. `workflow_run` uses the caller workflow
definition on its default branch; pull request events use the PR branch copy.
`workflow_dispatch` becomes available once the workflow exists on the default branch.
Live equivalence requires runs against the tagged callee.

Release tags are immutable: never move or recreate one. For later changes, create a new
patch tag on the merged central commit, push it to origin, confirm it resolves, and
update callers to that exact tag before merging their changes.

This repository's `.github/workflows/self-auto-reviewer.yaml`, named `Self Auto Reviewer`,
calls the published `.github/workflows/auto-reviewer.yaml` with the `validate` CI job.
Configure Actions secrets in each consumer repository:

1. Create `AUTO_REVIEWER_YNGWALD` and `CLAUDE_CODE_OAUTH_TOKEN`, with separate token
   values for each repository.
2. Update the caller to read `secrets.AUTO_REVIEWER_YNGWALD`, as shown below.
3. Delete the old repository secret `YNGWALD_PR_TOKEN` after all workflows in that
   repository use the new secret name.

The published `v0.0.2` callee still requires the input key `YNGWALD_PR_TOKEN` on the
left side of the mapping. Its value on the right reads `secrets.AUTO_REVIEWER_YNGWALD`;
consumers do not need a repository secret named `YNGWALD_PR_TOKEN`.

The unreleased source removes that legacy input. After the next release is published,
update the caller's version pin and replace the left-side key with
`AUTO_REVIEWER_YNGWALD`, yielding
`AUTO_REVIEWER_YNGWALD: ${{ secrets.AUTO_REVIEWER_YNGWALD }}`. Updating only the key while
still calling `v0.0.2` fails workflow validation. Existing `v0.0.1` and `v0.0.2` tags
retain their original interface.

CI runs on branch pushes, pull requests, and manual dispatch. Tag pushes do not trigger it.

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
    uses: sentimony/github-workflows/.github/workflows/auto-reviewer.yaml@v0.0.2
    with:
      required-ci-job: validate
      pr: ${{ inputs.pr || '' }}
      pr_number: ${{ inputs.pr_number || '' }}
    secrets:
      YNGWALD_PR_TOKEN: ${{ secrets.AUTO_REVIEWER_YNGWALD }}
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
    uses: sentimony/github-workflows/.github/workflows/auto-reviewer.yaml@v0.0.2
    with:
      required-ci-job: check
      pr: ${{ inputs.pr || '' }}
      pr_number: ${{ inputs.pr_number || '' }}
    secrets:
      YNGWALD_PR_TOKEN: ${{ secrets.AUTO_REVIEWER_YNGWALD }}
      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

## Offline checks

From the repository root, run `python3 -m unittest discover -s tests -p 'test_*.py'` and
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
