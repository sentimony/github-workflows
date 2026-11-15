#!/usr/bin/env python3
"""Check a reusable workflow's actual interface and caller permission ceiling offline."""
import argparse
import json
import os
import re
import subprocess
import sys


class ContractError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def mapping(value, message):
    require(isinstance(value, dict), message)
    return value


def load(path):
    ruby = os.environ.get('RUBY', 'ruby')
    source = "require 'yaml'; require 'json'; puts JSON.generate(YAML.safe_load_file(ARGV[0], aliases: true))"
    try:
        result = subprocess.run([ruby, '-e', source, path], capture_output=True, text=True, check=True)
        return mapping(json.loads(result.stdout), 'workflow must be a mapping')
    except (OSError, subprocess.CalledProcessError, ValueError):
        raise ContractError('cannot load workflow YAML') from None


def permissions(value):
    if value == 'read-all':
        return {'*': 1}
    if value == 'write-all':
        return {'*': 2}
    require(isinstance(value, dict), 'permissions must be explicit')
    levels = {'none': 0, 'read': 1, 'write': 2}
    result = {}
    for key, level in value.items():
        require(isinstance(key, str) and isinstance(level, str) and level in levels, 'invalid permission')
        result[key] = levels[level]
    return result


def level(perms, key):
    return perms.get(key, perms.get('*', 0))


def validate_type(value, kind):
    if kind == 'string':
        return isinstance(value, str)
    if kind == 'boolean':
        return type(value) is bool
    if kind == 'number':
        return type(value) in (int, float)
    return False


JOB_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9 _.,()/-]*')
REVIEWER_SECRETS = ('AUTO_REVIEWER_TOKEN', 'AUTO_REVIEWER_YNGWALD')


def job_names(*values):
    # Same split as the gate: a value with a newline splits on newlines only, else on commas.
    names = []
    for value in values:
        if not isinstance(value, str):
            continue
        value = value.replace('\r', '')
        parts = value.split('\n') if '\n' in value else value.split(',')
        names.extend(part.strip() for part in parts if part.strip())
    return names


def check(central, caller, expected):
    events = mapping(central.get('on', central.get('true')), 'missing workflow_call')
    call = mapping(events.get('workflow_call'), 'missing workflow_call')
    inputs = mapping(call.get('inputs', {}), 'invalid inputs schema')
    secrets = mapping(call.get('secrets', {}), 'invalid secrets schema')
    jobs = mapping(caller.get('jobs'), 'missing caller jobs')
    candidates = [job for job in jobs.values() if isinstance(job, dict) and 'uses' in job]
    require(len(candidates) == 1, 'caller must have one reusable workflow job')
    job = candidates[0]
    # Caller tests own exact uses paths/pins; this checker compares schemas and permissions.
    values = mapping(job.get('with', {}), 'caller inputs must be explicit')
    bindings = mapping(job.get('secrets', {}), 'caller secrets must be explicit')
    require(not (set(values) - set(inputs)), 'unknown caller input')
    require(not (set(bindings) - set(secrets)), 'unknown caller secret')
    for name, spec in inputs.items():
        spec = mapping(spec, 'invalid input schema')
        kind = spec.get('type')
        require(kind in ('string', 'number', 'boolean'), 'invalid input type')
        require(not spec.get('required', False) or name in values, 'missing required input')
        if 'default' in spec:
            require(validate_type(spec['default'], kind), 'invalid input default')
        if name in values:
            require(validate_type(values[name], kind), 'invalid caller input type')
    for name, spec in secrets.items():
        spec = mapping(spec, 'invalid secret schema')
        require(not spec.get('required', False) or name in bindings, 'missing required secret')
    require(all(isinstance(v, str) and v for v in bindings.values()), 'invalid secret binding')
    if all(name in secrets for name in REVIEWER_SECRETS):
        require(any(name in bindings for name in REVIEWER_SECRETS),
                'missing required secret: bind AUTO_REVIEWER_TOKEN or AUTO_REVIEWER_YNGWALD')
    names = job_names(values.get('required-ci-job'), values.get('required-ci-jobs'))
    require(bool(names), 'missing required CI job')
    for name in expected:
        require(name in names, 'incorrect required CI job')
        require(JOB_NAME.fullmatch(name) is not None, 'invalid CI job')
    require(all(JOB_NAME.fullmatch(name) for name in names), 'invalid CI job')
    ceiling = permissions(job.get('permissions', caller.get('permissions')))
    defaults = central.get('permissions', {})
    callee_jobs = mapping(central.get('jobs'), 'missing callee jobs')
    require(bool(callee_jobs), 'missing callee jobs')
    for callee_job in callee_jobs.values():
        callee_job = mapping(callee_job, 'invalid callee job')
        needed = permissions(callee_job.get('permissions', defaults))
        if '*' in needed:
            require(level(ceiling, '*') >= needed['*'], 'insufficient caller permissions')
        for key, amount in needed.items():
            require(level(ceiling, key) >= amount, 'insufficient caller permissions')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workflow', required=True)
    parser.add_argument('--caller', required=True)
    parser.add_argument('--required-ci-job', required=True, action='append',
                        help='CI job the caller must require; repeat for several')
    args = parser.parse_args()
    try:
        check(load(args.workflow), load(args.caller), args.required_ci_job)
    except ContractError as error:
        print(f'contract error: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
