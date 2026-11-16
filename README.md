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

Published comments show readiness first, as a bold line (`**Readiness: Ready with fixes**`),
then the verdict table, a collapsed Details block, and a Used footer. Neither comment kind has
Markdown headings: a section name is a bold line on its own (`**Review cycle summary**`,
`**Review usage**`, and the section names inside Details). Headings that the reviewer's draft
holds in Details are rewritten as bold lines.

The footer reads, for example:
`Used: model `claude-sonnet-5-5 medium` · skill `cr-request` [0.0.23](https://www.skills.sh/sentimony/agentic-workflow/cr-request) · workflow `shared-auto-reviewer` [v0.0.17](https://github.com/sentimony/github-workflows/commit/<sha>) · 143,575 tokens · $0.1645`.
Model and effort come first in one code span, then the skill and the workflow with plain linked
versions, then plain tokens and cost; without usage data the model, tokens and cost are omitted.
The footer carries no durations. The merged summary has one `Run time` column: the duration
of the Actions run that published each review, from the ledger's `run_id`, read through the API
as the Actions UI shows it (start to last update). The run that builds the summary is still in
progress, so its round counts from the run's start to now; the summary built when the PR
merges has every run complete. A review without a valid ledger, or a run the API no longer
returns, shows plain `unknown`. Measured values are code spans, `Total` is bold with its values
in code spans, and the last line is `Used: workflow `shared-auto-reviewer` vX.Y.Z`. Both summary
jobs need `actions: read`, which the callers below already grant.

The workflow version reads `vX.Y.Z`, linked to the commit that ran. For a caller pinned to a
commit it is the comment of the caller's `uses:` line, which counts only when the newest
`CHANGELOG.md` heading at that commit names the same version; without the comment, or on a
mismatch, the footer shows the SHA. For a caller on a branch it is the newest `CHANGELOG.md`
heading at the commit the branch resolved to for that run. The public comment omits Next step;
the reviewer's internal draft retains its validated contract. The workflow itself appends the
draft's signature and, when the model's is missing or invalid, the Next step line, closes an
unclosed Details block, and, when the draft still fails its structural validation, asks the
model once to fix the listed errors before publish. History and cycle summaries continue to read
earlier review formats: the `Used:` forms of v0.0.13-v0.0.16, the `## Readiness: X` heading,
`Readiness: **X**`, and the `Reviewed with:` signatures.

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
4. `review-changed-head` (boolean, default `true`). A new head is reviewed automatically
   when the previous review left unresolved Critical or Important findings, and also after
   a clean review if the PR's own diff (`base...head`, compared by stable patch ID) changed
   since the last reviewed head; an identical diff, such as a clean merge of the base
   branch, is skipped with that reason. The round limit applies either way. If the diffs
   cannot be fetched or compared, the new head is reviewed. Set `false` to review a new
   head only after unresolved Critical or Important findings. A review request that names
   the reviewer always gets a review of the current head.
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

Callers pin `shared-auto-reviewer.yaml` to the full SHA of a commit in `main` and name its
CHANGELOG version in a comment: `@<sha> # vX.Y.Z`. The pin lives only in the caller's `uses:`
line. A caller can move to a commit as soon as it is merged; the commit of a version is the
one its tag `vX.Y.Z` points to. The examples below show a placeholder SHA: replace it with
`git rev-parse vX.Y.Z^{commit}` of the release you pin.
The workflow was named `auto-reviewer-shared.yaml` up to `v0.0.16`; a caller keeps that name
while it pins one of those commits and changes the name together with the pin.
`workflow_run` uses the caller workflow
definition on its default branch; pull request events use the PR branch copy.
`workflow_dispatch` becomes available once the workflow exists on the default branch.
Live equivalence requires runs against the referenced callee.

Changes between versions are listed in [CHANGELOG.md](CHANGELOG.md). The `Tag versions`
workflow creates the annotated tag `vX.Y.Z` for each `CHANGELOG.md` version after a green CI
run on `main`, on the first commit of `main` that holds the heading; a tag never moves.

This repository's `.github/workflows/auto-reviewer.yaml`, named `Auto Reviewer`,
pins the previous release the same way, with the `validate` CI job; a PR that changes the
reusable workflow is reviewed by the released version, and a follow-up PR moves the pin to the new
commit. The reusable workflow is named `Shared Auto Reviewer`.

Configure Actions secrets in each consumer repository:

1. For a new `sentimony` repository, create the `AUTO_REVIEWER_YNGWALD` value as a
   fine-grained PAT of `yngwald` with resource owner `sentimony`, only that repository,
   and Pull requests and Issues set to Read and write. It creates a request the org owner
   approves at `github.com/organizations/sentimony/settings/personal-access-token-requests`.
   Copy the value with "Copy token", not from a screenshot, and check that `gh api user`
   returns `yngwald` with it. A private repository of a personal account cannot take a
   fine-grained PAT of another user; give `yngwald` a classic PAT with `repo` scope there.
2. Create `AUTO_REVIEWER_YNGWALD` and `CLAUDE_CODE_OAUTH_TOKEN`, with separate token
   values for each repository.
3. Update the caller to read `secrets.AUTO_REVIEWER_YNGWALD`, as shown below.
4. Delete the old repository secret `YNGWALD_PR_TOKEN` after all workflows in that
   repository use the new secret name.

For `v0.0.3`, bind the input directly to the repository secret of the same name:
`AUTO_REVIEWER_YNGWALD: ${{ secrets.AUTO_REVIEWER_YNGWALD }}`.

When migrating from `v0.0.1` or `v0.0.2`, update both the caller's version pin and the
left-side input key. Those immutable releases retain `YNGWALD_PR_TOKEN` as their input;
updating only the key while still calling either release fails workflow validation.
Consumers using those releases map the legacy input to `secrets.AUTO_REVIEWER_YNGWALD`;
they do not need a repository secret named `YNGWALD_PR_TOKEN`.

CI runs on branch pushes, pull requests, and manual dispatch. Tag pushes do not trigger it.

The [transport contract](SHARED_AUTO_REVIEWER_TRANSPORT.md) defines the review draft, ledger,
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
    uses: sentimony/github-workflows/.github/workflows/shared-auto-reviewer.yaml@0123456789012345678901234567890123456789 # v0.0.17
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
    uses: sentimony/github-workflows/.github/workflows/shared-auto-reviewer.yaml@0123456789012345678901234567890123456789 # v0.0.17
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
    uses: sentimony/github-workflows/.github/workflows/shared-auto-reviewer.yaml@0123456789012345678901234567890123456789 # v0.0.17
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
`actionlint .github/workflows/*.yaml .github/workflows/*.yml`. The live skl-x install
test needs the network and runs only with `AUTO_REVIEWER_LIVE=1`. To validate a caller
against this checkout, run:

```sh
python3 scripts/check_shared_auto_reviewer_contract.py --workflow .github/workflows/shared-auto-reviewer.yaml --caller path/to/caller.yaml --required-ci-job validate
```

Use `check` for the SoundCloud caller; repeat `--required-ci-job` to require several jobs. The checker needs Ruby 3 or later; set `RUBY` to
its executable when needed. It reads the callee schema and job permissions. It does not
verify the caller's `uses` target, ref, or pin; verify those separately. It also does not
contact GitHub, inspect secret expression internals, or verify repository secret
availability and runtime values.
