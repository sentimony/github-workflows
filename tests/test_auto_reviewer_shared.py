"""Exercise real workflow steps without contacting GitHub."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = {
    'reviewer': 'yngwald',
    'secret': 'AUTO_REVIEWER_YNGWALD',
    'workflow': '.github/workflows/auto-reviewer-shared.yaml',
    'name': 'Auto Reviewer (Shared)',
    'dispatch': 'pr',
    'jobs': ['validate'],
    'events': ['push', 'workflow_dispatch'],
    'skippable_jobs': [],
}
WORKFLOW = ROOT / ADAPTER['workflow']
HELPERS_DIR = tempfile.TemporaryDirectory()


def summary_table(body, first_column):
    lines=body.splitlines()
    index=next(i for i,line in enumerate(lines) if line.startswith('| '+first_column+' |'))
    cells=lambda line: [cell.strip().strip('*') for cell in line.strip('|').split('|')]
    headers=cells(lines[index]); rows=[]
    for line in lines[index+2:]:
        if not line.startswith('|'): break
        rows.append(dict(zip(headers,cells(line))))
    return rows


def summary_result(body, name):
    return summary_table(body,'Critical')[0][name]


def summary_usage(body, row, column):
    return next(item[column] for item in summary_table(body,'Review') if item['Review']==row)


def step(name):
    ruby = os.environ.get('RUBY', 'ruby')
    script = "require 'yaml'; require 'json'; w=YAML.load_file(ARGV[0], aliases: true); s=w['jobs'].values.flat_map{|j| j['steps'] || []}.find{|x| x['name']==ARGV[1]}; abort 'missing step' unless s; puts JSON.generate(s.fetch('run'))"
    source = json.loads(subprocess.check_output([ruby, '-e', script, str(WORKFLOW), name], text=True))
    if 'from auto_reviewer_lib import' in source:
        # The job writes the shared helpers in an earlier step; run that step first.
        source = json.loads(subprocess.check_output([ruby, '-e', script, str(WORKFLOW), 'Write review helpers'], text=True)) + source
    if 'auto_reviewer_lib' in source:
        source = f': "${{RUNNER_TEMP:={HELPERS_DIR.name}}}"\nexport RUNNER_TEMP\n' + source
    return source.replace(ADAPTER['reviewer'], 'so-upstars')


class DispatchCompatibilityTest(unittest.TestCase):
    def test_only_pr_input_feeds_gate_and_concurrency(self):
        workflow = WORKFLOW.read_text()
        self.assertNotIn('inputs.pr_number', workflow)
        self.assertNotIn('description: Legacy PR number', workflow)
        self.assertIn("format('pr-{0}', inputs.pr)", workflow)
        self.assertIn('github.event.pull_request.number || inputs.pr', workflow)
        self.assertIn('Provide a PR number using pr"', workflow)


class ClaudeInstallerTest(unittest.TestCase):
    def setUp(self):
        from scripts.check_auto_reviewer_shared_contract import load
        steps=load(str(WORKFLOW))['jobs']['review']['steps']
        self.assertTrue(any(s.get('name')=='Install latest Claude Code' for s in steps),
                        'The review runner must explicitly install the latest Claude Code')

    def install(self, failures=0, stale=False, missing=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); binary=root/'bin'; binary.mkdir()
            home=root/'home'; home.mkdir()
            cli=home/'.local/bin/claude'; cli.parent.mkdir(parents=True)
            if stale:
                cli.write_text('#!/bin/bash\necho stale\n'); cli.chmod(0o755)
            curl=binary/'curl'
            curl.write_text('''#!/bin/bash
n=0
[ ! -f "$COUNT" ] || n=$(cat "$COUNT")
n=$((n + 1))
echo "$n" > "$COUNT"
[ "$n" -gt "$FAILURES" ] || exit 22
cat <<'INSTALLER'
[ "$1" = latest ] || exit 23
echo "$1" > "$HOME/channel"
[ "$MISSING" != 1 ] || exit 0
mkdir -p "$HOME/.local/bin"
printf '#!/bin/bash\\necho "$1" > "$HOME/version-arg"\\necho test-claude\\n' > "$HOME/.local/bin/claude"
chmod +x "$HOME/.local/bin/claude"
INSTALLER
''')
            curl.chmod(0o755)
            sleep=binary/'sleep'; sleep.write_text('#!/bin/bash\necho "$1" >> "$HOME/sleeps"\n'); sleep.chmod(0o755)
            env={**os.environ,'HOME':str(home),'PATH':str(binary)+os.pathsep+os.environ['PATH'],
                 'COUNT':str(root/'count'),'FAILURES':str(failures),'MISSING':str(int(missing)),
                 'GITHUB_OUTPUT':str(root/'output'),'GITHUB_STEP_SUMMARY':str(root/'summary')}
            result=subprocess.run(['bash','-eo','pipefail','-c',step('Install latest Claude Code')],env=env,capture_output=True,text=True)
            read=lambda p: p.read_text() if p.exists() else ''
            result.summary=read(root/'summary')
            return result,read(root/'output'),read(home/'channel'),read(home/'version-arg'),read(root/'count'),read(home/'sleeps'),str(cli)

    def test_latest_installer_and_executable_output(self):
        result,output,channel,version,count,sleeps,cli=self.install()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.summary,'Claude Code installed from native latest: `test-claude`\n')
        self.assertEqual(channel,'latest\n')
        self.assertEqual(version,'--version\n')
        self.assertEqual(output,'path='+cli+'\n')
        self.assertEqual(count,'1\n')
        self.assertEqual(sleeps,'')

    def test_curl_failure_retries_with_pipefail(self):
        result,output,channel,version,count,sleeps,cli=self.install(failures=2)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(output,'path='+cli+'\n')
        self.assertEqual(count,'3\n')
        self.assertEqual(sleeps,'5\n5\n')

    def test_three_failed_attempts_never_use_stale_cli(self):
        for stale in (False,True):
            with self.subTest(stale=stale):
                result,output,channel,version,count,sleeps,cli=self.install(failures=3,stale=stale)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(output,'')
                self.assertEqual(version,'')
                self.assertEqual(count,'3\n')
                self.assertEqual(sleeps,'5\n5\n')

    def test_missing_executable_fails_without_output(self):
        result,output,*_=self.install(missing=True)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(output,'')

    def test_action_uses_installed_cli_before_duration_timer(self):
        from scripts.check_auto_reviewer_shared_contract import load
        steps=load(str(WORKFLOW))['jobs']['review']['steps']
        installer=next(s for s in steps if s.get('id')=='claude_cli')
        agent=next(s for s in steps if s.get('id')=='claude')
        timer=next(s for s in steps if s.get('id')=='start')
        self.assertEqual(installer['shell'],'bash')
        self.assertNotIn('env',installer)
        self.assertLess(steps.index(installer),steps.index(timer))
        self.assertEqual(steps.index(timer)+1,steps.index(agent))
        self.assertEqual(agent['uses'],'anthropics/claude-code-action@925afb53776cf1a2b738c6bdbed37b4d4000ef13')
        self.assertEqual(agent['with']['path_to_claude_code_executable'],'${{ steps.claude_cli.outputs.path }}')


class TransportTest(unittest.TestCase):
    def test_prompt_tool_permissions_and_artifact_paths(self):
        ruby=os.environ.get('RUBY','ruby')
        command="require 'yaml'; require 'json'; puts JSON.generate(YAML.load_file(ARGV[0], aliases: true))"
        workflow=json.loads(subprocess.check_output([ruby,'-e',command,str(WORKFLOW)],text=True))
        review=workflow['jobs']['review']
        agent=next(s for s in review['steps'] if s.get('id')=='claude')
        settings=agent['with']
        self.assertIn('review-actions.json',settings['prompt'])
        for key in ('schema_version','review_source_id','previous_findings','defer_decision'):
            self.assertIn(key,settings['prompt'])
        for name in ('Edit','Write'):
            self.assertIn(name+'(./.auto-review/review-actions.json)',settings['claude_args'])
        self.assertIn('--disallowedTools "Bash,',settings['claude_args'])
        upload=next(s for s in review['steps'] if str(s.get('uses','')).startswith('actions/upload-artifact@'))
        self.assertIn('.auto-review/review-actions.json',upload['with']['path'])
        self.assertEqual(workflow['jobs']['publish']['env']['ACTIONS_FILE'],'review-out/review-actions.json')
        self.assertFalse(any(str(s.get('uses','')).startswith('actions/checkout@') for s in workflow['jobs']['publish']['steps']))

class ResolverBoundaryTest(unittest.TestCase):
    def test_isolated_job_permissions_and_transport(self):
        ruby=os.environ.get('RUBY','ruby')
        script="require 'yaml'; require 'json'; puts JSON.generate(YAML.load_file(ARGV[0], aliases: true))"
        jobs=json.loads(subprocess.check_output([ruby,'-e',script,str(WORKFLOW)],text=True))['jobs']
        self.assertEqual([name for name,j in jobs.items() if j.get('permissions',{}).get('contents')=='write'], ['resolve_threads'])
        resolver=jobs['resolve_threads']
        self.assertEqual(resolver['permissions'],{'contents':'write','pull-requests':'write'})
        self.assertEqual(jobs['publish']['permissions'],{'pull-requests':'read','statuses':'write'})
        self.assertEqual(jobs['complete']['permissions'],jobs['publish']['permissions'])
        encoded=json.dumps(resolver)
        for forbidden in ('secrets.', 'actions/checkout', 'download-artifact', 'review-actions.json', 'claude-review-ledger', 'SO_UPSTARS'):
            self.assertNotIn(forbidden, encoded)
        self.assertIn('resolveReviewThread',encoded)
        self.assertNotIn('resolveReviewThread',step('Publish review actions'))
        self.assertIn('resolve_threads',jobs['complete']['needs'])
        # Footer/ledger fixtures exercise the same script in both trusted phases.
        self.assertEqual(step('Publish review actions'),step('Complete review actions'))
        # Both jobs report a failure that left the status pending the same way.
        self.assertEqual(step('Finalize failed publication'),step('Report an unexpected failure'))
        self.assertIn('Publishing the review failed',step('Report an unexpected failure'))

    def test_helper_users_write_the_helpers_first(self):
        ruby=os.environ.get('RUBY','ruby')
        jobs=json.loads(subprocess.check_output([ruby,'-e',"require 'yaml'; require 'json'; puts JSON.generate(YAML.load_file(ARGV[0], aliases: true))",str(WORKFLOW)],text=True))['jobs']
        users=set()
        for name,job in jobs.items():
            steps=job.get('steps',[])
            importing=[i for i,s in enumerate(steps) if 'from auto_reviewer_lib import' in s.get('run','')]
            if not importing: continue
            users.add(name)
            writers=[i for i,s in enumerate(steps) if s.get('name')=='Write review helpers']
            self.assertEqual(len(writers),1,name)
            self.assertLess(writers[0],importing[0],name)
        self.assertEqual(users,{'merged_summary','publish','complete','refresh_merged_summary'})
        self.assertNotIn('auto_reviewer_lib',json.dumps(jobs['resolve_threads']))


class PrepareThreadsTest(unittest.TestCase):
    def test_both_connection_levels_are_paginated(self):
        run = step('Prepare review threads')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.auto-review/target').mkdir(parents=True)
            fake = root / 'gh'
            fake.write_text('''#!/usr/bin/env python3
import json, sys
args = sys.argv
query = args[args.index('-f') + 1]
def val(prefix):
    return next((s.split('=', 1)[1] for s in args if s.startswith(prefix + '=')), None)
def comment(i):
    return {'databaseId': i, 'author': {'login':'host'}, 'authorAssociation':'MEMBER', 'body': 'proposal', 'createdAt':'2026-01-01T00:00:00Z', 'updatedAt':'2026-01-01T00:00:00Z', 'pullRequestReview': {'databaseId': 101}}
def conn(nodes, more=False, cursor=None): return {'nodes': nodes, 'pageInfo': {'hasNextPage':more, 'endCursor':cursor}}
if 'node(id:' in query:
    result = {'data': {'node': {'comments': conn([comment(2)])}}}
elif val('cursor') == 'threads-2':
    result = {'data': {'repository': {'pullRequest': {'reviewThreads': conn([{'id':'t2','path':'b.py','isResolved':False,'comments':conn([comment(3)])}])}}}}
else:
    result = {'data': {'repository': {'pullRequest': {'reviewThreads': conn([{'id':'t1','path':'a.py','isResolved':False,'comments':conn([comment(1)],True,'comments-2')}], True, 'threads-2')}}}}
print(json.dumps(result))
''')
            fake.chmod(0o755)
            env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'], 'GITHUB_REPOSITORY':'owner/repo', 'NUMBER':'42', 'GITHUB_OUTPUT':str(root/'output')}
            subprocess.run(['bash', '-eo', 'pipefail', '-c', run], cwd=root, env=env, check=True)
            threads = json.loads((root / '.auto-review/target/threads.json').read_text())
            self.assertEqual([t['thread_id'] for t in threads], ['t1', 't2'])
            self.assertEqual([c['databaseId'] for c in threads[0]['comments']], [1, 2])
            self.assertEqual(threads[0]['comments'][1]['authorAssociation'], 'MEMBER')



class DraftTest(unittest.TestCase):
    def run_draft(self, change=None):
        run = step('Validate review actions')
        draft = {'schema_version':1, 'repository':'owner/repo', 'pr_number':42,
                 'head_sha':'a'*40, 'review_source_id':None,
                 'findings':[{'origin_review_id':None, 'original_id':'R2-F1', 'path':'src/a.py', 'severity':'Minor'}],
                 'previous_findings':[]}
        if change: change(draft)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'actions.json').write_text(json.dumps(draft))
            low=r'$\color{green}◎$ Low'
            (root/'body.md').write_text('| Intent | System | Craft | Critical | Important | Minor |\n| --- | --- | --- | --- | --- | --- |\n| PASS | PASS | FINDINGS | 0 | 0 | 1 |\n- '+low+' R2-F1 [`src/a.py:1`](url) issue\n')
            (root/'comments.json').write_text(json.dumps([{'path':'src/a.py','line':1,'body':low+' R2-F1 - issue'}]))
            env = {**os.environ, 'REPO':'owner/repo', 'NUMBER':'42', 'HEAD_SHA':'a'*40,
                   'ACTIONS_FILE':str(root/'actions.json'), 'BODY_FILE':str(root/'body.md'), 'COMMENTS_FILE':str(root/'comments.json')}
            return subprocess.run(['bash','-eo','pipefail','-c',run], env=env, capture_output=True, text=True)

    def run_badge_draft(self, body_line, comment_body, severity='Minor'):
        run = step('Validate review actions')
        draft = {'schema_version':1, 'repository':'owner/repo', 'pr_number':42,
                 'head_sha':'a'*40, 'review_source_id':None,
                 'findings':[{'origin_review_id':None, 'original_id':'R2-F1', 'path':'src/a.py', 'severity':severity}],
                 'previous_findings':[]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'actions.json').write_text(json.dumps(draft))
            counts = [int(severity == name) for name in ('Critical', 'Important', 'Minor')]
            (root/'body.md').write_text('| Intent | System | Craft | Critical | Important | Minor |\n| --- | --- | --- | --- | --- | --- |\n| PASS | PASS | FINDINGS | '+' | '.join(map(str,counts))+' |\n'+body_line+'\n')
            (root/'comments.json').write_text(json.dumps([{'path':'src/a.py','line':1,'body':comment_body}]))
            env = {**os.environ, 'REPO':'owner/repo', 'NUMBER':'42', 'HEAD_SHA':'a'*40,
                   'ACTIONS_FILE':str(root/'actions.json'), 'BODY_FILE':str(root/'body.md'), 'COMMENTS_FILE':str(root/'comments.json')}
            return subprocess.run(['bash','-eo','pipefail','-c',run], env=env, capture_output=True, text=True)

    def test_colored_circle_matches_severity_in_report_and_comment(self):
        forms = [('Critical',r'$\color{red}◎$ High'),
                 ('Important',r'$\color{orange}◎$ Medium'),
                 ('Minor',r'$\color{green}◎$ Low')]
        for severity, circle in forms:
            with self.subTest(severity=severity):
                line = '- '+circle+' R2-F1 [`src/a.py:1`](url) issue'
                self.assertEqual(self.run_badge_draft(line, circle+' R2-F1 - issue', severity).returncode, 0)
                wrong = forms[(forms.index((severity,circle))+1) % len(forms)][1]
                self.assertNotEqual(self.run_badge_draft(line, wrong+' R2-F1 - issue', severity).returncode, 0)

    def test_legacy_severity_forms_are_rejected_in_new_drafts(self):
        circle=r'$\color{green}◎$ Low'
        for legacy in (r'$\color{green}{\bigcirc}$ Low', '**Minor**', '![Minor](https://img.shields.io/badge/Low-539BF5?style=flat-square)',
                       '![Minor](https://img.shields.io/badge/Low-539BF5?style=for-the-badge)'):
            with self.subTest(legacy=legacy):
                self.assertNotEqual(self.run_badge_draft('- '+legacy+' R2-F1 [`src/a.py:1`](url) issue', circle+' R2-F1 - issue').returncode, 0)
                self.assertNotEqual(self.run_badge_draft('- '+circle+' R2-F1 [`src/a.py:1`](url) issue', legacy+' R2-F1 - issue').returncode, 0)

    def test_missing_legacy_draft_is_explicit_gap(self):
        run=step('Validate review actions')
        with tempfile.TemporaryDirectory() as tmp:
            env={**os.environ,'ACTIONS_FILE':str(Path(tmp)/'missing')}
            p=subprocess.run(['bash','-eo','pipefail','-c',run],env=env,capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertIn('evidence gaps',p.stdout)

    def test_current_null_identity_and_report_match(self):
        self.assertEqual(self.run_draft().returncode, 0)

    def test_boolean_version_rejected(self):
        self.assertNotEqual(self.run_draft(lambda d: d.update(schema_version=True)).returncode, 0)

    def test_unknown_fields_rejected(self):
        self.assertNotEqual(self.run_draft(lambda d: d.update(extra='x')).returncode, 0)

    def test_mismatched_inline_identity_rejected(self):
        self.assertNotEqual(self.run_draft(lambda d: d['findings'][0].update(original_id='R2-F2')).returncode, 0)

    def test_bool_number_and_path_traversal_rejected(self):
        self.assertNotEqual(self.run_draft(lambda d: d.update(pr_number=True)).returncode,0)
        self.assertNotEqual(self.run_draft(lambda d: d['findings'][0].update(path='../secret')).returncode,0)

    def test_critical_defer_rejected(self):
        finding={'origin_review_id':101,'original_id':'R1-F1','path':'a.py','severity':'Critical',
                 'status':'NOT_ADDRESSED','defer_decision':'ACCEPTED','reason':'Later','owner':'alice'}
        self.assertNotEqual(self.run_draft(lambda d: d['previous_findings'].append(finding)).returncode,0)

class LedgerTest(unittest.TestCase):
    def run_ledger(self, signature=None, **override):
        run=step('Publish review actions')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            os.symlink(ROOT/'tests/fixtures/auto-reviewer-shared/gh',root/'gh')
            actions={'schema_version':1,'repository':'owner/repo','pr_number':42,'head_sha':'a'*40,
                     'review_source_id':None,'findings':[{'origin_review_id':None,'original_id':'R2-F1','path':'a.py','severity':'Minor'}],
                     'previous_findings':[]}
            (root/'actions.json').write_text(json.dumps(actions))
            if signature is None:
                signature='Reviewed with: `auto-reviewer.yaml` `v0.0.3` workflow · `cr-request` `v0.0.11` skill · In `1 min 3 sec`'
            (root/'post.md').write_text('<!-- claude-review sha='+('a'*40)+' -->\n\n'+signature+'\nNext step: `/verify`\n')
            env={**os.environ,'PATH':str(root)+os.pathsep+os.environ['PATH'],'FIXTURE_DIR':str(root),
                 'REPO':'owner/repo','NUMBER':'42','HEAD_SHA':'a'*40,'REVIEW_ID':'202','GITHUB_RUN_ID':'303',
                 'REVIEWER':'so-upstars','ACTIONS_FILE':str(root/'actions.json'),'POST_FILE':str(root/'post.md'),**override}
            result=subprocess.run(['bash','-eo','pipefail','-c',run],env=env,capture_output=True,text=True)
            trace=[json.loads(x) for x in (root/'trace.jsonl').read_text().splitlines()]
            body=(root/'body').read_text() if (root/'body').exists() else ''
            return result,trace,body

    def test_split_skill_only_footer_keeps_ledger_and_duration(self):
        result,trace,body=self.run_ledger(signature='Reviewed with: `cr-request` `v0.0.11` skill · In `1 min 3 sec`')
        self.assertEqual(result.returncode,0,result.stderr)
        marker=body.split('<!-- claude-review-ledger:v1 ',1)[1].split(' -->',1)[0]
        self.assertEqual(json.loads(marker)['duration_seconds'],63)
        self.assertIn('Reviewed with: `cr-request` `v0.0.11` skill',body)

    def test_shared_workflow_footer_keeps_ledger_and_duration(self):
        for name in ('auto-reviewer.yaml','auto-reviewer-shared.yaml'):
            for suffix in ('` `v0.0.4`','@v0.0.4`'):
                with self.subTest(name=name,suffix=suffix):
                    signature='Reviewed with: `'+name+suffix+' workflow · `cr-request` `v0.0.11` skill · In `1 min 3 sec`'
                    result,trace,body=self.run_ledger(signature=signature)
                    self.assertEqual(result.returncode,0,result.stderr)
                    marker=body.split('<!-- claude-review-ledger:v1 ',1)[1].split(' -->',1)[0]
                    self.assertEqual(json.loads(marker)['duration_seconds'],63)
                    self.assertLess(body.index('<!-- claude-review-ledger:v1 '),body.index(signature))

    def test_used_footer_preserves_ledger_duration_and_footer_position(self):
        result,trace,body=self.run_ledger(signature=USED_EXAMPLE)
        self.assertEqual(result.returncode,0,result.stderr)
        marker=body.split('<!-- claude-review-ledger:v1 ',1)[1].split(' -->',1)[0]
        self.assertEqual(json.loads(marker)['duration_seconds'],49)
        self.assertLess(body.index('<!-- claude-review-ledger:v1 '),body.index(USED_EXAMPLE))

    def test_canonical_nulls_and_put_readback(self):
        result,trace,body=self.run_ledger()
        self.assertEqual(result.returncode,0,result.stderr)
        marker=body.split('<!-- claude-review-ledger:v1 ',1)[1].split(' -->',1)[0]
        ledger=json.loads(marker)
        self.assertEqual((ledger['review_source_id'],ledger['findings'][0]['origin_review_id'],ledger['duration_seconds']),(202,202,63))
        ops=[(x['method'],x['endpoint']) for x in trace]
        self.assertIn(('PUT','repos/owner/repo/pulls/42/reviews/202'),ops)
        index=ops.index(('PUT','repos/owner/repo/pulls/42/reviews/202'))
        self.assertEqual(ops[index+1],('GET','repos/owner/repo/pulls/42/reviews/202'))

    def test_stale_head_has_no_write(self):
        result,trace,_=self.run_ledger(FIXTURE_HEAD='b'*40)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(any(x['method']!='GET' for x in trace))

    def test_missing_ledger_write_rights_stops_before_actions(self):
        result,trace,_=self.run_ledger(FAIL_PUT='1')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual([x['method'] for x in trace].count('PUT'),1)
        self.assertFalse(any('/issues' in x['endpoint'] or '/comments' in x['endpoint'] for x in trace))

    def test_failed_readback_fails_closed(self):
        result,trace,_=self.run_ledger(FAIL_READBACK='1')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual([x['method'] for x in trace].count('PUT'),1)
        self.assertFalse(any('/issues' in x['endpoint'] or '/comments' in x['endpoint'] for x in trace))

def pipeline_script():
    """Run the actual jobs with distinct fake credentials and output boundaries."""
    import shlex
    prepare=shlex.quote(step('Publish review actions'))
    resolve=shlex.quote(step('Resolve verified thread metadata'))
    complete=shlex.quote(step('Complete review actions'))
    return f'''
set +e
: > "$GITHUB_OUTPUT"
export GH_TOKEN=fixture-publisher PHASE=PREPARE STAGE=prepare
export REVIEW_THREADS_FILE="$FIXTURE_DIR/threads.json"
export REVIEW_THREADS_DIGEST=$(python3 -c 'import json,os,pathlib,hashlib; p=pathlib.Path(os.environ["REVIEW_THREADS_FILE"]); s=json.loads((p.parent/"state.json").read_text()); p.write_text(json.dumps([s["thread"]])); print(hashlib.sha256(p.read_bytes()).hexdigest())')
bash -eo pipefail -c {prepare}
prepare_rc=$?
export REQUESTS=$(python3 -c 'import os,pathlib; p=pathlib.Path(os.environ["GITHUB_OUTPUT"]); rows=[x.split("=",1)[1] for x in p.read_text().splitlines() if x.startswith("requests=")]; print(rows[-1] if rows else "[]")')
export GH_TOKEN=fixture-resolver STAGE=resolve
bash -eo pipefail -c {resolve}
resolve_rc=$?
export GH_TOKEN=fixture-publisher PHASE=COMPLETE STAGE=complete
bash -eo pipefail -c {complete}
complete_rc=$?
[ "$prepare_rc" = 0 ] && [ "$resolve_rc" = 0 ] && [ "$complete_rc" = 0 ]
'''


class DeferTest(unittest.TestCase):
    def test_success_and_recovery_after_reply_failure(self):
        run=pipeline_script()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            os.symlink(ROOT/'tests/fixtures/auto-reviewer-shared/gh_defer',root/'gh')
            sha='a'*40
            finding={'origin_review_id':101,'original_id':'R1-F1','path':'a.py','severity':'Important'}
            proposal={'schema_version':1,'repository':'owner/repo','pr_number':42,'head_sha':sha,**{k:finding[k] for k in ('origin_review_id','original_id','path')},'disposition':'DEFER','reason':'Can wait','owner':'alice'}
            prev={**finding,'status':'NOT_ADDRESSED','defer_decision':'ACCEPTED','reason':'Can wait','owner':'alice'}
            ledger={'schema_version':1,'repository':'owner/repo','pr_number':42,'head_sha':'b'*40,'review_source_id':101,'findings':[finding],'previous_findings':[], 'envelope_version':1,'published_review_id':101,'run_id':200,'duration_seconds':20,'action_progress':[],'disposition_events':[]}
            origin={'id':101,'user':{'login':'so-upstars'},'commit_id':'b'*40,'body':'<!-- claude-review-ledger:v1 '+json.dumps(ledger)+' -->'}
            comment=lambda i,author,body,review: {'databaseId':i,'author':{'login':author},'authorAssociation':'MEMBER','body':body,'createdAt':'2026-10-30T00:00:00Z','updatedAt':'2026-10-30T00:00:00Z','pullRequestReview':review}
            thread={'id':'thread1','path':'a.py','isResolved':False,'comments':[comment(1001,'so-upstars','**Important** R1-F1 - issue',{'databaseId':101}),comment(1002,'alice','<!-- cr-received-disposition:v1 '+json.dumps(proposal)+' -->',None)]}
            (root/'state.json').write_text(json.dumps({'origin':origin,'thread':thread}))
            (root/'actions.json').write_text(json.dumps({'schema_version':1,'repository':'owner/repo','pr_number':42,'head_sha':sha,'review_source_id':None,'findings':[],'previous_findings':[prev]}))
            (root/'post.md').write_text('| Intent | System | Craft | Critical | Important | Minor |\n| --- | --- | --- | --- | --- | --- |\n| PASS | PASS | PASS | 0 | 0 | 0 |\n**Readiness:** ready with fixes\n\nReviewed with: `cr-request@v0.0.11` skill · In 0 min 2 sec\nNext step: `/cr-received PR #42`\n')
            (root/'output').touch()
            env={**os.environ,'PATH':str(root)+os.pathsep+os.environ['PATH'],'FIXTURE_DIR':str(root),'REPO':'owner/repo','NUMBER':'42','HEAD_SHA':sha,'REVIEW_ID':'202','GITHUB_RUN_ID':'303','GITHUB_OUTPUT':str(root/'output'),'REVIEWER':'so-upstars','ACTIONS_FILE':str(root/'actions.json'),'POST_FILE':str(root/'post.md')}
            first=subprocess.run(['bash','-eo','pipefail','-c',run],env={**env,'FAIL_REPLY':'1'},capture_output=True,text=True)
            self.assertNotEqual(first.returncode,0,first.stderr)
            state=json.loads((root/'state.json').read_text())
            self.assertIsNotNone(state.get('issue'))
            self.assertFalse(state['thread']['isResolved'])
            self.assertIn('WRITE_FAILED',state['body'])
            self.assertIn('ready with fixes',state['body'])
            # A closed durable issue cannot authorize a retry's dependent writes.
            state['issue']['state']='closed'
            (root/'state.json').write_text(json.dumps(state))
            closed=subprocess.run(['bash','-eo','pipefail','-c',run],env=env,capture_output=True,text=True)
            self.assertNotEqual(closed.returncode,0,closed.stderr)
            closed_state=json.loads((root/'state.json').read_text())
            self.assertFalse(closed_state['thread']['isResolved'])
            self.assertEqual(len(closed_state['thread']['comments']),2)
            self.assertIn('WRITE_FAILED',closed_state['body'])
            state['issue']['state']='open'
            (root/'state.json').write_text(json.dumps(state))
            second=subprocess.run(['bash','-eo','pipefail','-c',run],env=env,capture_output=True,text=True)
            self.assertEqual(second.returncode,0,second.stderr)
            state=json.loads((root/'state.json').read_text())
            self.assertTrue(state['thread']['isResolved'])
            self.assertIn('Next step: `/verify -> /finalize squash`',state['body'])
            self.assertIn('**Readiness:** Ready',state['body'])
            trace=[json.loads(x) for x in (root/'trace.jsonl').read_text().splitlines()]
            self.assertEqual(sum(x['method']=='POST' and x['endpoint']=='repos/owner/repo/issues' for x in trace),1)
            self.assertEqual(sum(x['method']=='POST' and x['endpoint'].endswith('/replies') for x in trace),2)
            # Completed actions do not erase an unassessed axis or a separate not-ready gap.
            for readiness,axis in [('Not ready','PASS'),('not ready','UNASSESSED'),('Ready with fixes','UNASSESSED'),('ready with fixes','UNASSESSED')]:
                with self.subTest(readiness=readiness,axis=axis):
                    table=('| Intent | System | Craft | Critical | Important | Minor |\n'
                           '| --- | --- | --- | --- | --- | --- |\n'
                           f'| PASS | {axis} | PASS | 0 | 0 | 0 |\n')
                    (root/'state.json').write_text(json.dumps({'origin':origin,'thread':thread}))
                    (root/'output').write_text('')
                    (root/'post.md').write_text(table+f'**Readiness:** {readiness}\n\nReviewed with: `/cr-request` 0.0.11 skill · In 0 min 2 sec\nNext step: `/cr-received PR #42`\n')
                    result=subprocess.run(['bash','-eo','pipefail','-c',run],env=env,capture_output=True,text=True)
                    self.assertEqual(result.returncode,0,result.stderr)
                    completed=json.loads((root/'state.json').read_text())
                    self.assertTrue(completed['thread']['isResolved'])
                    self.assertIn(f'**Readiness:** {readiness}',completed['body'])
                    self.assertNotIn('WRITE_FAILED',completed['body'])
                    self.assertNotIn('deferred_complete=true',(root/'output').read_text())
            # A body-only source cannot authorize issue, reply or resolve; it remains visible as a gap.
            (root/'state.json').write_text(json.dumps({'origin':origin,'thread':{'id':'thread1','path':'other.py','isResolved':False,'comments':thread['comments']}}))
            (root/'trace.jsonl').write_text('')
            (root/'post.md').write_text('| Intent | System | Craft | Critical | Important | Minor |\n| --- | --- | --- | --- | --- | --- |\n| PASS | PASS | PASS | 0 | 0 | 0 |\n**Readiness:** ready with fixes\n\nReviewed with: `cr-request@v0.0.11` skill · In 0 min 2 sec\nNext step: `/cr-received PR #42`\n')
            missing=subprocess.run(['bash','-eo','pipefail','-c',run],env=env,capture_output=True,text=True)
            self.assertNotEqual(missing.returncode,0)
            missing_state=json.loads((root/'state.json').read_text())
            self.assertIn('Source thread missing or ambiguous',missing_state['body'])
            trace=[json.loads(x) for x in (root/'trace.jsonl').read_text().splitlines()]
            self.assertFalse(any(x['endpoint']=='repos/owner/repo/issues' or x['endpoint'].endswith('/replies') for x in trace))
            self.assertFalse(missing_state['thread']['isResolved'])
            for case in ('wrong_author', 'duplicate_disposition', 'conflicting_disposition', 'stale_proposal', 'bad_origin', 'missing_rights'):
                with self.subTest(case=case):
                    case_thread=json.loads(json.dumps(thread))
                    case_origin=json.loads(json.dumps(origin))
                    if case=='wrong_author': case_thread['comments'][0]['author']['login']='intruder'
                    if case=='duplicate_disposition': case_thread['comments'].append(dict(case_thread['comments'][1],databaseId=1004))
                    if case=='conflicting_disposition':
                        conflict={**proposal,'disposition':'REJECT','owner':None,'reason':'Different decision'}
                        case_thread['comments'].append(dict(case_thread['comments'][1],databaseId=1004,body='<!-- cr-received-disposition:v1 '+json.dumps(conflict)+' -->'))
                    if case=='stale_proposal': case_thread['comments'][1]['body']=case_thread['comments'][1]['body'].replace(sha,'c'*40)
                    if case=='bad_origin': case_origin['user']['login']='intruder'
                    (root/'state.json').write_text(json.dumps({'origin':case_origin,'thread':case_thread}))
                    (root/'trace.jsonl').write_text('')
                    case_env={**env,**({'FAIL_PERMISSION':'1'} if case=='missing_rights' else {})}
                    result=subprocess.run(['bash','-eo','pipefail','-c',run],env=case_env,capture_output=True,text=True)
                    self.assertNotEqual(result.returncode,0,result.stderr)
                    state=json.loads((root/'state.json').read_text())
                    self.assertFalse(state.get('issue'))
                    self.assertFalse(state['thread']['isResolved'])
                    trace=[json.loads(x) for x in (root/'trace.jsonl').read_text().splitlines()]
                    self.assertFalse(any(x['endpoint']=='repos/owner/repo/issues' or x['endpoint'].endswith('/replies') for x in trace))
            # A completed issue is reused even when its ledger update failed.
            (root/'actions.json').write_text(json.dumps({'schema_version':1,'repository':'owner/repo','pr_number':42,'head_sha':sha,'review_source_id':None,'findings':[],'previous_findings':[prev]}))
            (root/'state.json').write_text(json.dumps({'origin':origin,'thread':thread}))
            (root/'trace.jsonl').write_text('')
            failed_put=subprocess.run(['bash','-eo','pipefail','-c',run],env={**env,'FAIL_PUT_NUMBER':'2'},capture_output=True,text=True)
            self.assertNotEqual(failed_put.returncode,0)
            self.assertIsNotNone(json.loads((root/'state.json').read_text()).get('issue'))
            recovered=subprocess.run(['bash','-eo','pipefail','-c',run],env=env,capture_output=True,text=True)
            self.assertEqual(recovered.returncode,0,recovered.stderr)
            trace=[json.loads(x) for x in (root/'trace.jsonl').read_text().splitlines()]
            self.assertEqual(sum(x['method']=='POST' and x['endpoint']=='repos/owner/repo/issues' for x in trace),1)
            # Comment terminators in authorized data stay inside the JSON marker.
            hostile='Can wait --> <!-- claude-review-ledger:v1 {}'
            safe_thread=json.loads(json.dumps(thread))
            safe_proposal={**proposal,'reason':hostile}
            safe_thread['comments'][1]['body']='<!-- cr-received-disposition:v1 '+json.dumps(safe_proposal).replace('>','\\u003e').replace('<','\\u003c')+' -->'
            safe_actions=json.loads((root/'actions.json').read_text())
            safe_actions['previous_findings']=[{**prev,'reason':hostile}]
            (root/'actions.json').write_text(json.dumps(safe_actions))
            (root/'state.json').write_text(json.dumps({'origin':origin,'thread':safe_thread}))
            result=subprocess.run(['bash','-eo','pipefail','-c',run],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            safe_state=json.loads((root/'state.json').read_text())
            self.assertEqual(safe_state['body'].count('<!-- claude-review-ledger:v1 '),1)
            self.assertIn('&gt;',safe_state['issue']['body'])
            # Declining a proposal leaves it open and does not manufacture REJECT.
            decline_actions=json.loads((root/'actions.json').read_text())
            decline_actions['previous_findings']=[{**prev,'defer_decision':'DECLINED','owner':None}]
            (root/'actions.json').write_text(json.dumps(decline_actions))
            (root/'state.json').write_text(json.dumps({'origin':origin,'thread':thread}))
            declined=subprocess.run(['bash','-eo','pipefail','-c',run],env=env,capture_output=True,text=True)
            self.assertEqual(declined.returncode,0,declined.stderr)
            decline_state=json.loads((root/'state.json').read_text())
            decline_ledger=json.loads(decline_state['body'].split('<!-- claude-review-ledger:v1 ',1)[1].split(' -->',1)[0])
            self.assertEqual([event['disposition'] for event in decline_ledger['disposition_events']],['DEFER'])
            self.assertFalse(decline_state['thread']['isResolved'])
            self.assertFalse(decline_state.get('issue'))
            # A verified host REJECT is imported; a DECLINED defer is a separate state.
            rejected={**proposal,'disposition':'REJECT','owner':None,'reason':'Not a defect'}
            reject_thread=json.loads(json.dumps(thread))
            reject_thread['comments'][1]['body']='<!-- cr-received-disposition:v1 '+json.dumps(rejected)+' -->'
            reject_actions=json.loads((root/'actions.json').read_text())
            reject_actions['previous_findings']=[{**prev,'defer_decision':'NONE','owner':None}]
            (root/'actions.json').write_text(json.dumps(reject_actions))
            (root/'state.json').write_text(json.dumps({'origin':origin,'thread':reject_thread}))
            (root/'trace.jsonl').write_text('')
            rejected_run=subprocess.run(['bash','-eo','pipefail','-c',run],env=env,capture_output=True,text=True)
            self.assertEqual(rejected_run.returncode,0,rejected_run.stderr)
            reject_state=json.loads((root/'state.json').read_text())
            reject_ledger=json.loads(reject_state['body'].split('<!-- claude-review-ledger:v1 ',1)[1].split(' -->',1)[0])
            self.assertEqual([x['disposition'] for x in reject_ledger['disposition_events']],['REJECT'])
            self.assertFalse(reject_state['thread']['isResolved'])
            self.assertFalse(reject_state.get('issue'))
            # A related ID in prose cannot claim the first finding's root; valid work still proceeds.
            second_finding={**finding,'original_id':'R1-F2'}
            origin_ledger={**ledger,'findings':[finding,second_finding]}
            two_origin={**origin,'body':'<!-- claude-review-ledger:v1 '+json.dumps(origin_ledger)+' -->'}
            two_thread=json.loads(json.dumps(thread))
            two_thread['comments'][0]['body']+='; related R1-F2'
            second_prev={**second_finding,'status':'ADDRESSED','defer_decision':'NONE','reason':'Fixed','owner':None}
            actions=json.loads((root/'actions.json').read_text())
            actions['previous_findings']=[prev,second_prev]
            (root/'actions.json').write_text(json.dumps(actions))
            (root/'state.json').write_text(json.dumps({'origin':two_origin,'thread':two_thread}))
            (root/'trace.jsonl').write_text('')
            result=subprocess.run(['bash','-eo','pipefail','-c',run],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            state=json.loads((root/'state.json').read_text())
            self.assertIsNotNone(state.get('issue'))
            self.assertTrue(state['thread']['isResolved'])
            envelope=json.loads(state['body'].split('<!-- claude-review-ledger:v1 ',1)[1].split(' -->',1)[0])
            self.assertEqual(envelope['action_progress'][1]['resolve']['state'],'NOT_REQUESTED')
            self.assertIn('No source thread',envelope['action_progress'][1]['error'])
            self.assertIsNone(envelope['action_progress'][1]['resolve']['thread_id'])

class ResolverRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        os.symlink(ROOT/'tests/fixtures/auto-reviewer-shared/gh_defer',self.root/'gh')
        sha='a'*40
        self.finding={'origin_review_id':101,'original_id':'R1-F1','path':'a.py','severity':'Important'}
        f=self.finding
        ledger={'schema_version':1,'repository':'owner/repo','pr_number':42,'head_sha':'b'*40,
                'review_source_id':101,'findings':[f],'previous_findings':[],'envelope_version':1,
                'published_review_id':101,'run_id':200,'duration_seconds':20,'action_progress':[],'disposition_events':[]}
        proposal={'schema_version':1,'repository':'owner/repo','pr_number':42,'head_sha':sha,
                  **{k:f[k] for k in ('origin_review_id','original_id','path')},
                  'disposition':'DEFER','reason':'Can wait','owner':'alice'}
        def comment(i,author,body,review):
            return {'databaseId':i,'author':{'login':author},'authorAssociation':'MEMBER','body':body,
                    'createdAt':'2026-10-30T00:00:00Z','updatedAt':'2026-10-30T00:00:00Z','pullRequestReview':review}
        self.initial={'origin':{'id':101,'user':{'login':'so-upstars'},'commit_id':'b'*40,
                              'body':'<!-- claude-review-ledger:v1 '+json.dumps(ledger)+' -->'},
                      'thread':{'id':'api-thread-id','path':'a.py','isResolved':False,'comments':[
                          comment(1001,'so-upstars','**Important** R1-F1 - issue',{'databaseId':101}),
                          comment(1002,'alice','<!-- cr-received-disposition:v1 '+json.dumps(proposal)+' -->',None)]}}
        self.actions={'schema_version':1,'repository':'owner/repo','pr_number':42,'head_sha':sha,
                      'review_source_id':None,'findings':[],'previous_findings':[
                          {**f,'status':'NOT_ADDRESSED','defer_decision':'ACCEPTED','reason':'Can wait','owner':'alice'}]}
        self.env={**os.environ,'PATH':str(self.root)+os.pathsep+os.environ['PATH'],
                  'FIXTURE_DIR':str(self.root),'REPO':'owner/repo','NUMBER':'42','HEAD_SHA':sha,
                  'REVIEW_ID':'202','GITHUB_RUN_ID':'303','GITHUB_OUTPUT':str(self.root/'output'),
                  'REVIEWER':'so-upstars','ACTIONS_FILE':str(self.root/'actions.json'),'POST_FILE':str(self.root/'post.md'),
                  'REVIEW_THREADS_FILE':str(self.root/'threads.json')}
        self.reset()

    def reset(self, addressed=False):
        import hashlib
        (self.root/'state.json').write_text(json.dumps(self.initial))
        (self.root/'threads.json').write_text(json.dumps([self.initial['thread']]))
        self.env['REVIEW_THREADS_DIGEST']=hashlib.sha256((self.root/'threads.json').read_bytes()).hexdigest()
        actions=json.loads(json.dumps(self.actions))
        if addressed: actions['previous_findings'][0].update(status='ADDRESSED',defer_decision='NONE',owner=None)
        (self.root/'actions.json').write_text(json.dumps(actions))
        (self.root/'post.md').write_text('<!-- claude-review sha='+('a'*40)+' -->\n'
            '| Intent | System | Craft | Critical | Important | Minor |\n'
            '| --- | --- | --- | --- | --- | --- |\n| PASS | PASS | PASS | 0 | 0 | 0 |\n'
            '**Readiness:** Ready with fixes\n\nReviewed with: `auto-reviewer.yaml` `v0.0.3` workflow · `cr-request` `v0.0.11` skill · In `0 min 2 sec`\n'
            'Next step: `/cr-received PR #42`\n')
        (self.root/'output').write_text(''); (self.root/'trace.jsonl').write_text('')

    def run_step(self,name,**extra):
        return subprocess.run(['bash','-eo','pipefail','-c',step(name)],env={**self.env,**extra},capture_output=True,text=True)

    def test_completion_updates_public_readiness_without_reintroducing_next_step(self):
        self.reset(addressed=True)
        table='| Intent | System | Craft | Critical | Important | Minor |\n| --- | --- | --- | --- | --- | --- |\n| PASS | PASS | PASS | 0 | 0 | 0 |'
        (self.root/'post.md').write_text('Readiness: **Ready with fixes**\n\n'+table+'\n\n<details><summary>Details</summary>\n\nEvidence\n\n</details>\n\n'+USED_EXAMPLE+'\n')
        result=self.run_step('Publish review actions',GH_TOKEN='fixture-publisher',PHASE='PREPARE',STAGE='prepare')
        self.assertEqual(result.returncode,0,result.stderr)
        self.request=json.loads(next(x.split('=',1)[1] for x in (self.root/'output').read_text().splitlines() if x.startswith('requests=')))
        self.assertEqual(self.resolve().returncode,0)
        completed=self.complete()
        self.assertEqual(completed.returncode,0,completed.stderr)
        body=self.state()['body']
        self.assertTrue(body.startswith('Readiness: **Ready**\n'))
        self.assertNotIn('Next step:',body)
        self.assertIn(USED_EXAMPLE,body)

    def prepare(self,addressed=False):
        self.reset(addressed)
        result=self.run_step('Publish review actions',GH_TOKEN='fixture-publisher',PHASE='PREPARE',STAGE='prepare')
        self.assertEqual(result.returncode,0,result.stderr)
        self.request=json.loads(next(x.split('=',1)[1] for x in (self.root/'output').read_text().splitlines() if x.startswith('requests=')))
        state=self.state()
        self.assertFalse(state['thread']['isResolved'])
        self.assertNotIn('deferred_complete=true',(self.root/'output').read_text())
        self.assertIn('"state":"PENDING"',state['body'])

    def state(self): return json.loads((self.root/'state.json').read_text())
    def resolve(self,**extra):
        return self.run_step('Resolve verified thread metadata',GH_TOKEN='fixture-resolver',STAGE='resolve',REQUESTS=json.dumps(self.request),**extra)
    def complete(self,**extra):
        return self.run_step('Complete review actions',GH_TOKEN='fixture-publisher',PHASE='COMPLETE',STAGE='complete',**extra)
    def mutations(self):
        return [x for x in map(json.loads,(self.root/'trace.jsonl').read_text().splitlines()) if x.get('mutation')]

    def edit_proposal(self, reason, update_time=False):
        state=self.state(); comment=state['thread']['comments'][1]
        value=json.loads(comment['body'].split('<!-- cr-received-disposition:v1 ',1)[1].split(' -->',1)[0])
        value['reason']=reason
        comment['body']='<!-- cr-received-disposition:v1 '+json.dumps(value)+' -->'
        if update_time: comment['updatedAt']='2026-10-30T00:01:00Z'
        (self.root/'state.json').write_text(json.dumps(state))

    def test_api_reason_tokens_block_prepare_and_complete_puts(self):
        self.edit_proposal('ghp_'+'SyntheticFixtureOnly123')
        result=self.run_step('Publish review actions',GH_TOKEN='fixture-publisher',PHASE='PREPARE',STAGE='prepare')
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(any(x['method']=='PUT' for x in map(json.loads,(self.root/'trace.jsonl').read_text().splitlines())))
        self.prepare(addressed=True)
        count=self.state().get('put_count',0)
        self.edit_proposal('ATATT'+'SyntheticFixtureOnly123')
        result=self.complete()
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.state().get('put_count',0),count)
        self.assertNotIn('ATATT'+'SyntheticFixtureOnly123',self.state()['body'])

    def test_edited_proposal_blocks_resolver_even_with_same_timestamp(self):
        self.prepare(); self.edit_proposal('Changed after acceptance')
        result=self.resolve()
        self.assertNotEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.mutations(),[])
        self.assertFalse(self.state()['thread']['isResolved'])

    def test_edited_proposal_preserves_acceptance_during_complete_and_retry(self):
        self.prepare(); result=self.resolve(); self.assertEqual(result.returncode,0,result.stderr)
        original=self.state()['body']; count=self.state().get('put_count',0)
        self.edit_proposal('Changed after acceptance',update_time=True)
        result=self.complete()
        self.assertNotEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.state()['body'],original)
        self.assertEqual(self.state().get('put_count',0),count)
        self.assertNotIn('deferred_complete=true',(self.root/'output').read_text())
        result=self.run_step('Publish review actions',GH_TOKEN='fixture-publisher',PHASE='PREPARE',STAGE='prepare')
        self.assertNotEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.state()['body'],original)

    def test_proposal_edit_during_completion_stays_pending(self):
        self.prepare(); result=self.resolve(); self.assertEqual(result.returncode,0,result.stderr)
        result=self.complete(EDIT_PROPOSAL_ON_COMPLETE_READ='1')
        self.assertNotEqual(result.returncode,0,result.stderr)
        self.assertNotIn('deferred_complete=true',(self.root/'output').read_text())
        self.assertIn('Can wait',self.state()['body'])
        self.assertNotIn('Changed while completing',self.state()['body'])

    def test_reposted_review_in_same_run_retains_prepared_acceptance(self):
        self.prepare()
        state=self.state(); original=state['body']; count=state.get('put_count',0)
        self.edit_proposal('Changed before failed-job retry',update_time=True)
        state=self.state()
        state['history']=[{'id':202,'user':{'login':'so-upstars'},'commit_id':'a'*40,'body':original}]
        state['body']='initial'
        (self.root/'state.json').write_text(json.dumps(state))
        (self.root/'actions.json').write_text(json.dumps(self.actions))
        result=self.run_step('Publish review actions',GH_TOKEN='fixture-publisher',PHASE='PREPARE',STAGE='prepare',REVIEW_ID='203')
        self.assertNotEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.state().get('put_count',0),count)
        result=self.run_step('Publish review actions',GH_TOKEN='fixture-publisher',PHASE='PREPARE',STAGE='prepare',REVIEW_ID='204',GITHUB_RUN_ID='404')
        self.assertNotEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.state().get('put_count',0),count)
        # A genuinely fresh model run captures the edited proposal before reviewing it.
        import hashlib
        snapshot=self.root/'threads.json'
        snapshot.write_text(json.dumps([self.state()['thread']]))
        self.env['REVIEW_THREADS_DIGEST']=hashlib.sha256(snapshot.read_bytes()).hexdigest()
        result=self.run_step('Publish review actions',GH_TOKEN='fixture-publisher',PHASE='PREPARE',STAGE='prepare',REVIEW_ID='204',GITHUB_RUN_ID='404')
        self.assertEqual(result.returncode,0,result.stderr)
        envelope=json.loads(self.state()['body'].split('<!-- claude-review-ledger:v1 ',1)[1].split(' -->',1)[0])
        self.assertEqual(envelope['run_id'],404)
        self.assertEqual(envelope['disposition_events'][0]['reason'],'Changed before failed-job retry')

    def test_success_api_identity_bot_actor_and_idempotent_completion(self):
        self.prepare()
        # An untrusted artifact cannot supply the mutation ID.
        actions=json.loads((self.root/'actions.json').read_text()); actions['thread_id']='forged-artifact-id'
        (self.root/'actions.json').write_text(json.dumps(actions))
        result=self.resolve(); self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.state()['thread']['resolvedBy']['login'],'github-actions[bot]')
        self.assertNotIn('deferred_complete=true',(self.root/'output').read_text())
        result=self.complete(); self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('deferred_complete=true',(self.root/'output').read_text())
        self.assertEqual([x['id'] for x in self.mutations()],['api-thread-id'])
        self.assertTrue(all(x['token']=='fixture-resolver' for x in self.mutations()))
        result=self.resolve(); self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(len(self.mutations()),1)

    def test_thread_root_is_found_with_old_and_new_badge_style(self):
        for style in ('for-the-badge','flat-square'):
            with self.subTest(style=style):
                self.initial['thread']['comments'][0]['body']='![Important](https://img.shields.io/badge/Medium-C99A3A?style='+style+') R1-F1 - issue'
                self.prepare()
                result=self.resolve(); self.assertEqual(result.returncode,0,result.stderr)
                result=self.complete(); self.assertEqual(result.returncode,0,result.stderr)
                self.assertNotIn('source thread is missing or ambiguous',result.stderr)

    def test_thread_root_is_found_with_colored_circle(self):
        self.initial['thread']['comments'][0]['body']=r'$\color{orange}{\bigcirc}$ Medium R1-F1 - issue'
        self.prepare()
        result=self.resolve(); self.assertEqual(result.returncode,0,result.stderr)
        result=self.complete(); self.assertEqual(result.returncode,0,result.stderr)

    def test_thread_root_is_found_with_concentric_circle(self):
        self.initial['thread']['comments'][0]['body']=r'$\color{orange}◎$ Medium R1-F1 - issue'
        self.prepare()
        result=self.resolve(); self.assertEqual(result.returncode,0,result.stderr)
        result=self.complete(); self.assertEqual(result.returncode,0,result.stderr)

    def test_addressed_and_already_resolved_any_actor(self):
        for actor in (None,'so-upstars','github-actions[bot]','other-human'):
            with self.subTest(actor=actor):
                self.prepare(addressed=True)
                if actor:
                    state=self.state(); state['thread'].update(isResolved=True,resolvedBy={'login':actor})
                    (self.root/'state.json').write_text(json.dumps(state))
                result=self.resolve(); self.assertEqual(result.returncode,0,result.stderr)
                result=self.complete(); self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(len(self.mutations()),0 if actor else 1)
                self.assertNotIn('WRITE_FAILED',self.state()['body'])

    def test_resolver_reads_all_thread_pages(self):
        self.prepare()
        state=self.state(); state['paginated']=True
        (self.root/'state.json').write_text(json.dumps(state))
        result=self.resolve(); self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual([x['id'] for x in self.mutations()],['api-thread-id'])

    def test_gap_issue_closed_or_reply_unreadable_has_zero_mutations(self):
        for knob in ('CLOSE_ISSUE_AFTER_PREPARE','FAIL_REPLY_READ','CHANGE_HEAD_DURING_ISSUE'):
            with self.subTest(knob=knob):
                self.prepare()
                result=self.resolve(**{knob:'1'})
                self.assertNotEqual(result.returncode,0,result.stderr)
                self.assertFalse(self.state()['thread']['isResolved'])
                self.assertEqual(self.mutations(),[])
                result=self.complete()
                self.assertNotEqual(result.returncode,0,result.stderr)
                self.assertTrue(any(x in self.state()['body'] for x in ('WRITE_FAILED','PENDING')))
                self.assertNotIn('deferred_complete=true',(self.root/'output').read_text())

    def test_denial_and_completion_readback_failure_remain_pending(self):
        for knob in ('FAIL_RESOLVE','FAIL_RESOLVE_READBACK'):
            with self.subTest(knob=knob):
                self.prepare()
                result=self.resolve(**{knob:'1'})
                if knob=='FAIL_RESOLVE': self.assertNotEqual(result.returncode,0)
                result=self.complete(**{knob:'1'})
                self.assertNotEqual(result.returncode,0)
                self.assertTrue(any(x in self.state()['body'] for x in ('WRITE_FAILED','PENDING')))
                self.assertNotIn('deferred_complete=true',(self.root/'output').read_text())

    def test_canonical_completion_readback_failure_never_claims_success(self):
        self.prepare()
        result=self.resolve(); self.assertEqual(result.returncode,0,result.stderr)
        result=self.complete(FAIL_FINAL_READBACK='1')
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('deferred_complete=true',(self.root/'output').read_text())
        result=self.complete(); self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('deferred_complete=true',(self.root/'output').read_text())
        self.assertEqual(len(self.mutations()),1)

    def test_untrusted_handoff_and_changed_metadata_never_mutate(self):
        cases=('repository','pr_number','head_sha','origin_review_id','root_id','thread_id',
               'root_author','origin_author','wrong_path','ambiguous','stale_head','wrong_pr_link','wrong_reply_root')
        for case in cases:
            with self.subTest(case=case):
                self.prepare()
                if case in self.request[0]:
                    self.request[0][case]=999 if type(self.request[0][case]) is int else 'wrong'
                elif case=='thread_id': self.request[0]['thread_id']='forged'
                else:
                    state=self.state()
                    if case=='root_author': state['thread']['comments'][0]['author']['login']='intruder'
                    elif case=='origin_author': state['origin']['user']['login']='intruder'
                    elif case=='wrong_path': state['thread']['path']='wrong.py'
                    elif case=='ambiguous': state['ambiguous']=True
                    elif case=='stale_head': state['head']='c'*40
                    elif case=='wrong_pr_link': state['wrong_pr_link']=True
                    elif case=='wrong_reply_root': state['wrong_reply_root']=True
                    (self.root/'state.json').write_text(json.dumps(state))
                result=self.resolve()
                self.assertNotEqual(result.returncode,0,result.stderr)
                self.assertEqual(self.mutations(),[])

    def test_actual_status_waits_for_completion_and_readback(self):
        self.prepare()
        extra={'GH_TOKEN':'fixture-status','POST_OUTCOME':'success','LEDGER_OUTCOME':'skipped',
               'DEFERRED_COMPLETE':'','SIGNATURE':'Reviewed with: `auto-reviewer.yaml` `v0.0.3` workflow · `cr-request` `v0.0.11` skill','SUMMARY':'pending actions',
               'STATE':'failure','VALIDATED_AT':'2026-10-30T00:00:00Z','JOB_STATUS':'success',
               'CONTEXT':'Auto Reviewer','RUN_URL':'https://github.com/owner/repo/actions/runs/303',
               'MARKER':'<!-- claude-review sha='+('a'*40)+' -->'}
        result=self.run_step('Report review status',**extra)
        self.assertNotEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.state()['status']['state'],'failure')
        result=self.resolve(); self.assertEqual(result.returncode,0,result.stderr)
        # Resolver success alone cannot turn the final status green.
        result=self.run_step('Report review status',**extra)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.state()['status']['state'],'failure')
        result=self.complete(); self.assertEqual(result.returncode,0,result.stderr)
        extra.update(LEDGER_OUTCOME='success',DEFERRED_COMPLETE='true')
        result=self.run_step('Report review status',**extra)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.state()['status']['state'],'success')
        # A newer run's pending status is left untouched by the old completion.
        result=self.run_step('Report review status',**{**extra,'STATUS_OWNER':'newer-run'})
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('Status owned by another run',result.stdout)
        self.assertEqual(self.state()['status']['state'],'success')
        # Cancelled completion never uses a preexisting resolved thread as a substitute.
        extra.update(LEDGER_OUTCOME='cancelled',DEFERRED_COMPLETE='')
        result=self.run_step('Report review status',**extra)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.state()['status']['state'],'failure')

    def test_low_tokens_cannot_resolve_and_resolver_cannot_publish(self):
        self.prepare()
        mutation={'query':'mutation($id:ID!) { resolveReviewThread(input:{threadId:$id}) { thread { id } } }','variables':{'id':'api-thread-id'}}
        for role,endpoint,method,payload in (
            ('fixture-publisher','graphql','POST',mutation),
            ('fixture-resolver','repos/owner/repo/issues','POST',{'body':'forbidden'}),
            ('fixture-resolver','repos/owner/repo/pulls/42/comments/1001/replies','POST',{'body':'forbidden'}),
            ('fixture-resolver','repos/owner/repo/pulls/42/reviews/202','PUT',{'body':'forbidden'})):
            result=subprocess.run(['gh','api',endpoint,'--method',method,'--input','-'],
                input=json.dumps(payload),env={**self.env,'GH_TOKEN':role},text=True,capture_output=True)
            self.assertNotEqual(result.returncode,0)
        self.assertFalse(self.state()['thread']['isResolved'])



    def test_first_prepare_rejects_proposal_edited_since_review_snapshot(self):
        import hashlib
        snapshot=self.root/'threads.json'
        snapshot.write_text(json.dumps([self.initial['thread']]))
        self.edit_proposal('Edited before first PREPARE', update_time=True)
        result=self.run_step('Publish review actions',GH_TOKEN='fixture-publisher',PHASE='PREPARE',
            REVIEW_THREADS_FILE=str(snapshot),REVIEW_THREADS_DIGEST=hashlib.sha256(snapshot.read_bytes()).hexdigest())
        self.assertNotEqual(result.returncode,0,result.stderr)
        self.assertIn('reviewed DEFER proposal changed',result.stderr)
        self.assertFalse(self.state().get('issue'))
        self.assertNotIn('body',self.state())

    def test_snapshot_digest_rejects_tampered_review_context(self):
        (self.root/'threads.json').write_text('[]')
        result=self.run_step('Publish review actions',GH_TOKEN='fixture-publisher',PHASE='PREPARE')
        self.assertNotEqual(result.returncode,0,result.stderr)
        self.assertIn('review snapshot digest mismatch',result.stderr)
        self.assertFalse(self.state().get('issue'))

    def test_body_only_findings_do_not_require_unrequested_mutations(self):
        for status in ('ADDRESSED','NOT_ADDRESSED'):
            with self.subTest(status=status):
                self.reset()
                state=self.state(); state['thread']['path']='other.py'
                (self.root/'state.json').write_text(json.dumps(state))
                actions=json.loads((self.root/'actions.json').read_text())
                actions['previous_findings'][0].update(status=status,defer_decision='NONE',owner=None)
                (self.root/'actions.json').write_text(json.dumps(actions))
                result=self.run_step('Publish review actions',GH_TOKEN='fixture-publisher',PHASE='PREPARE')
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('No source thread',self.state()['body'])
                self.assertFalse(self.state().get('issue'))
                self.assertEqual(self.mutations(),[])

    def test_foreign_deferred_markers_cannot_block_recovery(self):
        marker='<!-- claude-review-deferred:v1 '+json.dumps({'repository':'owner/repo','pr_number':42,
            'origin_review_id':101,'original_id':'R1-F1','path':'a.py'},separators=(',',':'))+' -->'
        state=self.state()
        state['extra_issues']=[{'number':88,'user':{'login':'mallory'},'body':marker}]
        state['thread']['comments'].append({'databaseId':1004,'author':{'login':'mallory'},'body':marker})
        (self.root/'state.json').write_text(json.dumps(state))
        result=self.run_step('Publish review actions',GH_TOKEN='fixture-publisher',PHASE='PREPARE')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.state()['issue']['user']['login'],'so-upstars')
        self.assertFalse(self.state()['thread']['isResolved'])

    def test_legacy_origin_records_gap_without_mutations(self):
        state=self.state(); state['origin']['body']='Legacy prose without ledger'
        (self.root/'state.json').write_text(json.dumps(state))
        result=self.run_step('Publish review actions',GH_TOKEN='fixture-publisher',PHASE='PREPARE')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('Origin ledger unavailable',self.state()['body'])
        self.assertFalse(self.state().get('issue'))
        self.assertEqual(self.mutations(),[])



class PublicationFailureTest(unittest.TestCase):
    def test_publish_finalizer_owns_pending_status_and_has_no_pat(self):
        workflow=SummaryRefreshTest.workflow()
        item=next(s for s in workflow['jobs']['publish']['steps'] if s.get('name')=='Finalize failed publication')
        self.assertEqual(item['if'],'failure() || cancelled()')
        self.assertEqual(item['env'],{'GH_TOKEN':'${{ github.token }}'})
        for current,expected in [('run pending',True),('other pending',False),('run success',False)]:
            with self.subTest(current=current), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                fake=root/'gh'
                fake.write_text('#!/bin/sh\ncase "$2" in *status[?]*) printf "%s\\n" "$CURRENT";; *statuses*) printf "%s" "$*" > "$TRACE";; *) exit 2;; esac\n')
                fake.chmod(0o755)
                env={**os.environ,'PATH':str(root)+os.pathsep+os.environ['PATH'],
                    'CURRENT':current,'TRACE':str(root/'trace'),'REPO':'owner/repo',
                    'HEAD_SHA':'a'*40,'CONTEXT':'Auto Reviewer','RUN_URL':'run'}
                result=subprocess.run(['bash','-eo','pipefail','-c',item['run']],env=env,capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual((root/'trace').exists(),expected)
                if expected: self.assertIn('state=error',(root/'trace').read_text())


USED_EXAMPLE = 'Used: `cr-request` <sup>[0.0.16](https://www.skills.sh/sentimony/agentic-workflow/cr-request)</sup> · `auto-reviewer-shared` <sup>[0.0.4](https://github.com/sentimony/github-workflows)</sup> · `claude-sonnet-5-5 medium` · `0 min 49 sec` · `202,629 tokens` · `$0.1634`'


class PublicPresentationTest(unittest.TestCase):
    def render(self, readiness='Ready', ref='v0.0.4', usage=True, reversed_footer=False, details='<details open><summary>Details</summary>', full=False, evidence='Full evidence.', extra_before='', extra_after='', row='| PASS | PASS | PASS | 0 | 0 | 0 |'):
        signature=f'Reviewed with: `auto-reviewer-shared.yaml` `{ref}` workflow · `cr-request` `v0.0.16` skill · In `0 min 49 sec`'
        if usage:
            signature+=' · `claude-sonnet-5-5` `medium` · `202,629 tokens` · `$0.1634`'
        footer=[signature,'Next step: `/verify -> /finalize squash`']
        if reversed_footer: footer.reverse()
        marker='<!-- claude-review sha='+('a'*40)+' -->'
        table='| Intent | System | Craft | Critical | Important | Minor |\n| --- | --- | --- | --- | --- | --- |\n'+row
        body=marker+'\n\n'+extra_before+table+'\n\n**Readiness:** '+readiness+'\n\n'+details+'\n\n'+evidence+'\n\n</details>\n\n'+extra_after+'\n'.join(footer)+'\n'
        script=step('Validate review body')
        fragment=script[script.index('# The Next step line loses'):script.index('# GitHub rejects review bodies over')]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'draft').write_text(body)
            env={**os.environ,'BODY_FILE':str(root/'draft'),'POST_FILE':str(root/'post'),'SIGNATURE':signature,'INTERNAL_SIGNATURE':signature,
                 'LAST_LINE':footer[-1]}
            if full:
                fake=root/'gh'
                fake.write_text('#!/bin/sh\ncase "$2" in\n  */status[?]*) echo run-url ;;\n  */pulls/42) echo '+('a'*40)+' ;;\n'
                                '  */statuses/*) echo "$*" >> '+str(root/'statuses')+' ;;\n  *) exit 2 ;;\nesac\n')
                fake.chmod(0o755)
                env.update(PATH=str(root)+os.pathsep+os.environ['PATH'],REPO='owner/repo',NUMBER='42',HEAD_SHA='a'*40,
                           RUN_URL='run-url',CONTEXT='Auto Reviewer',MARKER=marker,JOB_STATUS='success',REVIEW_RESULT='success',VERSION='0.0.16',
                           WORKFLOW_IDENTITY='auto-reviewer-shared.yaml@'+ref,COMMENTS_FILE=str(root/'comments'),
                           ACTIONS_FILE=str(root/'actions'),GITHUB_OUTPUT=str(root/'output'))
                command=script
            else:
                command='signature="$INTERNAL_SIGNATURE"; last="$LAST_LINE"\n'+fragment
            result=subprocess.run(['bash','-eo','pipefail','-c',command],env=env,capture_output=True,text=True)
            result.outputs=(root/'output').read_text() if (root/'output').exists() else ''
            result.statuses=(root/'statuses').read_text() if (root/'statuses').exists() else ''
            return result,(root/'post').read_text() if (root/'post').exists() else '',marker,table

    def test_exact_requested_published_layout(self):
        result,body,marker,table=self.render()
        self.assertEqual(result.returncode,0,result.stderr)
        expected=marker+'\n\nReadiness: **Ready**\n\n'+table+'\n\n<details><summary>Details</summary>\n\nFull evidence.\n\n</details>\n\n'+USED_EXAMPLE+'\n'
        self.assertEqual(body,expected)
        self.assertNotIn('Next step:',body)
        self.assertNotIn('Reviewed with:',body)

    def test_whole_validator_exports_the_actual_public_signature(self):
        result,body,marker,table=self.render(full=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('ready_to_post=true\n',result.outputs)
        self.assertIn('signature='+USED_EXAMPLE+'\n',result.outputs)
        self.assertIn('summary=Critical 0, Important 0, Minor 0; Ready\n',result.outputs)
        self.assertTrue(body.startswith(marker+'\n\nReadiness: **Ready**\n'))

    def test_nested_details_are_rejected_before_publication(self):
        result,body,_,_=self.render(details='<details><summary>Details</summary>\n<details><summary>Other</summary>')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(body,'')

    def test_details_tags_quoted_as_code_remain_publishable_and_intact(self):
        cases=('Quoted `<details>` and `</details>` tags.',
               '```html\n<details>\n</details>\n```',
               '~~~html\n<details open>\n</details>\n~~~',
               '    <details>\n    </details>',
               '``literal\n<details>\n</details>\n``',
               'Use `` `<details>` `` in code.',
               'Hidden comment: <!-- <details> </details> --> remains intact.')
        for evidence in cases:
            with self.subTest(evidence=evidence):
                result,body,_,_=self.render(evidence=evidence)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('\n\n'+evidence+'\n\n</details>',body)
                self.assertTrue(body.rstrip().endswith(USED_EXAMPLE))

    def test_unmatched_backtick_cannot_consume_the_details_boundary(self):
        for evidence in ('It don`t break.', 'Unmatched ` quote.\n\nAnother paragraph with `code`.'):
            with self.subTest(evidence=evidence):
                result,body,_,_=self.render(evidence=evidence)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn(evidence,body)
                self.assertTrue(body.rstrip().endswith(USED_EXAMPLE))

    def test_evidence_outside_details_fails_instead_of_silently_disappearing(self):
        for side in ('extra_before','extra_after'):
            with self.subTest(side=side):
                result,body,_,_=self.render(**{side:'Additional material evidence.\n\n'})
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(body,'')
                self.assertIn('outside Details',result.stderr)

    def test_other_readiness_values_and_legacy_footer_order(self):
        for readiness in ('Ready with fixes','Not ready'):
            with self.subTest(readiness=readiness):
                result,body,_,_=self.render(readiness=readiness,reversed_footer=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('Readiness: **'+readiness+'**',body)
                self.assertNotIn('Next step:',body)
                self.assertTrue(body.rstrip().endswith(USED_EXAMPLE))

    def test_unknown_usage_and_nonversion_refs_remain_honest(self):
        for ref in ('a'*40, 'v2.preview', 'feature/review'):
            with self.subTest(ref=ref):
                result,body,_,_=self.render(ref=ref,usage=False)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('<sup>['+ref+'](https://github.com/sentimony/github-workflows)</sup>',body)
                self.assertTrue(body.rstrip().endswith(' · `0 min 49 sec`'))
                self.assertNotIn('tokens',body)
                self.assertNotIn('Next step:',body)


class FooterTest(unittest.TestCase):
    def test_footer_grammar_from_workflow(self):
        script=step('Validate review body')
        start=script.index("tail2=$(tr -d")
        end=script.index('          # The Next step line loses') if '          # The Next step line loses' in script else script.index('# The Next step line loses')
        fragment=script[start:end]
        signature='Reviewed with: `auto-reviewer-shared.yaml` `v0.0.3` workflow · `cr-request` `v0.0.11` skill · In `3 min 12 sec`'
        accepts=['Next step: `/cr-received` · 4 findings to rule on','Next step: none','Next step: none · nothing to rule on','Next step: `/verify docs/plans/x.md` -> `/finalize` squash','Next step: `/verify` -> `/finalize` squash','Next step: `/cr-received PR #16`']
        accepts += ['Next step: `/verify -> /finalize squash`',
                    'Next step: `/verify docs/plans/x.md -> /finalize squash`']
        rejects=['Next step: cr-received - 4 findings','Next step: ','Next step: `/cr-received` - 4 findings','Next step: none - nothing to rule on','Next step: `/verify` - `/finalize` squash']
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'body'
            for nextline in accepts+rejects:
                with self.subTest(nextline=nextline):
                    path.write_text(signature+'\n'+nextline+'\n')
                    env={**os.environ,'BODY_FILE':str(path),'VERSION':'0.0.11','WORKFLOW_IDENTITY':'auto-reviewer-shared.yaml@v0.0.3'}
                    result=subprocess.run(['bash','-c','fail() { exit 1; }\n'+fragment],env=env,capture_output=True,text=True)
                    self.assertEqual(result.returncode==0,nextline in accepts,result.stderr)
            for body in [signature+'\r\n'+accepts[0]+'\r\n',signature+'\n\n'+accepts[0]+'\n',accepts[0]+'\n'+signature+'\n']:
                path.write_bytes(body.encode())
                result=subprocess.run(['bash','-c','fail() { exit 1; }\n'+fragment],env={**os.environ,'BODY_FILE':str(path),'VERSION':'0.0.11','WORKFLOW_IDENTITY':'auto-reviewer-shared.yaml@v0.0.3'},capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
            for body in [signature.replace('skill ·','skill·')+'\n'+accepts[0]+'\n',signature+'\nextra\n'+accepts[0]+'\n']:
                path.write_text(body)
                result=subprocess.run(['bash','-c','fail() { exit 1; }\n'+fragment],env={**os.environ,'BODY_FILE':str(path),'VERSION':'0.0.11','WORKFLOW_IDENTITY':'auto-reviewer-shared.yaml@v0.0.3'},capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)

    def test_workflow_identity_is_validated_against_trusted_job_output(self):
        script=step('Validate review body')
        fragment='fail() { exit 1; }\n'+script[script.index('tail2=$(tr -d'):script.index('# The Next step line loses')]
        identity='auto-reviewer-shared.yaml@v0.0.3'
        cases=[('v0.0.3',identity,True),('v0.0.1',identity,False),
               (None,identity,False),(None,'',False),('v0.0.3','',False)]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'body'
            for ref,expected_identity,accepted in cases:
                with self.subTest(ref=ref,identity=expected_identity):
                    prefix=f'`auto-reviewer-shared.yaml` `{ref}` workflow · ' if ref is not None else ''
                    path.write_text('Reviewed with: '+prefix+'`cr-request` `v0.0.16` skill · In `0 min 39 sec`\nNext step: `/verify -> /finalize squash`\n')
                    env={**os.environ,'BODY_FILE':str(path),'VERSION':'0.0.16','WORKFLOW_IDENTITY':expected_identity}
                    result=subprocess.run(['bash','-c',fragment],env=env,capture_output=True,text=True)
                    self.assertEqual(result.returncode==0,accepted,result.stderr)

    def test_old_workflow_basename_is_rejected_for_fresh_review(self):
        script=step('Validate review body')
        fragment='fail() { exit 1; }\n'+script[script.index('tail2=$(tr -d'):script.index('# The Next step line loses')]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'body'
            path.write_text('Reviewed with: `auto-reviewer.yaml` `v0.0.3` workflow · `cr-request` `v0.0.16` skill · In `0 min 55 sec`\nNext step: `/verify -> /finalize squash`\n')
            env={**os.environ,'BODY_FILE':str(path),'VERSION':'0.0.16','WORKFLOW_IDENTITY':'auto-reviewer.yaml@v0.0.3'}
            result=subprocess.run(['bash','-c',fragment],env=env,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)

    def test_separate_code_spans_match_requested_footer(self):
        script=step('Validate review body')
        fragment='fail() { exit 1; }\n'+script[script.index('tail2=$(tr -d'):script.index('# The Next step line loses')]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'body'
            path.write_text('Reviewed with: `auto-reviewer-shared.yaml` `1a0e98e59aa772bb4c0b77d2dc4d2f9c6110e04e` workflow · `cr-request` `v0.0.16` skill · In `0 min 55 sec` · `claude-sonnet-5-5` `medium` · `289,723 tokens` · `$0.3138`\nNext step: `/verify -> /finalize squash`\n')
            env={**os.environ,'BODY_FILE':str(path),'VERSION':'0.0.16','WORKFLOW_IDENTITY':'auto-reviewer-shared.yaml@1a0e98e59aa772bb4c0b77d2dc4d2f9c6110e04e'}
            result=subprocess.run(['bash','-c',fragment],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_signature_accepts_recorded_usage_only_in_its_format(self):
        script=step('Validate review body')
        fragment='fail() { exit 1; }\n'+script[script.index('tail2=$(tr -d'):script.index('# The Next step line loses')]
        base='Reviewed with: `auto-reviewer-shared.yaml` `v0.0.3` workflow · `cr-request` `v0.0.11` skill · In `3 min 12 sec`'
        accepts=[base,base+' · `claude-sonnet-5-5` `medium` · `132,478 tokens` · `$0.1452`',
                 base+' · `claude-fable-5-1[1m]` `high` · `1 tokens` · `$12.0000`',
                 base+' · `claude-sonnet-5-5` `low` · `0 tokens` · `$0.0000`']
        rejects=[base+' · `claude-sonnet-5-5` `medium` · `132,478 tokens` · `$0.43`',
                 base+' · `claude-sonnet-5-5` `xhigh` · `1 tokens` · `$0.1452`',
                 base+' · `claude-sonnet-5-5` `medium` · `13,24 tokens` · `$0.1452`',
                 base+' · `claude-sonnet-5-5 medium` · `132,478 tokens` · `$0.1452`',
                 base+' · `claude-sonnet-5-5` `medium` · 132,478 tokens · `$0.1452`',
                 base+' · `claude-sonnet-5-5` `medium` · `132,478 tokens` · $0.1452',
                 base+' · `claude-sonnet-5-5` `medium` · `12345 tokens` · `$0.1452`']
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'body'
            for signature in accepts+rejects:
                with self.subTest(signature=signature):
                    path.write_text(signature+'\nNext step: `/cr-received PR #16`\n')
                    env={**os.environ,'BODY_FILE':str(path),'VERSION':'0.0.11','WORKFLOW_IDENTITY':'auto-reviewer-shared.yaml@v0.0.3'}
                    result=subprocess.run(['bash','-c',fragment],env=env,capture_output=True,text=True)
                    self.assertEqual(result.returncode==0,signature in accepts,result.stderr)

class ReadinessTest(unittest.TestCase):
    def test_capitalised_and_legacy_lowercase_readiness_read_as_capitalised(self):
        presentation=PublicPresentationTest()
        for readiness,expected,state in [('Ready','Ready','success'),('Ready with fixes','Ready with fixes','success'),
                                         ('Not ready','Not ready','failure'),('ready','Ready','success'),
                                         ('ready with fixes','Ready with fixes','success'),('not ready','Not ready','failure')]:
            with self.subTest(readiness=readiness):
                result,_,_,_=presentation.render(readiness=readiness,full=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('summary=Critical 0, Important 0, Minor 0; '+expected+'\n',result.outputs)
                self.assertIn('state='+state+'\n',result.outputs)
        result,body,_,_=presentation.render(readiness='Almost',full=True)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(body,'')

    def test_summary_row_counts_and_strict_grammar(self):
        presentation=PublicPresentationTest()
        result,_,_,_=presentation.render(row='| FINDINGS | PASS | UNASSESSED | 1 | 2 | 3 |',full=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('summary=Critical 1, Important 2, Minor 3; Ready\n',result.outputs)
        self.assertIn('state=failure\n',result.outputs)
        for row in ('| PASS | PASS | OK | 0 | 0 | 0 |','| PASS | PASS | PASS | 0 | 0 | x |','| PASS | PASS | PASS | 0 | 0 |'):
            with self.subTest(row=row):
                result,body,_,_=presentation.render(row=row,full=True)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(body,'')
                self.assertNotIn('ready_to_post',result.outputs)
                self.assertIn('state=failure',result.statuses)
                self.assertIn('description=The review has no valid Summary table',result.statuses)

    def test_prompt_and_state_use_capitalised_readiness(self):
        text=WORKFLOW.read_text(encoding='utf-8')
        self.assertIn('**Readiness:** Ready, Ready with fixes, or Not ready',text)
        self.assertIn('[ "$readiness" = "Not ready" ]',text)
        self.assertIn('Critical 0, Important 0, Minor 0; Ready',text)

    def test_default_round_limit_is_ten(self):
        gate = GateTest()
        for count, allowed in ((9, True), (10, False)):
            with self.subTest(count=count):
                result, output = gate.run_gate(gate.reviews(count, 0))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'review={str(allowed).lower()}', output)

class WorkflowIdentityTest(unittest.TestCase):
    def test_callee_tag_branch_and_sha_are_resolved_without_a_version_constant(self):
        for ref,expected in [('refs/tags/v0.0.1','v0.0.1'),('refs/tags/v0.0.3','v0.0.3'),
                             ('refs/heads/main','main'),('a'*40,'a'*40),('refs/tags/'+'a'*256,'a'*256)]:
            with self.subTest(ref=ref), tempfile.TemporaryDirectory() as tmp:
                output=Path(tmp)/'output'
                env={**os.environ,'WORKFLOW_REF':'sentimony/github-workflows/.github/workflows/auto-reviewer-shared.yaml@'+ref,
                     'GITHUB_OUTPUT':str(output)}
                result=subprocess.run(['bash','-eo','pipefail','-c',step('Resolve workflow identity')],env=env,capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(output.read_text(),'identity=auto-reviewer-shared.yaml@'+expected+'\nref='+expected+'\n')

    def test_missing_or_unsafe_identity_fails_without_inventing_a_version(self):
        for ref in ['owner/repo/.github/workflows/auto-reviewer.yaml@v0.0.3', '', 'owner/repo/.github/workflows/caller.yaml@refs/tags/v0.0.3',
                    'owner/repo/.github/workflows/auto-reviewer-shared.yaml@refs/tags/bad`ref',
                    'owner/repo/.github/workflows/auto-reviewer-shared.yaml@v0.0.3@bad',
                    'owner/repo/.github/workflows/auto-reviewer-shared.yaml@refs/tags/'+'a'*257]:
            with self.subTest(ref=ref), tempfile.TemporaryDirectory() as tmp:
                output=Path(tmp)/'output'
                env={**os.environ,'WORKFLOW_REF':ref,'GITHUB_OUTPUT':str(output)}
                result=subprocess.run(['bash','-eo','pipefail','-c',step('Resolve workflow identity')],env=env,capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
                self.assertFalse(output.exists())

class UsageRecordTest(unittest.TestCase):
    def run_duration(self, execution, model='claude-sonnet-5-5', effort='medium', raw=None, version='0.0.11', elapsed=125, workflow_ref='v0.0.3'):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'.auto-review').mkdir()
            sig=f'Reviewed with: `auto-reviewer-shared.yaml` `{workflow_ref}` workflow · `cr-request` `v{version}` skill'
            (root/'.auto-review/review.md').write_text('<!-- m -->\nbody\n\n'+sig+'\nNext step: `/cr-received PR #1`\n')
            env={**os.environ,'START':'0','SIGNATURE':sig,'SEP':' · ','USAGE_MODEL':model,'USAGE_EFFORT':effort}
            if execution is not None:
                (root/'exec.json').write_text(raw if raw is not None else json.dumps(execution)); env['EXEC_FILE']=str(root/'exec.json')
            run=step('Add review duration').replace('elapsed=$(( $(date +%s) - START ))',f'elapsed={elapsed}')
            result=subprocess.run(['bash','-eo','pipefail','-c',run],cwd=root,env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            return (root/'.auto-review/review.md').read_text().splitlines()[-2]

    def test_usage_is_appended_to_the_signature(self):
        result={'type':'result','total_cost_usd':0.43215,'usage':{'input_tokens':100,'output_tokens':20,'cache_creation_input_tokens':3,'cache_read_input_tokens':900},'modelUsage':{}}
        line=self.run_duration([{'type':'system'},result])
        self.assertEqual(line,'Reviewed with: `auto-reviewer-shared.yaml` `v0.0.3` workflow · `cr-request` `v0.0.11` skill · In `2 min 5 sec` · `claude-sonnet-5-5` `medium` · `1,023 tokens` · `$0.4321`')

    def test_exact_requested_footer_example(self):
        usage={'type':'result','total_cost_usd':0.3138,'usage':{'input_tokens':289723}}
        line=self.run_duration([usage], version='0.0.16', elapsed=55,
                               workflow_ref='1a0e98e59aa772bb4c0b77d2dc4d2f9c6110e04e')
        self.assertEqual(line,'Reviewed with: `auto-reviewer-shared.yaml` `1a0e98e59aa772bb4c0b77d2dc4d2f9c6110e04e` workflow · `cr-request` `v0.0.16` skill · In `0 min 55 sec` · `claude-sonnet-5-5` `medium` · `289,723 tokens` · `$0.3138`')
        workflow=WORKFLOW.read_text()
        self.assertEqual(workflow.count('Reviewed with: `auto-reviewer-shared.yaml` `${{ steps.workflow_identity.outputs.ref }}` workflow · `cr-request` `v${{ steps.skill.outputs.version }}` skill'),2)
        self.assertIn('Next step: `/verify -> /finalize squash`',workflow)

    def test_missing_or_unreadable_usage_leaves_duration_only(self):
        duration='Reviewed with: `auto-reviewer-shared.yaml` `v0.0.3` workflow · `cr-request` `v0.0.11` skill · In `2 min 5 sec`'
        self.assertEqual(self.run_duration(None),duration)
        self.assertEqual(self.run_duration([],raw='not json'),duration)
        self.assertEqual(self.run_duration([{'type':'system'}]),duration)
        self.assertEqual(self.run_duration([{'type':'result','usage':{}}]),duration)

class TokenFilterTest(unittest.TestCase):
    def test_atlassian_prefix_is_blocked_in_every_transport(self):
        script=step('Validate review body')
        start=script.index("if grep -qE '")
        end=script.index('summary="Critical',start)
        fragment='fail() { exit 1; }\n'+script[start:end]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            paths={name:root/name for name in ('POST_FILE','COMMENTS_FILE','ACTIONS_FILE')}
            env={**os.environ,**{name:str(path) for name,path in paths.items()}}
            for name in paths:
                with self.subTest(transport=name):
                    for field,path in paths.items(): path.write_text('safe synthetic text' if field=='POST_FILE' else '{}')
                    paths[name].write_text('ATATT'+'SyntheticFixtureOnly123')
                    result=subprocess.run(['bash','-c',fragment],env=env,capture_output=True,text=True)
                    self.assertNotEqual(result.returncode,0,result.stderr)
            for field,path in paths.items(): path.write_text('safe synthetic text' if field=='POST_FILE' else '{}')
            result=subprocess.run(['bash','-c',fragment],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)


class PythonIsolationTest(unittest.TestCase):
    def test_prepare_imports_ignore_checkout_and_pythonpath(self):
        script=step('Prepare review threads')
        snippet='\n'.join(script.splitlines()[:2])+'\nPY\n'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'json.py').write_text("from pathlib import Path\nPath('imported').touch()\nraise RuntimeError('checkout import')\n")
            result=subprocess.run(['bash','-c',snippet],cwd=root,
                env={**os.environ,'PYTHONPATH':str(root),'GH_TOKEN':'synthetic-fixture'},capture_output=True,text=True)
            self.assertFalse((root/'imported').exists(),'checkout Python module executed')
            self.assertEqual(result.returncode,0,result.stderr)

    def test_every_inline_python_uses_isolated_mode(self):
        import re
        lines=[line.strip() for line in WORKFLOW.read_text().splitlines() if re.match(r'\s*python3\s',line)]
        self.assertGreater(len(lines),5)
        self.assertTrue(all(line.startswith('python3 -I ') for line in lines),lines)


class DecodedTokenFilterTest(unittest.TestCase):
    def test_escaped_prefixes_and_malformed_json_are_rejected(self):
        script=step('Validate review body')
        start=script.index("if grep -qE '")
        end=script.index('summary="Critical',start)
        fragment='fail() { exit 1; }\n'+script[start:end]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            paths={name:root/name for name in ('POST_FILE','COMMENTS_FILE','ACTIONS_FILE')}
            env={**os.environ,**{name:str(path) for name,path in paths.items()}}
            cases=[('COMMENTS_FILE',r'[{"body":"\u0041TATTSyntheticFixtureOnly123"}]'),
                   ('ACTIONS_FILE',r'{"reason":"\u0041TATTSyntheticFixtureOnly123"}'),
                   ('ACTIONS_FILE',r'{"nested":[{"reason":"\u0067hp_SyntheticFixtureOnly123"}]}'),
                   ('ACTIONS_FILE','{"reason":')]
            for name,value in cases:
                with self.subTest(transport=name,value=value):
                    paths['POST_FILE'].write_text('safe synthetic text')
                    paths['COMMENTS_FILE'].write_text('[]'); paths['ACTIONS_FILE'].write_text('{}')
                    paths[name].write_text(value)
                    result=subprocess.run(['bash','-c',fragment],env=env,capture_output=True,text=True)
                    self.assertNotEqual(result.returncode,0,result.stderr)


class SummaryRefreshTest(unittest.TestCase):
    @staticmethod
    def workflow():
        ruby=os.environ.get('RUBY','ruby')
        return json.loads(subprocess.check_output([ruby,'-e',"require 'yaml'; require 'json'; puts JSON.generate(YAML.load_file(ARGV[0], aliases: true))",str(WORKFLOW)],text=True))

    def test_refresh_runs_after_completion_with_shared_summary_lock(self):
        jobs=self.workflow()['jobs']
        self.assertIn('refresh_merged_summary',jobs)
        refresh=jobs['refresh_merged_summary']; closed=jobs['merged_summary']
        self.assertIn('complete',refresh['needs'])
        self.assertIn('always()',refresh['if'])
        self.assertIn("needs.gate.outputs.review == 'true'",refresh['if'])
        self.assertEqual(refresh['permissions'],closed['permissions'])
        for job in (refresh,closed):
            self.assertTrue(job['concurrency']['group'].startswith('claude-review-summary-pr-'))
            self.assertFalse(job['concurrency']['cancel-in-progress'])
            self.assertTrue(all(s.get('if')=='always()' for s in job['steps']))
        self.assertEqual(refresh['steps'],closed['steps'])

    def test_late_review_refreshes_empty_merge_snapshot_without_duplicates(self):
        jobs=self.workflow()['jobs']; self.assertIn('refresh_merged_summary',jobs)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            os.symlink(ROOT/'tests/fixtures/auto-reviewer-shared/gh_summary',root/'gh')
            state={'merged':True,'reviews':[],'comments':[],'threads':[]}
            (root/'state.json').write_text(json.dumps(state))
            env={**os.environ,'PATH':str(root)+os.pathsep+os.environ['PATH'],
                 'FIXTURE_DIR':str(root),'REPO':'owner/repo','NUMBER':'42'}
            def invoke(job):
                for item in jobs[job]['steps']:
                    result=subprocess.run(['bash','-eo','pipefail','-c',step(item['name'])],cwd=root,env=env,capture_output=True,text=True)
                    self.assertEqual(result.returncode,0,result.stderr)
            invoke('merged_summary')
            state=json.loads((root/'state.json').read_text()); self.assertEqual(state['comments'],[])
            state['reviews']=[SummaryRunTest.review()]
            (root/'state.json').write_text(json.dumps(state))
            invoke('refresh_merged_summary'); invoke('merged_summary')
            state=json.loads((root/'state.json').read_text())
            self.assertEqual(len(state['comments']),1)
            self.assertEqual(sum(row['Review'].isdigit() for row in summary_table(state['comments'][0]['body'],'Review')),1)

    def test_unmerged_completion_skips_summary_publication(self):
        job=self.workflow()['jobs']['refresh_merged_summary']
        self.assertEqual(job['env']['SUMMARY_REFRESH'],'true')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            os.symlink(ROOT/'tests/fixtures/auto-reviewer-shared/gh_summary',root/'gh')
            (root/'state.json').write_text(json.dumps({'merged':False,'reviews':[],'comments':[]}))
            env={**os.environ,'PATH':str(root)+os.pathsep+os.environ['PATH'],
                 'FIXTURE_DIR':str(root),'REPO':'owner/repo','NUMBER':'42',
                 'SUMMARY_REFRESH':job['env']['SUMMARY_REFRESH']}
            for item in job['steps']:
                result=subprocess.run(['bash','-eo','pipefail','-c',step(item['name'])],cwd=root,env=env,capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
            state=json.loads((root/'state.json').read_text())
            self.assertEqual(state['comments'],[])
            self.assertFalse((root/'summary.txt').exists())


class SummaryParserTest(unittest.TestCase):
    def test_gate_understands_all_three_summary_layouts(self):
        script=step('Review gate decision')
        start=script.index("blocking=$(awk -F'|'")
        end=script.index('# Count unresolved prior blockers',start)
        fragment=script[start:end]+'\nprintf "%s" "$blocking"\n'
        cases=[('| Intent | System | Craft | Critical | Important | Minor |\n| --- | --- | --- | --- | --- | --- |\n| PASS | FINDINGS | PASS | 1 | 2 | 9 |',3),
               ('| Critical | Important | Minor |\n| --- | --- | --- |\n| 2 | 4 | 8 |',6),
               ('| Severity | Count |\n| --- | --- |\n| Critical | 3 |\n| Important | 5 |\n| Minor | 6 |',8)]
        for table,expected in cases:
            with self.subTest(table=table):
                p=subprocess.run(['bash','-c',fragment],env={**os.environ,'last_summary':table},capture_output=True,text=True)
                self.assertEqual(p.returncode,0,p.stderr)
                self.assertEqual(int(p.stdout),expected)

class GateTest(unittest.TestCase):
    def run_gate(self, reviews=(), labels=(), action='', event='pull_request', requested=False, ci='success', jobs=None, author='author', source_events=None, runs=None, required_jobs=None, skippable_jobs=None, event_pr='42', ci_job=None, max_rounds='', total_override=None):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            pr={'head':{'sha':'a'*40,'repo':{'full_name':'owner/repo'}}, 'base':{'sha':'b'*40},
                'state':'open','draft':False,'user':{'type':'User','login':author},
                'labels':[{'name':x} for x in labels], 'requested_reviewers':[{'login':'so-upstars'}] if requested else []}
            source_events = ADAPTER['events'] if source_events is None else source_events
            required_jobs = ADAPTER['jobs'] if required_jobs is None else required_jobs
            skippable_jobs = ADAPTER.get('skippable_jobs', []) if skippable_jobs is None else skippable_jobs
            if runs is None:
                runs = [] if ci == 'none' else [{'id':900,'event':source_events[0],
                    'status':'completed' if ci in ('success','failure') else ci,
                    'conclusion':ci if ci in ('success','failure') else None}]
            (root/'data.json').write_text(json.dumps({'pr':pr,'reviews':reviews,'runs':runs,
                'jobs':jobs if jobs is not None else [{'name':n,'conclusion':'success'} for n in required_jobs]}))
            fake=root/'gh'
            fake.write_text('''#!/usr/bin/env python3
import json, os, subprocess, sys
args=sys.argv[1:]
open(os.environ['API_MARKER'], 'w').close()
d=json.load(open(os.environ['DATA']))
endpoint=next((x for x in args if x.startswith('repos/')), '')
if '/statuses/' in endpoint: sys.exit(0)
if '/commits/' in endpoint and '/status?' in endpoint: print('false'); sys.exit(0)
if '/actions/workflows/ci.yml/runs?' in endpoint:
    if 'head_sha='+'a'*40 not in endpoint: sys.exit(2)
    source=json.dumps({'workflow_runs':d['runs']})
    result=subprocess.run(['jq','-r',args[args.index('--jq')+1]],input=source,text=True,capture_output=True)
    sys.stdout.write(result.stdout); sys.stderr.write(result.stderr); sys.exit(result.returncode)
if '/actions/runs/900/jobs?' in endpoint:
    for item in d['jobs']: print(json.dumps(item))
    sys.exit(0)
if endpoint.endswith('/reviews'):
    source=json.dumps(d['reviews'])
    result=subprocess.run(['jq','-r',args[args.index('--jq')+1]],input=source,text=True,capture_output=True)
    sys.stdout.write(result.stdout); sys.stderr.write(result.stderr); sys.exit(result.returncode)
if endpoint.endswith('/pulls/42'): print(json.dumps(d['pr'])); sys.exit(0)
sys.exit(2)
''')
            fake.chmod(0o755)
            env={**os.environ,'PATH':str(root)+os.pathsep+os.environ['PATH'],'DATA':str(root/'data.json'),
                 'REPO':'owner/repo','REVIEWER':'so-upstars','RUN_SHA':'','RUN_HEAD_REPO':'',
                 'API_MARKER':str(root/'api-called'),
                 'REQUIRED_CI_JOB': required_jobs[0] if ci_job is None else ci_job,
                 'CLAUDE_REVIEW_MAX_ROUNDS':max_rounds,'CI_EVENTS':json.dumps(source_events),
                 'SKIPPABLE_JOBS':json.dumps(skippable_jobs),
                 'EVENT_PR':event_pr,'EVENT_NAME':event,'EVENT_ACTION':action,'CONTEXT':'Auto Reviewer',
                 'RUN_URL':'url','GITHUB_OUTPUT':str(root/'output'),'GITHUB_STEP_SUMMARY':str(root/'summary')}
            source=step('Review gate decision')
            if total_override is not None:
                needle='total=$(jq -s length <<< "$reviews")'
                assert source.count(needle) == 1
                source=source.replace(needle, 'total='+str(total_override), 1)
            result=subprocess.run(['bash','-eo','pipefail','-c',source],env=env,capture_output=True,text=True)
            output=(root/'output').read_text() if (root/'output').exists() else ''
            result.api_called=(root/'api-called').exists()
            return result,output

    def test_dispatch_without_pr_number_fails_clearly(self):
        result, output = self.run_gate(event="workflow_dispatch", event_pr="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Provide a PR number", result.stdout)
        self.assertNotIn("review=true", output)

    @staticmethod
    def reviews(manual, auto, *, clean=False, samehead=False):
        reviews=[{'user':{'login':'so-upstars'},'body':'manual','submitted_at':f'2026-01-01T00:00:{i:02}Z'} for i in range(manual)]
        for i in range(auto):
            sha=('a' if samehead and i==auto-1 else 'c')*40
            count=0 if clean and i==auto-1 else 1
            body=f'<!-- claude-review sha={sha} -->\n| Critical | Important | Minor |\n| --- | --- | --- |\n| {count} | 0 | 0 |'
            reviews.append({'user':{'login':'so-upstars'},'body':body,'submitted_at':f'2026-01-02T00:00:{i:02}Z'})
        return reviews

    def test_adapter_required_jobs_and_self_review_guard(self):
        required=ADAPTER['jobs']
        for name in required:
            for conclusion in ('failure','missing'):
                with self.subTest(name=name, conclusion=conclusion):
                    jobs=[{'name':n,'conclusion':conclusion if n==name else 'success'} for n in required if n!=name or conclusion!='missing']
                    result,output=self.run_gate(jobs=jobs)
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertIn('review=false',output)
                    self.assertIn('CI on ',result.stdout)
        result,output=self.run_gate(author='so-upstars')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('review=false',output)
        self.assertIn('is by so-upstars',result.stdout)

    def test_docs_only_ci_skips_require_explicit_project_allowlist(self):
        for job in ('validate', 'check'):
            for skippable, allowed in (([job], True), ([], False)):
                with self.subTest(job=job, skippable=skippable):
                    result, output = self.run_gate(required_jobs=[job],
                        jobs=[{'name':job, 'conclusion':'skipped'}], skippable_jobs=skippable)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(f'review={str(allowed).lower()}', output)
            for ci in ('failure', 'in_progress', 'none'):
                with self.subTest(job=job, ci=ci):
                    result, output = self.run_gate(required_jobs=[job], ci=ci)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('review=false', output)
            for conclusion in ('missing', 'failure', 'timed_out'):
                with self.subTest(job=job, conclusion=conclusion):
                    jobs=[] if conclusion=='missing' else [{'name':job, 'conclusion':conclusion}]
                    result, output = self.run_gate(required_jobs=[job], jobs=jobs, skippable_jobs=[job])
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('review=false', output)

    def test_strict_adapters_reject_every_skipped_required_job(self):
        for name in ('validate', 'check'):
            with self.subTest(name=name):
                result, output = self.run_gate(required_jobs=[name],
                    jobs=[{'name':name, 'conclusion':'skipped'}], skippable_jobs=[])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('review=false', output)

    def test_source_ci_event_selection_uses_newest_allowed_run(self):
        def run(event, conclusion='success'):
            return {'id':900,'event':event,'status':'completed','conclusion':conclusion}
        for allowed in (['pull_request'], ['push','workflow_dispatch']):
            for event in ('pull_request','push','workflow_dispatch','schedule'):
                with self.subTest(allowed=allowed, event=event):
                    result,output=self.run_gate(source_events=allowed,runs=[run(event)])
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertIn('review='+str(event in allowed).lower(),output)
            result,output=self.run_gate(source_events=allowed,
                runs=[run('schedule','failure'),run(allowed[-1])])
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('review=true',output)
            result,output=self.run_gate(source_events=allowed,
                runs=[run(allowed[0],'failure'),run(allowed[-1])])
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('review=false',output)

    def test_unresolved_previous_blocker_keeps_automatic_round(self):
        review=self.reviews(0,1,clean=True)[0]
        envelope={'schema_version':1,'envelope_version':1,'repository':'owner/repo','pr_number':42,
            'head_sha':'c'*40,'review_source_id':101,'published_review_id':101,'run_id':200,
            'duration_seconds':20,'findings':[],
            'previous_findings':[{'origin_review_id':99,'original_id':'R1-F1','path':'a.py',
                'severity':'Important','status':'NOT_ADDRESSED','defer_decision':'NONE','reason':'Still open','owner':None}],
            'action_progress':[],'disposition_events':[]}
        review.update(id=101,commit_id='c'*40)
        review['body']+='\n<!-- claude-review-ledger:v1 '+json.dumps(envelope)+' -->'
        result,output=self.run_gate([review])
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('review=true',output)
        finding=envelope['previous_findings'][0]
        for disposition in ('ADDRESSED','REJECT','DEFER'):
            with self.subTest(disposition=disposition):
                changed=json.loads(json.dumps(envelope))
                if disposition=='ADDRESSED': changed['previous_findings'][0]['status']='ADDRESSED'
                elif disposition=='REJECT': changed['disposition_events']=[{**finding,'disposition':'REJECT'}]
                else:
                    changed['previous_findings'][0]['defer_decision']='ACCEPTED'
                    changed['action_progress']=[{**finding,**{k:{'state':'SUCCEEDED'} for k in ('issue','reply','resolve')}}]
                review['body']=self.reviews(0,1,clean=True)[0]['body']+'\n<!-- claude-review-ledger:v1 '+json.dumps(changed)+' -->'
                result,output=self.run_gate([review])
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('review=false',output)

    def test_limits_and_manual_counts(self):
        for manual,auto,limit,allow in [(0,4,5,True),(0,5,5,False),(1,3,5,True),(2,3,5,False),(0,1,1,False),(0,9,10,True),(0,10,10,False)]:
            with self.subTest(manual=manual,auto=auto,limit=limit):
                labels=[] if limit==10 else [f'auto-review-rounds-{limit}']
                result,output=self.run_gate(self.reviews(manual,auto),labels)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn(f'review={str(allow).lower()}',output)
                if not allow:
                    self.assertRegex(result.stdout, rf'\b{manual+auto} reviews from so-upstars\b')
                    self.assertRegex(result.stdout, rf'\blimit {limit}\b')
                    self.assertTrue('auto-review-rounds-' in result.stdout or 'request so-upstars' in result.stdout)

    def test_configured_limit_controls_automatic_rounds(self):
        for job in ('validate', 'check'):
            for count, allowed in ((2, True), (3, False)):
                with self.subTest(job=job, count=count):
                    result, output = self.run_gate(self.reviews(count, 0), ci_job=job,
                        required_jobs=[job], max_rounds='3')
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(f'review={str(allowed).lower()}', output)

    def test_round_limit_configuration(self):
        for max_rounds, labels, count, allowed in [('', [], 10, False),
                ('3', ['auto-review-rounds-12'], 3, True),
                ('0003', [], 3, False), ('3', ['auto-review-rounds-00012'], 3, True)]:
            with self.subTest(max_rounds=max_rounds, labels=labels):
                result, output = self.run_gate(self.reviews(count, 0), labels, max_rounds=max_rounds)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'review={str(allowed).lower()}', output)

    def test_manual_triggers_bypass_reached_custom_limit_but_not_guards(self):
        for kwargs in ({'action':'review_requested'}, {'requested':True},
                       {'event':'workflow_dispatch'}):
            for guard, allowed in (({}, True), ({'labels':['skip-auto-review']}, False),
                                   ({'ci':'failure'}, False)):
                with self.subTest(trigger=kwargs, guard=guard):
                    result, output = self.run_gate(self.reviews(3, 0), max_rounds='3',
                                                   **kwargs, **guard)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(f'review={str(allowed).lower()}', output)

    def test_invalid_round_variable_fails_before_overrides_and_bypasses(self):
        for value in ('0', '-1', '1.5', 'x', ' ', '+5', '000'):
            for kwargs in ({}, {'labels':['auto-review-rounds-12']},
                           {'action':'review_requested', 'requested':True},
                           {'event':'workflow_dispatch'}):
                with self.subTest(value=value, kwargs=kwargs):
                    result, output = self.run_gate(max_rounds=value, **kwargs)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn('review=true', output)

    def test_round_limits_compare_large_decimals_exactly(self):
        for limit in ('9007199254740993', '9' * 100):
            for total, allowed in ((str(int(limit)-1), True), (limit, False)):
                with self.subTest(limit=limit, total=total):
                    result, output = self.run_gate(max_rounds=limit, total_override=total)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(f'review={str(allowed).lower()}', output)

    def test_invalid_ci_job_fails_before_api(self):
        for job in ('', 'a b', '"x"', '["validate"]', 'a","b', '1job', '-job', 'x\ny', 'x\n'):
            with self.subTest(job=job):
                result, output = self.run_gate(ci_job=job)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('review=true', output)
                self.assertIn('error', result.stdout.lower())
                self.assertFalse(result.api_called)

    def test_manual_only_and_latest_usable_summary(self):
        # Manual reviews count even with no marked summary; another author does not count.
        others={'user':{'login':'someone'},'body':'manual','submitted_at':'2026-02-01T00:00:00Z'}
        result,output=self.run_gate(self.reviews(5,0)+[others],['auto-review-rounds-5'])
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('review=false',output)
        self.assertIn('5 reviews from so-upstars; limit 5',result.stdout)
        reviews=self.reviews(0,1,clean=True)+self.reviews(1,0)
        reviews[-1]['submitted_at']='2026-03-01T00:00:00Z'
        result,output=self.run_gate(reviews)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('review=false',output)
        self.assertIn('no Critical or Important findings',result.stdout)

    def test_invalid_labels_fail_closed_even_on_request(self):
        for labels in [['auto-review-rounds-0'],['auto-review-rounds-000'],['auto-review-rounds--1'],['auto-review-rounds-x'],['auto-review-rounds-1','auto-review-rounds-10']]:
            with self.subTest(labels=labels):
                result,output=self.run_gate(labels=labels,action='review_requested')
                self.assertNotEqual(result.returncode,0)
                self.assertIn('error',result.stdout.lower())
                self.assertNotIn('review=true',output)

    def test_existing_guards_and_bypasses(self):
        for kwargs,allow in [({'labels':['skip-auto-review'],'action':'review_requested'},False),
                             ({'reviews':self.reviews(0,1,clean=True),'labels':['auto-review-rounds-10']},False),
                             ({'reviews':self.reviews(0,1,samehead=True)},False),
                             ({'reviews':self.reviews(2,3),'action':'review_requested'},True),
                             ({'reviews':self.reviews(2,3),'event':'workflow_dispatch'},True),
                             ({'reviews':self.reviews(2,3),'requested':True},True),
                             ({'action':'review_requested','ci':'failure'},False)]:
            with self.subTest(kwargs=kwargs):
                result,output=self.run_gate(**kwargs)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn(f'review={str(allow).lower()}',output)

    def test_workflow_settings(self):
        ruby=os.environ.get('RUBY','ruby')
        workflow=json.loads(subprocess.check_output([ruby,'-e',"require 'yaml'; require 'json'; puts JSON.generate(YAML.load_file(ARGV[0], aliases: true))",str(WORKFLOW)],text=True))
        self.assertEqual(workflow['name'],ADAPTER['name'])
        triggers=workflow.get('on',workflow.get('true',{}))
        self.assertEqual(set(triggers), {'workflow_call'})
        call=triggers['workflow_call']
        self.assertEqual(set(call['inputs']), {'required-ci-job', 'pr'})
        self.assertEqual(call['inputs']['required-ci-job']['type'], 'string')
        self.assertTrue(call['inputs']['required-ci-job']['required'])
        for name in ('pr',):
            self.assertEqual(call['inputs'][name]['type'], 'string')
            self.assertFalse(call['inputs'][name]['required'])
            self.assertEqual(call['inputs'][name]['default'], '')
        self.assertEqual(set(call['secrets']), {'AUTO_REVIEWER_YNGWALD', 'CLAUDE_CODE_OAUTH_TOKEN'})
        self.assertTrue(all(x['required'] for x in call['secrets'].values()))
        gate=next(s for s in workflow['jobs']['gate']['steps'] if s.get('id')=='gate')
        self.assertEqual(gate['env']['REVIEWER'],ADAPTER['reviewer'])
        self.assertEqual(gate['env']['REQUIRED_CI_JOB'], '${{ inputs.required-ci-job }}')
        self.assertEqual(gate['env']['CLAUDE_REVIEW_MAX_ROUNDS'], '${{ vars.CLAUDE_REVIEW_MAX_ROUNDS }}')
        self.assertNotIn('REQUIRED_JOBS', gate['env'])
        self.assertEqual(json.loads(gate['env']['CI_EVENTS']),ADAPTER['events'])
        self.assertEqual(json.loads(gate['env']['SKIPPABLE_JOBS']),ADAPTER['skippable_jobs'])
        self.assertIn('inputs.'+ADAPTER['dispatch'],gate['env']['EVENT_PR'])
        self.assertNotIn('inputs.pr_number',gate['env']['EVENT_PR'])
        self.assertIn('inputs.'+ADAPTER['dispatch'],workflow['concurrency']['group'])
        self.assertNotIn('inputs.pr_number',workflow['concurrency']['group'])
        self.assertEqual(workflow['jobs']['publish']['env']['REVIEWER'],ADAPTER['reviewer'])
        self.assertEqual(workflow['jobs']['complete']['env']['REVIEWER'],ADAPTER['reviewer'])
        secrets=[s.get('env',{}).get('GH_TOKEN','') for job in workflow['jobs'].values()
                 for s in job.get('steps',[]) if 'secrets.' in s.get('env',{}).get('GH_TOKEN','')]
        self.assertTrue(secrets)
        self.assertEqual(set(secrets),{'${{ secrets.'+ADAPTER['secret']+' }}'})
        review=workflow['jobs']['review']
        agent=next(s for s in review['steps'] if s.get('id')=='claude')
        self.assertIn('--model ${{ steps.model.outputs.model }}',agent['with']['claude_args'])
        self.assertIn('--effort ${{ steps.model.outputs.effort }}',agent['with']['claude_args'])
        resolve=next(s for s in review['steps'] if s.get('id')=='model')
        self.assertEqual(resolve['env']['MODEL'],"${{ vars.CLAUDE_REVIEW_MODEL || 'claude-sonnet-5-5' }}")
        self.assertEqual(resolve['env']['EFFORT'],"${{ vars.CLAUDE_REVIEW_EFFORT || 'medium' }}")
        self.assertEqual(workflow['permissions'],{})
        self.assertEqual(review['permissions'],{'contents':'read','pull-requests':'read'})
        self.assertTrue(workflow['concurrency']['cancel-in-progress'])
        self.assertTrue(review['concurrency']['cancel-in-progress'])
        triggers=workflow.get('on',workflow.get('true',{}))
        self.assertIn('CI (${{ inputs.required-ci-job }}) is green', agent['with']['prompt'])
        self.assertNotIn('CI (validate) is green', agent['with']['prompt'])
        install=step('Install cr-request skill')
        self.assertIn('REVIEW_SKILLS_REF',install)
        self.assertIn('v[0-9]',install)

class MergedSummaryTest(unittest.TestCase):
    def test_summary_job_is_isolated_and_closed_only(self):
        ruby = os.environ.get('RUBY', 'ruby')
        workflow = json.loads(subprocess.check_output([ruby, '-e', "require 'yaml'; require 'json'; puts JSON.generate(YAML.load_file(ARGV[0], aliases: true))", str(WORKFLOW)], text=True))
        job = workflow['jobs']['merged_summary']
        self.assertIn('closed', job['if'])
        self.assertIn('merged', job['if'])
        self.assertFalse(any('checkout' in str(s.get('uses', '')) for s in job['steps']))
        self.assertIn("github.event.action != 'closed'", workflow['jobs']['gate']['if'])
        self.assertIn('closed', workflow['concurrency']['group'])
        self.assertIn('Gather merged review summary', [s.get('name') for s in job['steps']])
        self.assertIn('Publish merged review summary', [s.get('name') for s in job['steps']])

class SummaryRunTest(unittest.TestCase):
    def run_summary(self, state, repeat=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            os.symlink(ROOT/'tests/fixtures/auto-reviewer-shared/gh_summary',root/'gh')
            (root/'state.json').write_text(json.dumps(state))
            env={**os.environ,'PATH':str(root)+os.pathsep+os.environ['PATH'], 'FIXTURE_DIR':str(root),'REPO':'owner/repo','NUMBER':'42'}
            def invoke(name):
                return subprocess.run(['bash','-eo','pipefail','-c',step(name)],cwd=root,env=env,capture_output=True,text=True)
            gather=invoke('Gather merged review summary')
            self.assertEqual(gather.returncode,0,gather.stderr)
            publish=invoke('Publish merged review summary')
            self.assertEqual(publish.returncode,0,publish.stderr)
            if repeat:
                self.assertEqual(invoke('Gather merged review summary').returncode,0)
                self.assertEqual(invoke('Publish merged review summary').returncode,0)
            return json.loads((root/'state.json').read_text()), [json.loads(x) for x in (root/'trace.jsonl').read_text().splitlines()]

    @staticmethod
    def review(ident=101, duration=20):
        finding={'origin_review_id':ident,'original_id':'R1-F1','path':'a.py','severity':'Important'}
        d={'schema_version':1,'repository':'owner/repo','pr_number':42,'head_sha':'a'*40,'review_source_id':ident,'findings':[finding],'previous_findings':[], 'envelope_version':1,'published_review_id':ident,'run_id':200,'duration_seconds':duration,'action_progress':[],'disposition_events':[]}
        return {'id':ident,'user':{'login':'so-upstars'},'commit_id':'a'*40,'body':'<!-- claude-review-ledger:v1 '+json.dumps(d)+' -->'}

    @staticmethod
    def with_signature(review, line):
        return {**review,'body':review['body']+'\n\n'+line+'\nNext step: none'}

    def test_approved_summary_layout_and_grouped_tokens(self):
        prefix='Reviewed with: `cr-request@v0.0.16` skill · In '
        first=self.with_signature(self.review(101,39),prefix+'0 min 39 sec · `claude-sonnet-5-5 medium` · 132,478 tokens · $0.1452')
        second=self.with_signature(self.review(102,51),prefix+'0 min 51 sec · `claude-sonnet-5-5 medium` · 212,265 tokens · $0.2264')
        state,_=self.run_summary({'reviews':[first,second]})
        body=state['comments'][0]['body']
        self.assertIn('| Critical | Important | Minor | Fixed | Rejected | Deferred | Undetermined |',body)
        self.assertNotIn('| Reviews |',body)
        self.assertNotIn('| Findings |',body)
        self.assertNotIn('| Duration total |',body)
        self.assertIn('| Review | Model | Effort | Duration | Tokens | Cost |',body)
        self.assertIn('| 1 | claude-sonnet-5-5 | medium | 39 sec | 132,478 | $0.15 |',body)
        self.assertIn('| 2 | claude-sonnet-5-5 | medium | 51 sec | 212,265 | $0.23 |',body)
        self.assertIn('| **Total** | | | **90 sec** | **344,743** | **$0.37** |',body)
        self.assertIn('| Mean | | | 45.00 sec | | |',body)
        self.assertIn('| Measured | | | 2/2 | 2/2 | 2/2 |',body)

    def test_approved_summary_keeps_missing_duration_aligned_with_its_review(self):
        first=self.review(101,None)
        second=self.with_signature(self.review(102,0),
            'Reviewed with: `cr-request@v0.0.16` skill · In 0 min 0 sec · `claude-sonnet-5-5 medium` · 0 tokens · $0.0000')
        state,_=self.run_summary({'reviews':[first,second,self.review(103,7)]})
        body=state['comments'][0]['body']
        self.assertIn('| 1 | unknown | unknown | unknown | unknown | unknown |',body)
        self.assertIn('| 2 | claude-sonnet-5-5 | medium | 0 sec | 0 | $0.00 |',body)
        self.assertIn('| **Total** | | | **7 sec** | **0** | **$0.00** |',body)
        self.assertIn('| Mean | | | 3.50 sec | | |',body)
        self.assertIn('| Measured | | | 2/3 | 1/3 | 1/3 |',body)
        self.assertIn('| 3 | unknown | unknown | 7 sec | unknown | unknown |',body)

    def test_split_code_footer_and_older_formats_share_summary(self):
        split='Reviewed with: `auto-reviewer.yaml` `1a0e98e59aa772bb4c0b77d2dc4d2f9c6110e04e` workflow · `cr-request` `v0.0.16` skill · In `0 min 55 sec` · `claude-sonnet-5-5` `medium` · `289,723 tokens` · `$0.3138`'
        combined='Reviewed with: `auto-reviewer.yaml@v0.0.3` workflow · `cr-request@v0.0.16` skill · In 0 min 39 sec · `claude-sonnet-5-5 medium` · 132,478 tokens · $0.1452'
        legacy='Reviewed with: `/cr-request` 0.0.11 skill · In 0 min 20 sec · claude-sonnet-5-5 medium · 1000 tokens · $0.2500'
        reviews=[self.with_signature({'id':n,'user':{'login':'so-upstars'},'body':''},line)
                 for n,line in enumerate((split,combined,legacy),101)]
        state,_=self.run_summary({'reviews':reviews})
        body=state['comments'][0]['body']
        self.assertIn('| 1 | claude-sonnet-5-5 | medium | 55 sec | 289,723 | $0.31 |',body)
        self.assertIn('| **Total** | | | **114 sec** | **423,201** | **$0.71** |',body)
        self.assertIn('| Mean | | | 38.00 sec | | |',body)
        self.assertIn('| Measured | | | 3/3 | 3/3 | 3/3 |',body)

    def test_shared_workflow_and_old_workflow_footers_share_history(self):
        old='Reviewed with: `auto-reviewer.yaml` `v0.0.3` workflow · `cr-request` `v0.0.16` skill · In `0 min 55 sec` · `claude-sonnet-5-5` `medium` · `289,723 tokens` · `$0.3138`'
        new=old.replace('auto-reviewer.yaml', 'auto-reviewer-shared.yaml').replace('v0.0.3', 'v0.0.4')
        reviews=[self.with_signature({'id':n,'user':{'login':'so-upstars'},'body':''},line)
                 for n,line in enumerate((old,new),101)]
        state,_=self.run_summary({'reviews':reviews})
        body=state['comments'][0]['body']
        self.assertIn('| **Total** | | | **110 sec** | **579,446** | **$0.63** |',body)
        self.assertIn('| Measured | | | 2/2 | 2/2 | 2/2 |',body)

    def test_used_duration_without_usage_keeps_zero_distinct_from_unknown(self):
        line=USED_EXAMPLE.split(' · `claude-sonnet-5-5 medium`',1)[0]+' · `0 min 0 sec`'
        review=self.with_signature({'id':101,'user':{'login':'so-upstars'},'body':''},line)
        state,_=self.run_summary({'reviews':[review]})
        body=state['comments'][0]['body']
        self.assertIn('| 1 | unknown | unknown | 0 sec | unknown | unknown |',body)
        self.assertIn('| Measured | | | 1/1 | 0/1 | 0/1 |',body)

    def test_used_footer_and_legacy_footer_share_summary_usage(self):
        legacy='Reviewed with: `cr-request@v0.0.16` skill · In 0 min 0 sec · `claude-sonnet-5-5 medium` · 0 tokens · $0.0000'
        reviews=[self.with_signature({'id':n,'user':{'login':'so-upstars'},'body':''},line)
                 for n,line in enumerate((USED_EXAMPLE,legacy),101)]
        state,_=self.run_summary({'reviews':reviews})
        body=state['comments'][0]['body']
        self.assertIn('| 1 | claude-sonnet-5-5 | medium | 49 sec | 202,629 | $0.16 |',body)
        self.assertIn('| **Total** | | | **49 sec** | **202,629** | **$0.16** |',body)
        self.assertIn('| Measured | | | 2/2 | 2/2 | 2/2 |',body)

    def test_summary_is_a_table_with_per_review_usage(self):
        base='Reviewed with: `/cr-request` 0.0.11 skill · In 0 min 20 sec'
        first=self.with_signature(self.review(101),base+' · claude-sonnet-5-5 medium · 1000 tokens · $0.2500')
        second=self.with_signature(self.review(102),base+' · claude-opus-5-5 high · 2500 tokens · $1.0049')
        old=self.with_signature(self.review(103),base)
        state,_=self.run_summary({'reviews':[first,second,old]})
        body=state['comments'][0]['body']
        self.assertEqual(summary_result(body,'Critical'),'0 + unknown')
        self.assertEqual(summary_result(body,'Important'),'3 + unknown')
        self.assertEqual(summary_result(body,'Minor'),'0 + unknown')
        self.assertIn('| 1 | claude-sonnet-5-5 | medium | 20 sec | 1,000 | $0.25 |',body)
        self.assertIn('| 2 | claude-opus-5-5 | high | 20 sec | 2,500 | $1.00 |',body)
        self.assertIn('| 3 | unknown | unknown | 20 sec | unknown | unknown |',body)
        self.assertIn('| **Total** | | | **60 sec** | **3,500** | **$1.25** |',body)
        self.assertIn('| Mean | | | 20.00 sec | | |',body)
        self.assertIn('| Measured | | | 3/3 | 2/3 | 2/3 |',body)

    def test_summary_reads_new_footer_and_legacy_usage_together(self):
        modern='Reviewed with: `auto-reviewer.yaml@v0.0.3` workflow · `cr-request@v0.0.16` skill · In 0 min 39 sec · `claude-sonnet-5-5 medium` · 132,478 tokens · $0.1452'
        legacy='Reviewed with: `/cr-request` 0.0.11 skill · In 0 min 20 sec · claude-sonnet-5-5 medium · 1000 tokens · $0.2500'
        first=self.with_signature({'id':101,'user':{'login':'so-upstars'},'body':''}, modern)
        second=self.with_signature(self.review(102), legacy)
        state,_=self.run_summary({'reviews':[first,second]})
        body=state['comments'][0]['body']
        self.assertIn('| 1 | claude-sonnet-5-5 | medium | 39 sec | 132,478 | $0.15 |',body)
        self.assertIn('| **Total** | | | **59 sec** | **133,478** | **$0.40** |',body)
        self.assertEqual(summary_usage(body,'Total','Duration'),'59 sec')

    def test_summary_without_recorded_usage_is_unknown(self):
        state,_=self.run_summary({'reviews':[self.review()]})
        body=state['comments'][0]['body']
        self.assertIn('| 1 | unknown | unknown | 20 sec | unknown | unknown |',body)
        self.assertIn('| **Total** | | | **20 sec** | **unknown** | **unknown** |',body)

    def test_no_reviews_and_legacy_duration(self):
        state,trace=self.run_summary({'reviews':[]})
        self.assertFalse(state.get('comments'))
        review={'id':1,'user':{'login':'so-upstars'},'body':'Reviewed with: `/cr-request` 1.0 skill · In 1 min 3 sec'}
        state,trace=self.run_summary({'reviews':[review]},repeat=True)
        self.assertEqual(len(state['comments']),1)
        body=state['comments'][0]['body']
        for severity in ('Critical','Important','Minor'):
            self.assertEqual(summary_result(body,severity),'0 + unknown')
        self.assertEqual(summary_usage(body,'Total','Duration'),'63 sec')
        self.assertEqual(sum(x['method']=='POST' and '/issues/42/comments' in x['endpoint'] for x in trace),1)

    def test_forged_marker_and_last_host_reply(self):
        review=self.review()
        marker={'schema_version':1,'repository':'owner/repo','pr_number':42,'head_sha':'a'*40,'origin_review_id':101,'original_id':'R1-F1','path':'a.py','disposition':'REJECT','reason':'invalid','owner':None}
        comment=lambda i,author,body,review: {'databaseId':i,'author':{'login':author},'authorAssociation':'MEMBER','body':body,'updatedAt':'2026-10-30T00:00:00Z','pullRequestReview':review}
        thread={'id':'t','path':'a.py','isResolved':False,'comments':[comment(1,'so-upstars','**Important** R1-F1 - finding',{'databaseId':101}),comment(2,'alice','<!-- cr-received-disposition:v1 '+json.dumps(marker)+' -->',None)]}
        fake={'id':88,'user':{'login':'mallory'},'body':'<!-- claude-review summary -->'}
        state,_=self.run_summary({'reviews':[review], 'threads':[thread], 'comments':[fake]},repeat=True)
        self.assertEqual(len(state['comments']),2)
        self.assertEqual(summary_result(state['comments'][1]['body'],'Rejected'),'1')
        self.assertEqual(summary_usage(state['comments'][1]['body'],'Mean','Duration'),'20.00 sec')

    def test_paginated_reviews_and_unknown_duration(self):
        reviews=[{'id':n,'user':{'login':'so-upstars'},'body':'legacy'} for n in range(1,102)]
        state,trace=self.run_summary({'reviews':reviews})
        self.assertEqual(sum(row['Review'].isdigit() for row in summary_table(state['comments'][0]['body'],'Review')),101)
        self.assertEqual(summary_usage(state['comments'][0]['body'],'Total','Duration'),'unknown')
        self.assertTrue(any('page=2' in x['endpoint'] for x in trace))

    def test_malformed_ledger_is_unknown_not_zero(self):
        review=self.review()
        review['body']=review['body'].replace('"envelope_version": 1','"envelope_version": true')
        state,_=self.run_summary({'reviews':[review]})
        body=state['comments'][0]['body']
        for severity in ('Critical','Important','Minor'):
            self.assertEqual(summary_result(body,severity),'0 + unknown')
        self.assertEqual(summary_result(body,'Rejected'),'0 + unknown')
        self.assertEqual(summary_usage(body,'Measured','Duration'),'0/1')

    def test_later_explicit_disposition_wins(self):
        review=self.review()
        def marker(disposition):
            return '<!-- cr-received-disposition:v1 '+json.dumps({'schema_version':1,'repository':'owner/repo','pr_number':42,'head_sha':'a'*40,'origin_review_id':101,'original_id':'R1-F1','path':'a.py','disposition':disposition,'reason':'reason','owner':None})+' -->'
        def c(i,body,stamp,author='alice',review=None): return {'databaseId':i,'author':{'login':author},'authorAssociation':'MEMBER','body':body,'updatedAt':stamp,'pullRequestReview':review}
        thread={'id':'t','path':'a.py','isResolved':False,'comments':[c(1,'**Important** R1-F1 - finding','2026-10-30T00:00:00Z','so-upstars',{'databaseId':101}),c(2,marker('REJECT'),'2026-10-30T00:00:01Z'),c(3,marker('ACCEPT'),'2026-10-30T00:00:02Z')]}
        state,_=self.run_summary({'reviews':[review],'threads':[thread]})
        self.assertEqual(summary_result(state['comments'][0]['body'],'Rejected'),'0')

    def test_declined_defer_does_not_erase_reject_or_duplicate_finding(self):
        origin=self.review()
        d=json.loads(origin['body'].split('v1 ',1)[1].split(' -->')[0])
        finding=d['findings'][0]
        prev={**finding,'status':'NOT_ADDRESSED','defer_decision':'DECLINED','reason':'still required','owner':None}
        d.update(head_sha='b'*40,review_source_id=202,published_review_id=202,findings=[],previous_findings=[prev],duration_seconds=40)
        carry={'id':202,'user':{'login':'so-upstars'},'commit_id':'b'*40,'submitted_at':'2026-10-30T00:00:02Z','body':'<!-- claude-review-ledger:v1 '+json.dumps(d)+' -->'}
        marker={'schema_version':1,'repository':'owner/repo','pr_number':42,'head_sha':'a'*40,'origin_review_id':101,'original_id':'R1-F1','path':'a.py','disposition':'REJECT','reason':'no defect','owner':None}
        def c(i,author,body,review): return {'databaseId':i,'author':{'login':author},'authorAssociation':'MEMBER','body':body,'updatedAt':'2026-10-30T00:00:00Z','pullRequestReview':review}
        thread={'id':'t','path':'a.py','isResolved':False,'comments':[c(1,'so-upstars','**Important** R1-F1 - finding',{'databaseId':101}),c(2,'alice','<!-- cr-received-disposition:v1 '+json.dumps(marker)+' -->',None)]}
        state,_=self.run_summary({'reviews':[origin,carry],'threads':[thread]})
        body=state['comments'][0]['body']
        self.assertEqual(sum(int(summary_result(body,k).split()[0]) for k in ('Critical','Important','Minor')),1)
        self.assertEqual(summary_result(body,'Important'),'1')
        self.assertEqual(summary_result(body,'Undetermined'),'0')
        self.assertEqual(summary_result(body,'Rejected'),'1')
        self.assertEqual(summary_usage(body,'Total','Duration'),'60 sec')
        self.assertEqual(summary_usage(body,'Mean','Duration'),'30.00 sec')

    def defer_fixture(self):
        origin=self.review()
        d=json.loads(origin['body'].split('v1 ',1)[1].split(' -->')[0]); finding=d['findings'][0]
        prev={**finding,'status':'NOT_ADDRESSED','defer_decision':'ACCEPTED','reason':'Later','owner':'alice'}
        progress={'origin_review_id':101,'original_id':'R1-F1','path':'a.py','issue':{'state':'SUCCEEDED','number':57,'url':'https://github.com/owner/repo/issues/57'},'reply':{'state':'SUCCEEDED','comment_id':3},'resolve':{'state':'SUCCEEDED','thread_id':'t'},'error':None}
        d.update(head_sha='b'*40,review_source_id=202,published_review_id=202,findings=[],previous_findings=[prev],action_progress=[progress])
        carry={'id':202,'user':{'login':'so-upstars'},'commit_id':'b'*40,'submitted_at':'2026-10-30T00:00:02Z','body':'<!-- claude-review-ledger:v1 '+json.dumps(d)+' -->'}
        payload={'schema_version':1,'repository':'owner/repo','pr_number':42,'head_sha':'b'*40,'origin_review_id':101,'original_id':'R1-F1','path':'a.py','disposition':'DEFER','reason':'Later','owner':'alice'}
        deferred='<!-- claude-review-deferred:v1 '+json.dumps({k:payload[k] for k in ('repository','pr_number','origin_review_id','original_id','path')},separators=(',',':'))+' -->'
        def c(i,author,body,review): return {'databaseId':i,'author':{'login':author},'authorAssociation':'MEMBER','body':body,'updatedAt':'2026-10-30T00:00:00Z','pullRequestReview':review}
        thread={'id':'t','path':'a.py','isResolved':True,'comments':[c(1,'so-upstars','**Important** R1-F1 - finding',{'databaseId':101}),c(2,'alice','<!-- cr-received-disposition:v1 '+json.dumps(payload)+' -->',None),c(3,'so-upstars','https://github.com/owner/repo/issues/57\n'+deferred,None)]}
        issue={'number':57,'state':'open','user':{'login':'so-upstars'},'html_url':'https://github.com/owner/repo/issues/57','body':'R1-F1 Important #discussion_r1 alice Later\n'+deferred}
        d['disposition_events']=[{'origin_review_id':101,'original_id':'R1-F1','path':'a.py',
            'disposition':'DEFER','reason':'Later','source_comment_id':2,'source_author':'alice',
            'source_updated_at':'2026-10-30T00:00:00Z'}]
        carry['body']='<!-- claude-review-ledger:v1 '+json.dumps(d)+' -->'
        return {'reviews':[origin,carry],'threads':[thread],'issue':issue}

    def test_defer_requires_all_readbacks(self):
        data=self.defer_fixture()
        thread=data['threads'][0]
        state,_=self.run_summary(data)
        self.assertEqual(summary_result(state['comments'][0]['body'],'Deferred'),'1')
        thread['isResolved']=False
        state,_=self.run_summary(data)
        self.assertEqual(summary_result(state['comments'][0]['body'],'Deferred'),'0 + unknown')

    def test_acceptance_reason_can_differ_from_proposal(self):
        data=self.defer_fixture()
        carry=data['reviews'][1]
        d=json.loads(carry['body'].split('v1 ',1)[1].split(' -->')[0])
        d['previous_findings'][0]['reason']='Reviewer confirms later work'
        carry['body']='<!-- claude-review-ledger:v1 '+json.dumps(d)+' -->'
        data['issue']['body']=data['issue']['body'].replace('alice Later','alice Reviewer confirms later work')
        state,_=self.run_summary(data)
        self.assertEqual(summary_result(state['comments'][0]['body'],'Deferred'),'1')

    def test_closed_deferred_issue_is_incomplete(self):
        data=self.defer_fixture()
        data['issue']['state']='closed'
        state,_=self.run_summary(data)
        self.assertEqual(summary_result(state['comments'][0]['body'],'Deferred'),'0 + unknown')

    def test_latest_defer_decision_controls_completion(self):
        for decision in ('DECLINED','NONE'):
            for reverse in (False,True):
                with self.subTest(decision=decision,reverse=reverse):
                    data=self.defer_fixture()
                    later=json.loads(json.dumps(data['reviews'][1]))
                    d=json.loads(later['body'].split('v1 ',1)[1].split(' -->')[0])
                    d.update(review_source_id=303,published_review_id=303,action_progress=[])
                    d['previous_findings'][0].update(defer_decision=decision,owner=None)
                    later.update(id=303,submitted_at='2026-10-30T00:00:03Z',body='<!-- claude-review-ledger:v1 '+json.dumps(d)+' -->')
                    data['reviews'].append(later)
                    if reverse: data['reviews'].reverse()
                    state,_=self.run_summary(data)
                    self.assertEqual(summary_result(state['comments'][0]['body'],'Deferred'),'0 + unknown')
                    self.assertEqual(summary_result(state['comments'][0]['body'],'Rejected'),'0 + unknown')

    def test_new_host_defer_requires_fresh_matching_acceptance(self):
        for change in ('head','reason'):
            data=self.defer_fixture()
            marker=data['threads'][0]['comments'][1]
            payload=json.loads(marker['body'].split('v1 ',1)[1].split(' -->')[0])
            payload['head_sha' if change=='head' else 'reason']='c'*40 if change=='head' else 'New reason'
            marker.update(updatedAt='2026-10-30T00:00:04Z',body='<!-- cr-received-disposition:v1 '+json.dumps(payload)+' -->')
            data['head_sha']='c'*40
            state,_=self.run_summary(data)
            self.assertEqual(summary_result(state['comments'][0]['body'],'Deferred'),'0 + unknown')

    def test_same_timestamp_defer_edits_require_matching_source_evidence(self):
        for change in ('reason','comment_id','author'):
            with self.subTest(change=change):
                data=self.defer_fixture(); marker=data['threads'][0]['comments'][1]
                if change=='reason':
                    payload=json.loads(marker['body'].split('v1 ',1)[1].split(' -->')[0])
                    payload['reason']='Different proposal in the same second'
                    marker['body']='<!-- cr-received-disposition:v1 '+json.dumps(payload)+' -->'
                elif change=='comment_id': marker['databaseId']=4
                else: marker['author']={'login':'bob'}
                state,_=self.run_summary(data)
                self.assertEqual(summary_result(state['comments'][0]['body'],'Deferred'),'0 + unknown')

    def test_thread_and_reply_pagination(self):
        review=self.review()
        marker={'schema_version':1,'repository':'owner/repo','pr_number':42,'head_sha':'a'*40,'origin_review_id':101,'original_id':'R1-F1','path':'a.py','disposition':'REJECT','reason':'invalid','owner':None}
        def c(i,author,body,review=None): return {'databaseId':i,'author':{'login':author},'authorAssociation':'MEMBER','body':body,'updatedAt':'2026-10-30T00:00:00Z','pullRequestReview':review}
        comments=[c(1,'so-upstars','**Important** R1-F1 - finding',{'databaseId':101})]+[c(i,'alice','unstructured') for i in range(2,101)]+[c(101,'alice','<!-- cr-received-disposition:v1 '+json.dumps(marker)+' -->')]
        threads=[{'id':'other'+str(i),'path':'other','isResolved':False,'comments':[]} for i in range(100)]
        threads.append({'id':'target','path':'a.py','isResolved':False,'comments':comments})
        state,trace=self.run_summary({'reviews':[review],'threads':threads})
        self.assertEqual(summary_result(state['comments'][0]['body'],'Rejected'),'1')
        self.assertGreaterEqual(sum(x['endpoint']=='graphql' for x in trace),3)

    def test_unmerged_rejected_before_write_and_existing_comment_updated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); os.symlink(ROOT/'tests/fixtures/auto-reviewer-shared/gh_summary',root/'gh')
            (root/'state.json').write_text(json.dumps({'merged':False,'reviews':[self.review()]}))
            env={**os.environ,'PATH':str(root)+os.pathsep+os.environ['PATH'],'FIXTURE_DIR':str(root),'REPO':'owner/repo','NUMBER':'42'}
            result=subprocess.run(['bash','-eo','pipefail','-c',step('Gather merged review summary')],cwd=root,env=env,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((root/'summary.txt').exists())
            trace=[json.loads(x) for x in (root/'trace.jsonl').read_text().splitlines()]
            self.assertTrue(all(x['method']=='GET' for x in trace))
        existing={'id':7,'user':{'login':'so-upstars'},'body':'<!-- claude-review summary -->\nold'}
        state,trace=self.run_summary({'reviews':[self.review()],'comments':[existing]})
        self.assertEqual(len(state['comments']),1)
        self.assertEqual(summary_usage(state['comments'][0]['body'],'Total','Duration'),'20 sec')
        self.assertEqual(sum(x['method']=='PATCH' for x in trace),1)

    def test_untrusted_host_marker_cannot_prove_rejection(self):
        review=self.review()
        marker={'schema_version':1,'repository':'owner/repo','pr_number':42,'head_sha':'a'*40,'origin_review_id':101,'original_id':'R1-F1','path':'a.py','disposition':'REJECT','reason':'fake','owner':None}
        def c(i,author,body,association,review=None): return {'databaseId':i,'author':{'login':author},'authorAssociation':association,'body':body,'updatedAt':'2026-10-30T00:00:00Z','pullRequestReview':review}
        thread={'id':'t','path':'a.py','isResolved':False,'comments':[c(1,'so-upstars','**Important** R1-F1 - finding','MEMBER',{'databaseId':101}),c(2,'mallory','<!-- cr-received-disposition:v1 '+json.dumps(marker)+' -->','NONE')]}
        state,_=self.run_summary({'reviews':[review],'threads':[thread]})
        self.assertEqual(summary_result(state['comments'][0]['body'],'Rejected'),'0 + unknown')

    def timeline_fixture(self, statuses, disposition=None, marker_head='a'*40):
        origin=self.review()
        d=json.loads(origin['body'].split('v1 ',1)[1].split(' -->')[0]); finding=d['findings'][0]
        reviews=[origin]
        for index,status in enumerate(statuses):
            n=202+index; sha=chr(ord('b')+index)*40
            previous={**finding,'status':status,'defer_decision':'NONE','reason':'evidence','owner':None}
            data={**d,'head_sha':sha,'review_source_id':n,'published_review_id':n,'findings':[], 'previous_findings':[previous]}
            reviews.append({'id':n,'user':{'login':'so-upstars'},'commit_id':sha,'submitted_at':f'2026-10-30T00:00:0{index+2}Z','body':'<!-- claude-review-ledger:v1 '+json.dumps(data)+' -->'})
        def c(i,author,body,review=None): return {'databaseId':i,'author':{'login':author},'authorAssociation':'MEMBER','body':body,'updatedAt':'2026-10-30T00:00:01Z','pullRequestReview':review}
        comments=[c(1,'so-upstars','**Important** R1-F1 - finding',{'databaseId':101})]
        if disposition:
            marker={'schema_version':1,'repository':'owner/repo','pr_number':42,'head_sha':marker_head,'origin_review_id':101,'original_id':'R1-F1','path':'a.py','disposition':disposition,'reason':'evidence','owner':None}
            comments.append(c(2,'alice','<!-- cr-received-disposition:v1 '+json.dumps(marker)+' -->'))
        return {'reviews':reviews,'threads':[{'id':'t','path':'a.py','isResolved':False,'comments':comments}]}

    def test_host_accept_then_later_addressed_is_fixed(self):
        state,_=self.run_summary(self.timeline_fixture(['ADDRESSED'],'ACCEPT'))
        self.assertEqual(summary_result(state['comments'][0]['body'],'Fixed'),'1')
        self.assertEqual(summary_result(state['comments'][0]['body'],'Undetermined'),'0')

    def test_addressed_without_marker_is_known_fixed(self):
        state,_=self.run_summary(self.timeline_fixture(['ADDRESSED']))
        self.assertEqual(summary_result(state['comments'][0]['body'],'Fixed'),'1')

    def test_later_not_addressed_resets_fixed_but_not_reject(self):
        state,_=self.run_summary(self.timeline_fixture(['ADDRESSED','NOT_ADDRESSED']))
        self.assertEqual(summary_result(state['comments'][0]['body'],'Fixed'),'0 + unknown')
        state,_=self.run_summary(self.timeline_fixture(['NOT_ADDRESSED'],'REJECT'))
        self.assertEqual(summary_result(state['comments'][0]['body'],'Rejected'),'1')

    def test_unrelated_head_cannot_supply_reject(self):
        state,_=self.run_summary(self.timeline_fixture(['ADDRESSED'],'REJECT','f'*40))
        self.assertEqual(summary_result(state['comments'][0]['body'],'Rejected'),'0 + unknown')

    def test_host_disposition_equal_timestamp_uses_comment_id(self):
        fixture=self.timeline_fixture([],'REJECT')
        original=fixture['threads'][0]['comments'][1]
        marker=json.loads(original['body'].split('v1 ',1)[1].split(' -->')[0]); marker['disposition']='ACCEPT'
        later={**original,'databaseId':3,'body':'<!-- cr-received-disposition:v1 '+json.dumps(marker)+' -->'}
        fixture['threads'][0]['comments']=[fixture['threads'][0]['comments'][0],later,original]
        state,_=self.run_summary(fixture)
        self.assertEqual(summary_result(state['comments'][0]['body'],'Rejected'),'0')




class ReviewModelTest(unittest.TestCase):
    def run_resolve(self,model,effort):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'output'
            out.write_text('')
            result=subprocess.run(['bash','-eo','pipefail','-c',step('Resolve review model and effort')],
                                  env={**os.environ,'MODEL':model,'EFFORT':effort,'GITHUB_OUTPUT':str(out)},
                                  capture_output=True,text=True)
            return result,out.read_text()

    def test_accepts_defaults_and_allowed_values(self):
        for model,effort in [('claude-sonnet-5-5','medium'),('claude-opus-5-5','high'),('claude-haiku-5-5','low'),
                             ('claude-fable-5-1[1m]','medium')]:
            with self.subTest(model=model,effort=effort):
                result,output=self.run_resolve(model,effort)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(output,f'model={model}\neffort={effort}\n')

    def test_rejects_bad_effort_with_error_annotation(self):
        for effort in ['','xhigh','max','Medium','medium --foo','medium\nhigh']:
            with self.subTest(effort=effort):
                result,output=self.run_resolve('claude-sonnet-5-5',effort)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('::error',result.stdout+result.stderr)
                self.assertEqual(output,'')

    def test_rejects_bad_model_shape_with_error_annotation(self):
        for model in ['','sonnet --effort high','claude sonnet','-x','claude-sonnet-5-5\n--max-turns 1','a;b','$(id)','x'*130]:
            with self.subTest(model=model):
                result,output=self.run_resolve(model,'medium')
                self.assertNotEqual(result.returncode,0)
                self.assertIn('::error',result.stdout+result.stderr)
                self.assertEqual(output,'')


class ReviewSkillInstallTest(unittest.TestCase):
    # Fake npx installs a tree chosen by INSTALL; fake git lists release tags.
    NPX = """#!/bin/sh
