"""Run the real Tag versions step against a local bare origin."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/tag-versions.yml'


def load():
    ruby = os.environ.get('RUBY', 'ruby')
    script = "require 'yaml'; require 'json'; puts JSON.generate(YAML.load_file(ARGV[0]))"
    return json.loads(subprocess.check_output([ruby, '-e', script, str(WORKFLOW)], text=True))


class TagVersionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = load()
        cls.script = next(s['run'] for s in cls.workflow['jobs']['tag']['steps'] if s.get('name') == 'Tag versions')

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

    def commit(self, changelog=None, push=True):
        if changelog is not None:
            (self.clone / 'CHANGELOG.md').write_text(changelog)
            self.git('add', 'CHANGELOG.md')
        self.git('commit', '-q', '--allow-empty', '-m', 'change')
        if push:
            self.git('push', '-q', 'origin', 'main')
        return self.git('rev-parse', 'HEAD')

    def tag_versions(self, candidate=''):
        self.git('fetch', '-q', 'origin')
        return subprocess.run(['bash', '--noprofile', '--norc', '-eo', 'pipefail', '-c', self.script], cwd=self.clone,
                              env={**self.env, 'CANDIDATE': candidate}, capture_output=True, text=True)

    def remote_tags(self):
        out = self.git('ls-remote', '--tags', 'origin', 'refs/tags/v*')
        return {line.split('\t')[1][len('refs/tags/'):]: line.split('\t')[0]
                for line in out.splitlines() if not line.endswith('^{}')}

    def target(self, name):
        return self.git('rev-parse', f'{name}^{{commit}}')

    def test_each_version_is_tagged_on_the_first_commit_with_its_heading(self):
        first = self.commit('# Changelog\n\n## v0.0.1 - 2026-11-15\n')
        self.commit()
        second = self.commit('# Changelog\n\n## v0.0.2\n\n## v0.0.1 - 2026-11-15\n')
        # A later edit of an old heading does not move its tag.
        self.commit('# Changelog\n\n## v0.0.2\n\nMore.\n\n## v0.0.1 - 2026-11-16\n')
        # Two versions squashed into main before the next run.
        both = self.commit('## v0.0.4\n\n## v0.0.3\n\n## v0.0.2\n\n## v0.0.1\n')
        tip = self.commit()
        result = self.tag_versions(tip)
        self.assertEqual(result.returncode, 0, result.stderr)
        tags = self.remote_tags()
        self.assertEqual(set(tags), {'v0.0.1', 'v0.0.2', 'v0.0.3', 'v0.0.4'})
        self.assertEqual([self.target(n) for n in ('v0.0.1', 'v0.0.2', 'v0.0.3', 'v0.0.4')],
                         [first, second, both, both])
        self.assertIn('tag v0.0.3 ->', result.stdout)

    def test_tags_are_annotated_with_the_version_as_message(self):
        sha = self.commit('## v0.0.1\n')
        self.assertEqual(self.tag_versions(sha).returncode, 0)
        self.assertEqual(self.git('cat-file', '-t', 'v0.0.1'), 'tag')
        self.assertEqual(self.git('tag', '-l', '--format=%(contents)', 'v0.0.1').strip(), 'v0.0.1')

    def test_existing_tags_are_never_moved_and_a_rerun_changes_nothing(self):
        first = self.commit('## v0.0.1\n')
        second = self.commit('## v0.0.2\n\n## v0.0.1\n')
        # v0.0.1 sits on another commit (a release commit, say): it stays there.
        self.git('tag', '-a', '-m', 'v0.0.1', 'v0.0.1', second)
        self.git('push', '-q', 'origin', 'refs/tags/v0.0.1')
        self.git('tag', '-d', 'v0.0.1')
        tip = self.commit()
        result = self.tag_versions(tip)
        self.assertEqual(result.returncode, 0, result.stderr)
        tags = self.remote_tags()
        self.assertEqual(self.git('ls-remote', 'origin', 'refs/tags/v0.0.1^{}').split('\t')[0], second)
        self.assertNotEqual(first, second)
        self.assertEqual(set(tags), {'v0.0.1', 'v0.0.2'})
        before = dict(tags)
        result = self.tag_versions(tip)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.remote_tags(), before)
        self.assertIn('No untagged version', result.stdout)

    def test_only_history_up_to_the_candidate_is_tagged(self):
        first = self.commit('## v0.0.1\n')
        self.commit('## v0.0.2\n\n## v0.0.1\n')
        self.assertEqual(self.tag_versions(first).returncode, 0)
        self.assertEqual(set(self.remote_tags()), {'v0.0.1'})

    def test_empty_candidate_is_the_tip_of_main(self):
        self.commit('## v0.0.1\n')
        self.commit('## v0.0.2\n\n## v0.0.1\n')
        self.assertEqual(self.tag_versions().returncode, 0)
        self.assertEqual(set(self.remote_tags()), {'v0.0.1', 'v0.0.2'})

    def test_only_version_headings_count(self):
        sha = self.commit('# Changelog\n\n## Unreleased\n\n### v0.0.9\n\nText mentioning ## v0.0.8\n\n## v0.0.1 - x\n')
        result = self.tag_versions(sha)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(self.remote_tags()), {'v0.0.1'})

    def test_no_changelog_changes_nothing(self):
        sha = self.commit()
        result = self.tag_versions(sha)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.remote_tags(), {})

    def test_rejected_candidates_leave_tags(self):
        first = self.commit('## v0.0.1\n')
        outside = self.commit('## v0.0.9\n## v0.0.1\n', push=False)
        self.git('reset', '-q', '--hard', 'origin/main')
        for name, candidate in [('short sha', first[:12]), ('ref name', 'main'), ('unknown', 'f' * 40),
                                ('not in main', outside)]:
            with self.subTest(name=name):
                result = self.tag_versions(candidate)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('::error::', result.stdout)
                self.assertEqual(self.remote_tags(), {})

    def test_remote_tag_created_meanwhile_is_not_moved(self):
        first = self.commit('## v0.0.1\n')
        # Another run tagged v0.0.1 elsewhere after this clone fetched: the plain push is rejected.
        other = self.commit()
        self.git('tag', '-a', '-m', 'v0.0.1', 'v0.0.1', other)
        self.git('push', '-q', 'origin', 'refs/tags/v0.0.1')
        self.git('tag', '-d', 'v0.0.1')
        result = self.tag_versions(first)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git('ls-remote', 'origin', 'refs/tags/v0.0.1^{}').split('\t')[0], other)

    def test_trigger_permissions_and_no_force(self):
        workflow = self.workflow
        triggers = workflow.get('on') or workflow.get('true')
        self.assertEqual(triggers['workflow_run'], {'workflows': ['CI'], 'types': ['completed'], 'branches': ['main']})
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        job = workflow['jobs']['tag']
        for needle in ("github.ref == 'refs/heads/main'", "workflow_run.conclusion == 'success'",
                       "workflow_run.event == 'push'", "workflow_run.head_branch == 'main'",
                       'workflow_run.head_repository.full_name == github.repository'):
            self.assertIn(needle, job['if'])
        checkout = job['steps'][0]
        self.assertRegex(checkout['uses'], r'\Aactions/checkout@[0-9a-f]{40}\Z')
        self.assertEqual(checkout['with'], {'ref': 'main', 'fetch-depth': 0,
                                            'ssh-key': '${{ secrets.STABLE_DEPLOY_KEY }}'})
        for forbidden in ('--force', ' -f ', 'tag -d', 'tag -f', '--delete', ' :refs/'):
            self.assertNotIn(forbidden, self.script)


if __name__ == '__main__':
    unittest.main()
