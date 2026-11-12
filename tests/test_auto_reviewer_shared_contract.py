import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.check_auto_reviewer_shared_contract import load

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / 'scripts/check_auto_reviewer_shared_contract.py'
WORKFLOW = ROOT / '.github/workflows/auto-reviewer-shared.yaml'
CALLER = '''name: Auto Reviewer
on: [workflow_dispatch]
permissions:
  actions: read
  contents: write
  pull-requests: write
  issues: read
  statuses: write
jobs:
  review:
    uses: sentimony/github-workflows/.github/workflows/auto-reviewer.yaml@0123456789012345678901234567890123456789
    with:
      required-ci-job: validate
    secrets:
      AUTO_REVIEWER_YNGWALD: ${{ secrets.AUTO_REVIEWER_YNGWALD }}
      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
'''

class ContractTest(unittest.TestCase):
    def check(self, workflow=None, caller=CALLER, expected='validate'):
        expected = [expected] if isinstance(expected, str) else expected
        with tempfile.TemporaryDirectory() as directory:
            central = Path(directory) / 'central.yaml'
            adapter = Path(directory) / 'caller.yaml'
            central.write_text(workflow if workflow is not None else WORKFLOW.read_text())
            adapter.write_text(caller)
            env = os.environ.copy()
            env.pop('GEM_HOME', None)
            env.pop('GEM_PATH', None)
            return subprocess.run(['python3', str(CHECKER), '--workflow', str(central), '--caller', str(adapter), *[x for name in expected for x in ('--required-ci-job', name)]], capture_output=True, text=True, env=env)

    def test_source_rejects_legacy_pr_number_input(self):
        caller = CALLER.replace('      required-ci-job:', "      pr_number: '42'\n      required-ci-job:")
        result = self.check(caller=caller)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unknown caller input', result.stderr)

    def test_valid_caller_is_accepted(self):
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_caller_forms(self):
        token = CALLER.replace('AUTO_REVIEWER_YNGWALD: ${{ secrets.AUTO_REVIEWER_YNGWALD }}',
                               'AUTO_REVIEWER_TOKEN: ${{ secrets.SO_UPSTARS_PR_TOKEN }}')
        self.assertEqual(self.check(caller=token).returncode, 0)
        both = CALLER.replace('    secrets:', '    secrets:\n      AUTO_REVIEWER_TOKEN: ${{ secrets.X }}')
        self.assertEqual(self.check(caller=both).returncode, 0)
        jobs = CALLER.replace('required-ci-job: validate', 'required-ci-jobs: |\n        Lint\n        Unit tests\n      reviewer: so-upstars\n      review-changed-head: true')
        self.assertEqual(self.check(caller=jobs, expected=['Lint', 'Unit tests']).returncode, 0)
        comma = CALLER.replace('required-ci-job: validate', 'required-ci-jobs: Lint, Unit tests')
        self.assertEqual(self.check(caller=comma, expected=['Unit tests']).returncode, 0)
        missing = self.check(caller=comma, expected=['Docs'])
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn('incorrect required CI job', missing.stderr)
        bad = self.check(caller=CALLER.replace('validate', 'bad;job'), expected='bad;job')
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn('invalid CI job', bad.stderr)
        no_secret = self.check(caller=CALLER.replace('      AUTO_REVIEWER_YNGWALD: ${{ secrets.AUTO_REVIEWER_YNGWALD }}\n', ''))
        self.assertNotEqual(no_secret.returncode, 0)
        self.assertIn('missing required secret', no_secret.stderr)

    def test_source_rejects_legacy_secret_input(self):
        legacy = CALLER.replace('      AUTO_REVIEWER_YNGWALD:', '      YNGWALD_PR_TOKEN:')
        both = CALLER.replace('    secrets:', '    secrets:\n      YNGWALD_PR_TOKEN: legacy')
        for caller in (legacy, both):
            with self.subTest(caller=caller):
                result = self.check(caller=caller)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('unknown caller secret', result.stderr)
        self.assertNotIn('YNGWALD_PR_TOKEN', WORKFLOW.read_text())

    def test_self_caller_matches_callee_contract(self):
        path = ROOT / '.github/workflows/auto-reviewer.yaml'
        caller = path.read_text()
        result = self.check(caller=caller)
        self.assertEqual(result.returncode, 0, result.stderr)
        workflow = load(str(path))
        self.assertEqual(workflow['name'], 'Auto Reviewer')
        self.assertNotIn('concurrency', workflow)
        self.assertEqual(set(workflow['jobs']), {'auto_review'})
        job = workflow['jobs']['auto_review']
        self.assertEqual(job['uses'],
                         'sentimony/github-workflows/.github/workflows/auto-reviewer-shared.yaml@v0.0.7')
        self.assertNotIn('concurrency', job)
        self.assertEqual(job['secrets']['AUTO_REVIEWER_YNGWALD'],
                         '${{ secrets.AUTO_REVIEWER_YNGWALD }}')
        self.assertEqual(job['secrets']['CLAUDE_CODE_OAUTH_TOKEN'],
                         '${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}')

        ci = load(str(ROOT / '.github/workflows/ci.yml'))
        events = ci.get('on', ci.get('true'))
        self.assertEqual(set(events), {'push', 'pull_request', 'workflow_dispatch'})
        self.assertEqual(events['push'], {'branches': ['**']})

    def test_contract_mutations_fail(self):
        original = WORKFLOW.read_text()
        cases = {
            'renamed input': (original.replace('      required-ci-job:', '      renamed-ci-job:', 1), CALLER),
            'renamed secret': (original.replace('      AUTO_REVIEWER_YNGWALD:', '      RENAMED_TOKEN:', 1), CALLER),
            'missing input': (original, CALLER.replace('      required-ci-job: validate\n', "      pr: ''\n")),
            'wrong job': (original, CALLER.replace('required-ci-job: validate', 'required-ci-job: other')),
            'wrong type': (original, CALLER.replace('required-ci-job: validate', 'required-ci-job: 123')),
            'unknown input': (original, CALLER.replace('      required-ci-job:', '      typo: value\n      required-ci-job:')),
            'missing secret': (original, CALLER.replace('      AUTO_REVIEWER_YNGWALD: ${{ secrets.AUTO_REVIEWER_YNGWALD }}\n', '')),
            'unknown secret': (original, CALLER.replace('    secrets:', '    secrets:\n      EXTRA: value')),
            'inherit': (original, CALLER[0:CALLER.index('    secrets:')] + '    secrets: inherit\n'),
            'missing status permission': (original, CALLER.replace('  statuses: write\n', '')),
            'new callee permission': (original.replace('      actions: read\n', '      id-token: write\n      actions: read\n', 1), CALLER),
        }
        expected_errors = {
            'renamed input': 'unknown caller input', 'renamed secret': 'unknown caller secret',
            'missing input': 'missing required CI job', 'wrong job': 'incorrect required CI job',
            'wrong type': 'invalid caller input type',
            'unknown input': 'unknown caller input', 'missing secret': 'missing required secret',
            'unknown secret': 'unknown caller secret', 'inherit': 'caller secrets must be explicit',
            'missing status permission': 'insufficient caller permissions',
            'new callee permission': 'insufficient caller permissions',
        }
        for name, (workflow, caller) in cases.items():
            with self.subTest(name=name):
                result = self.check(workflow, caller)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected_errors[name], result.stderr)

    def test_callee_schema_changes_are_enforced(self):
        original = WORKFLOW.read_text()
        cases = (
            (original.replace("type: string\n        default: ''", 'type: number\n        default: 1', 1), CALLER, 'invalid caller input type'),
            (original.replace("default: ''", 'default: 123', 1), CALLER, 'invalid input default'),
            (original.replace('      pr:\n        description: PR number\n        required: false',
                              '      pr:\n        description: PR number\n        required: true', 1),
             CALLER, 'missing required input'),
        )
        for workflow, caller, error in cases:
            with self.subTest(error=error):
                result = self.check(workflow, caller)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)

    def test_permission_ceiling_levels_and_callee_inheritance(self):
        block = 'permissions:\n  actions: read\n  contents: write\n  pull-requests: write\n  issues: read\n  statuses: write\n'
        for replacement, succeeds in (('permissions: {}\n', False), ('', False),
                                      ('permissions: read-all\n', False),
                                      ('permissions: write-all\n', True)):
            with self.subTest(replacement=replacement):
                result = self.check(caller=CALLER.replace(block, replacement))
                self.assertEqual(result.returncode == 0, succeeds, result.stderr)
        denied = self.check(caller=CALLER.replace('  statuses: write', '  statuses: none'))
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn('insufficient caller permissions', denied.stderr)
        allowed = self.check(caller=CALLER.replace('  statuses: write',
                                                  '  statuses: write\n  id-token: none'))
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        original = WORKFLOW.read_text()
        inherited = original.replace('permissions: {}\n', 'permissions:\n  id-token: write\n', 1)
        inherited += '\n  extra_inherited_job:\n    runs-on: ubuntu-latest\n    steps: []\n'
        result = self.check(workflow=inherited)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('insufficient caller permissions', result.stderr)

    def test_job_permission_override_replaces_workflow_permissions(self):
        caller = CALLER.replace('    uses:', '    permissions:\n      actions: read\n      contents: write\n      pull-requests: write\n      issues: read\n      statuses: write\n    uses:')
        self.assertEqual(self.check(caller=caller.replace('  statuses: write\n', '', 1)).returncode, 0)
        self.assertNotEqual(self.check(caller=caller.replace('      statuses: write\n', '')).returncode, 0)

    def test_readme_examples_match_actual_callee(self):
        import re
        readme = (ROOT / 'README.md').read_text()
        examples = re.findall(r'```yaml\n(.*?)\n```', readme, re.S)
        self.assertEqual(len(examples), 3)
        for example, expected in zip(examples, (['validate'], ['check'], ['Lint', 'Unit tests'])):
            with self.subTest(expected=expected):
                tag, secret = (('v0.0.6', 'AUTO_REVIEWER_TOKEN: ${{ secrets.SO_UPSTARS_PR_TOKEN }}')
                               if 'Unit tests' in expected else
                               ('v0.0.4', 'AUTO_REVIEWER_YNGWALD: ${{ secrets.AUTO_REVIEWER_YNGWALD }}'))
                self.assertIn(f'uses: sentimony/github-workflows/.github/workflows/auto-reviewer-shared.yaml@{tag}\n', example + '\n')
                self.assertIn(secret + '\n', example + '\n')
                result = self.check(caller=example, expected=expected)
                self.assertEqual(result.returncode, 0, result.stderr)
