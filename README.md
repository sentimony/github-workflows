# GitHub Workflows

Reusable GitHub Actions workflows for repositories in the Sentimony organization and
beyond.

## Auto Reviewer

The reusable workflow posts Claude reviews for eligible pull requests after CI. Its
`Auto Reviewer` commit status is advisory. The caller workflow name identifies the
workflow; `required-ci-job` / `required-ci-jobs` name the CI jobs checked by the gate. These are separate
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
comment omits Next step; the reviewer's internal draft retains its validated contract. The
workflow itself appends the draft's signature and, when the model's is missing or invalid,
the Next step line, so a slip in those lines does not fail the run.
History and cycle summaries continue to read earlier review formats.

### Contract

1. CI jobs: `required-ci-job` (one name) and `required-ci-jobs` (newline- or
   comma-separated list) are both optional strings that default to empty and are merged;
   the caller must give at least one, or the gate fails. A list containing a newline splits
   on newlines only, so a job name may hold a comma. Each name must match
   `[A-Za-z_][A-Za-z0-9 _.,()/-]*`. `skippable-ci-jobs` (same list format, default empty)
   names required jobs whose `skipped` conclusion counts as success.
2. CI source: `ci-events` is a comma-separated list of event names (default
   `push,workflow_dispatch`; use `pull_request` when the CI workflow runs on pull requests)
   and `ci-workflow` the file name of the CI workflow in `.github/workflows` (default
   `ci.yml`). The gate uses the newest run on the exact head from those events.
3. `reviewer` is the login of the account that owns the reviewer token (default
   `yngwald`). It posts the review, owns the ledger threads and summary, and is the account
   a review request must name. The workflow fails when the token belongs to someone else.
4. `review-changed-head` (boolean, default `false`). By default a new head is reviewed
   automatically only when the previous review left unresolved Critical or Important
   findings. When `true`, a new head is also reviewed after a clean review if the PR's own
   diff (`base...head`, compared by stable patch ID) changed since the last reviewed head;
   an identical diff, such as a clean merge of the base branch, is skipped with that
   reason. The round limit applies either way. If the diffs cannot be fetched or compared,
   the new head is reviewed.
5. `pr` is the optional PR-number string, with an empty default for manual dispatch.
6. Secrets: `CLAUDE_CODE_OAUTH_TOKEN` is required. The reviewer PAT comes from
   `AUTO_REVIEWER_TOKEN`, or from `AUTO_REVIEWER_YNGWALD` when the former is empty; both
   are optional in the schema, but the gate fails with a clear message when neither has a
   value. Existing callers that bind only `AUTO_REVIEWER_YNGWALD` keep working unchanged.
   The old `YNGWALD_PR_TOKEN` input is removed from the contract. Secret names alone do not
   guarantee nonempty values for fork or bot events.

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
dispatch or a review request for the reviewer bypasses the automatic round count, while
configuration validation still applies.

### Callers

Callers target `auto-reviewer-shared.yaml@v0.0.4`. Publish that tag before using these examples; GitHub cannot
resolve a reusable workflow at a tag that does not yet exist.
`workflow_run` uses the caller workflow
definition on its default branch; pull request events use the PR branch copy.
`workflow_dispatch` becomes available once the workflow exists on the default branch.
Live equivalence requires runs against the tagged callee.

Release tags are immutable: a published tag never moves. Changes between tags are listed
in [CHANGELOG.md](CHANGELOG.md).

This repository's `.github/workflows/auto-reviewer.yaml`, named `Auto Reviewer`,
targets `.github/workflows/auto-reviewer-shared.yaml@v0.0.4` with the `validate`
CI job. The reusable workflow is named `Auto Reviewer (Shared)`.

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

<details>
<summary>Another reviewer account (<code>so-upstars</code>, from <code>v0.0.6</code>)</summary>

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
    uses: sentimony/github-workflows/.github/workflows/auto-reviewer-shared.yaml@v0.0.6
    with:
      required-ci-jobs: |
        Lint
        Unit tests
      ci-workflow: ci.yml
      ci-events: pull_request
      reviewer: so-upstars
      review-changed-head: true
      pr: ${{ inputs.pr || '' }}
    secrets:
      AUTO_REVIEWER_TOKEN: ${{ secrets.SO_UPSTARS_PR_TOKEN }}
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

Use `check` for the SoundCloud caller; repeat `--required-ci-job` to require several jobs. The checker needs Ruby 3 or later; set `RUBY` to
its executable when needed. It reads the callee schema and job permissions. It does not
verify the caller's `uses` target, ref, or pin; verify those separately. It also does not
contact GitHub, inspect secret expression internals, or verify repository secret
availability and runtime values.
