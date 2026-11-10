"""Install cr-request with the real skl-x and compare it with git archive of the same ref.

Skipped unless AUTO_REVIEWER_LIVE=1: it needs the network, npx and git. The skl-x CI
workflow runs it on a schedule and on pull requests that change the install step.
"""
import io
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import unittest

from test_auto_reviewer_shared import step

SOURCE = 'https://github.com/sentimony/agentic-workflow'
# The last tag with the plugins/aw/skills/ layout; installed by its full commit SHA.
OLD_LAYOUT_TAG = 'v0.0.10'


def git(*args, cwd):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True).stdout


def archived_skill(clone, ref):
    """Return {relative path: bytes} of the single cr-request directory at ref."""
    names = git('ls-tree', '-r', '--name-only', ref, cwd=clone).decode().splitlines()
    roots = [n[:-len('/SKILL.md')] for n in names
             if re.fullmatch(r'(.+/)?skills/cr-request/SKILL\.md', n)]
    if len(roots) != 1:
        raise AssertionError(f'{ref}: expected one cr-request skill, found {roots}')
    root = roots[0]
    files = {}
    with tarfile.open(fileobj=io.BytesIO(git('archive', ref, root, cwd=clone))) as tar:
        for member in tar.getmembers():
            if member.issym() or member.islnk():
                raise AssertionError(f'{ref}: link in the archive: {member.name}')
            if member.isfile():
                files[member.name[len(root) + 1:]] = tar.extractfile(member).read()
    return root, files


def installed_skill(path):
    files = {}
    for item in sorted(path.rglob('*')):
        if item.is_symlink():
            raise AssertionError(f'link in the installed skill: {item}')
        if item.is_file():
            files[item.relative_to(path).as_posix()] = item.read_bytes()
    return files


@unittest.skipUnless(os.environ.get('AUTO_REVIEWER_LIVE') == '1', 'set AUTO_REVIEWER_LIVE=1 to run')
class SklXLiveInstallTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.clone = Path(cls.tmp.name) / 'agentic-workflow'
        subprocess.run(['git', 'clone', '--quiet', '--filter=blob:none', '--no-checkout', SOURCE,
                        str(cls.clone)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def install(self, ref=''):
        """Run the real step with a fresh HOME; return the chosen ref and the installed files."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'home').mkdir()
            env = {**os.environ, 'HOME': str(root / 'home'), 'SOURCE': SOURCE,
                   'REVIEW_SKILLS_REF': ref, 'GITHUB_OUTPUT': str(root / 'output')}
            result = subprocess.run(['bash', '-e', '-c', step('Install cr-request skill')], cwd=root,
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            chosen = re.search(r'^cr-request source: (\S+)$', result.stdout, re.M).group(1)
            self.assertRegex((root / 'output').read_text(), r'^version=[0-9][0-9.]*\n$')
            return chosen, installed_skill(root / 'home/.claude/skills/cr-request')

    def assert_same_as_archive(self, ref, installed):
        root, archived = archived_skill(self.clone, ref)
        self.assertEqual(sorted(installed), sorted(archived), f'{ref} ({root}): file lists differ')
        for name, data in archived.items():
            self.assertEqual(installed[name], data, f'{ref} ({root}): {name} differs')

    def test_newest_release_tag(self):
        chosen, installed = self.install()
        tags = git('tag', '--list', 'v*', cwd=self.clone).decode().split()
        releases = [t for t in tags if re.fullmatch(r'v\d+\.\d+\.\d+', t)]
        newest = max(releases, key=lambda t: tuple(map(int, t[1:].split('.'))))
        self.assertEqual(chosen, newest)
        self.assert_same_as_archive(chosen, installed)

    def test_full_sha_with_plugins_layout(self):
        sha = git('rev-parse', OLD_LAYOUT_TAG + '^{commit}', cwd=self.clone).decode().strip()
        root, _ = archived_skill(self.clone, sha)
        self.assertRegex(root, r'^plugins/[^/]+/skills/cr-request$')
        chosen, installed = self.install(sha)
        self.assertEqual(chosen, sha)
        self.assert_same_as_archive(sha, installed)


if __name__ == '__main__':
    unittest.main()