printf '%s\\n' "$@" > "$ROOT/npx_args"
skill="$HOME/.claude/skills/cr-request"
mkdir -p "$HOME/.claude/skills" "$ROOT/outside"
printf -- '---\\nmetadata:\\n  version: "9.9.9"\\n---\\n' > "$ROOT/outside/SKILL.md"
case "$INSTALL" in
  none) ;;
  dirlink) ln -s "$ROOT/outside" "$skill" ;;
  filelink) mkdir -p "$skill"; ln -s "$ROOT/outside/SKILL.md" "$skill/SKILL.md" ;;
  empty) mkdir -p "$skill"; : > "$skill/SKILL.md" ;;
  noversion) mkdir -p "$skill"; printf -- '---\\nname: cr-request\\n---\\n' > "$skill/SKILL.md" ;;
  *) mkdir -p "$skill"; printf -- '---\\nmetadata:\\n  version: "0.0.11"\\n---\\n' > "$skill/SKILL.md" ;;
esac
"""
    GIT = """#!/bin/sh
printf '%s\\trefs/tags/v0.0.9\\n%s\\trefs/tags/v0.0.10\\n%s\\trefs/tags/latest\\n' \\
  aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb cccccccccccccccccccccccccccccccccccccccc
"""

    def install(self, mode='ok', ref=''):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'home').mkdir()
            for name, body in (('npx', self.NPX), ('git', self.GIT)):
                (root / name).write_text(body)
                (root / name).chmod(0o755)
            env = {**os.environ, 'HOME': str(root / 'home'), 'ROOT': str(root), 'INSTALL': mode,
                   'PATH': str(root) + os.pathsep + os.environ['PATH'], 'REVIEW_SKILLS_REF': ref,
                   'SOURCE': 'https://github.com/sentimony/agentic-workflow',
                   'GITHUB_OUTPUT': str(root / 'output')}
            result = subprocess.run(['bash', '-e', '-c', step('Install cr-request skill')], cwd=root,
                                    env=env, capture_output=True, text=True)
            args = (root / 'npx_args').read_text().splitlines() if (root / 'npx_args').exists() else []
            output = (root / 'output').read_text() if (root / 'output').exists() else ''
            return result, args, output

    def test_latest_release_tag_is_installed_through_skl_x(self):
        result, args, output = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(args, ['-y', '--prefer-online', 'skl-x@latest', 'add',
                                'https://github.com/sentimony/agentic-workflow/tree/v0.0.10',
                                '-s', 'cr-request', '-a', 'claude-code', '-g', '-y', '-m', 'sm'])
        self.assertEqual(output, 'version=0.0.11\n')

    def test_pinned_commit_is_installed(self):
        result, args, _ = self.install(ref='d' * 40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('https://github.com/sentimony/agentic-workflow/tree/' + 'd' * 40, args)

    def test_invalid_pin_installs_nothing(self):
        result, args, _ = self.install(ref='main')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('must be a full commit SHA', result.stdout)
        self.assertEqual(args, [])

    def assert_rejected(self, mode, message):
        result, _, output = self.install(mode)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stdout)
        self.assertEqual(output, '')

    def test_missing_skill_rejected(self):
        self.assert_rejected('none', 'not installed as a directory')

    def test_symlinked_skill_rejected(self):
        self.assert_rejected('dirlink', 'not installed as a directory')

    def test_symlinked_skill_file_rejected(self):
        self.assert_rejected('filelink', 'contains a symbolic link')

    def test_zero_byte_skill_file_rejected(self):
        self.assert_rejected('empty', 'no SKILL.md or it is empty')

    def test_missing_version_rejected(self):
        self.assert_rejected('noversion', 'No version in the cr-request SKILL.md')

if __name__ == '__main__':
    unittest.main()
