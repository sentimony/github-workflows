"""Run the real Promote stable step against a local bare origin."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/promote-stable.yml'


def load():
    ruby = os.environ.get('RUBY', 'ruby')
    script = "require 'yaml'; require 'json'; puts JSON.generate(YAML.load_file(ARGV[0]))"
    return json.loads(subprocess.check_output([ruby, '-e', script, str(WORKFLOW)], text=True))


class PromoteStableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = load()
        cls.script = next(s['run'] for s in cls.workflow['jobs']['promote']['steps'] if s.get('name') == 'Promote stable')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.origin = root / 'origin.git'
        self.clone = root / 'clone'
        self.env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
                    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.com',
                    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.com'}
        self.env.pop('GITHUB_STEP_SUMMARY', None)
        self.git('init', '-q', '--bare', '-b', 'main', str(self.origin), cwd=root)
        self.git('clone', '-q', str(self.origin), str(self.clone), cwd=root)
        self.git('checkout', '-q', '-b', 'main')

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd or self.clone, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def release(self, changelog, push=True):
        (self.clone / 'CHANGELOG.md').write_text(changelog)
        self.git('add', 'CHANGELOG.md')
        self.git('commit', '-q', '--allow-empty', '-m', 'release')
        if push:
            self.git('push', '-q', 'origin', 'main')
        return self.git('rev-parse', 'HEAD')

    def promote(self, candidate=''):
        self.git('fetch', '-q', 'origin')
        return subprocess.run(['bash', '--noprofile', '--norc', '-eo', 'pipefail', '-c', self.script], cwd=self.clone,
                              env={**self.env, 'CANDIDATE': candidate}, capture_output=True, text=True)

    def stable(self):
        return self.git('ls-remote', 'origin', 'refs/heads/stable').split('\t')[0]

    def test_first_run_creates_stable_and_a_new_version_moves_it(self):
        first = self.release('# Changelog\n\nIntro.\n\n## v0.0.1 - 2026-11-15\n')
        result = self.promote(first)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.stable(), first)
        self.assertIn('stable: none -> v0.0.1', result.stdout)
        second = self.release('# Changelog\n\n## v0.0.2\n\n## v0.0.1 - 2026-11-15\n')
        result = self.promote(second)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.stable(), second)
        self.assertIn('stable: v0.0.1 -> v0.0.2', result.stdout)

    def test_empty_candidate_is_the_tip_of_main(self):
        tip = self.release('## v0.0.1 - 2026-11-15\n')
        self.assertEqual(self.promote().returncode, 0)
        self.assertEqual(self.stable(), tip)

    def test_commit_without_a_new_version_leaves_stable(self):
        first = self.release('## v0.0.1 - 2026-11-15\n')
        self.assertEqual(self.promote(first).returncode, 0)
        same = self.release('## v0.0.1 - 2026-11-15\n\nMore.\n')
        for candidate in (same, first):
            with self.subTest(candidate=candidate):
                result = self.promote(candidate)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.stable(), first)

    def test_older_commit_never_moves_stable_back(self):
        first = self.release('## v0.0.1\n')
        second = self.release('## v0.0.2\n')
        self.assertEqual(self.promote(second).returncode, 0)
        result = self.promote(first)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('already ahead', result.stdout)
        self.assertEqual(self.stable(), second)

    def test_rejected_candidates_leave_stable(self):
        first = self.release('## v0.0.1\n')
        self.assertEqual(self.promote(first).returncode, 0)
        unversioned = self.release('# Changelog\n\n## Unreleased\n')
        outside = self.release('## v0.0.9\n', push=False)
        self.git('reset', '-q', '--hard', 'origin/main')
        for name, candidate in [('short sha', first[:12]), ('ref name', 'main'), ('unknown', 'f' * 40),
                                ('not in main', outside), ('no version heading', unversioned)]:
            with self.subTest(name=name):
                result = self.promote(candidate)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('::error::', result.stdout)
                self.assertEqual(self.stable(), first)

    def test_diverged_stable_is_left_to_an_admin(self):
        first = self.release('## v0.0.1\n')
        self.git('checkout', '-q', '-b', 'side')
        (self.clone / 'CHANGELOG.md').write_text('## v0.0.5\n')
        self.git('commit', '-q', '-am', 'side')
        side = self.git('rev-parse', 'HEAD')
        self.git('push', '-q', 'origin', 'side:refs/heads/stable')
        self.git('checkout', '-q', 'main')
        second = self.release('## v0.0.2\n')
        result = self.promote(second)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('an admin moves it by hand', result.stdout)
        self.assertEqual(self.stable(), side)
        self.assertNotEqual(first, side)

    def test_trigger_and_permissions(self):
        workflow = self.workflow
        triggers = workflow.get('on') or workflow.get('true')
        self.assertEqual(triggers['workflow_run'], {'workflows': ['CI'], 'types': ['completed'], 'branches': ['main']})
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        job = workflow['jobs']['promote']
        for needle in ("github.ref == 'refs/heads/main'", "workflow_run.conclusion == 'success'",
                       "workflow_run.event == 'push'", "workflow_run.head_branch == 'main'",
                       'workflow_run.head_repository.full_name == github.repository'):
            self.assertIn(needle, job['if'])
        checkout = job['steps'][0]
        self.assertRegex(checkout['uses'], r'\Aactions/checkout@[0-9a-f]{40}\Z')
        self.assertEqual(checkout['with'], {'ref': 'main', 'fetch-depth': 0,
                                            'ssh-key': '${{ secrets.STABLE_DEPLOY_KEY }}'})
        self.assertNotIn('--force', self.script)
        self.assertNotIn(' -f ', self.script)


if __name__ == '__main__':
    unittest.main()
