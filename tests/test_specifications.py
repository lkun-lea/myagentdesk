from concurrent.futures import ThreadPoolExecutor
import os

import pytest

from server.errors import Problem
from server.github_issues import GitHubIssues
from server.project_context import acceptance_cases
from test_requirement_groups import advance, create_group, stage_run, workflow


BASE = '1' * 40
HEAD = '2' * 40
CHANGED = '3' * 40
DIRECTORY = 'openspec/changes/pin-requirement-specification'


@pytest.fixture
def source(monkeypatch):
    live_base, live_head = os.environ.get('MAD_OPENSPEC_LIVE_BASE'), os.environ.get('MAD_OPENSPEC_LIVE_HEAD')
    if live_base and live_head:
        return 'lkun-lea/myagentdesk', live_base, live_head, None

    trees = {letter: letter * 40 for letter in 'abcdef78'}
    proposal = [{'path': 'proposal.md', 'type': 'blob', 'mode': '100644'}]

    def read(_client, path):
        if path in ('/repos/example/specifications', '/repositories/71'):
            return {'id': 71, 'full_name': 'example/specifications', 'private': False}
        if path == '/repositories/71/issues/10':
            return {'number': 10, 'state': 'open', 'title': '项目规格页', 'body': '固定规格与验收'}
        if path.startswith('/repositories/71/git/commits/'):
            sha = path.rsplit('/', 1)[-1]
            if sha in (BASE, HEAD, CHANGED):
                return {'sha': sha, 'tree': {'sha': trees['e' if sha == CHANGED else 'a']}}
        if path.startswith('/repositories/71/git/trees/'):
            sha = path.rsplit('/', 1)[-1]
            entries = {
                trees['a']: [{'path': 'openspec', 'type': 'tree', 'sha': trees['b']}],
                trees['b']: [{'path': 'changes', 'type': 'tree', 'sha': trees['c']}],
                trees['c']: [{'path': DIRECTORY.rsplit('/', 1)[-1], 'type': 'tree', 'sha': trees['d']}],
                trees['d']: proposal,
                trees['e']: [{'path': 'openspec', 'type': 'tree', 'sha': trees['f']}],
                trees['f']: [{'path': 'changes', 'type': 'tree', 'sha': trees['7']}],
                trees['7']: [{'path': DIRECTORY.rsplit('/', 1)[-1], 'type': 'tree', 'sha': trees['8']}],
                trees['8']: proposal,
            }
            if sha in entries:
                return {'sha': sha, 'truncated': False, 'tree': entries[sha]}
        raise Problem('github_source_unavailable', 404)

    monkeypatch.setattr(GitHubIssues, 'read', read)
    return 'example/specifications', BASE, HEAD, CHANGED


def test_fixed_specification_preserves_evidence_across_issue_revisions(workflow, source):
    (product, developer, tester, _), _, project, _, _, group_input, endpoint = workflow
    repository, base, head, changed_tree = source
    path = '/api/projects/' + project
    connection = product.put(path + '/github/connection', json={'repository': repository, 'expected_revision': 0})
    assert connection.status_code == 200, connection.text
    version = connection.json()['revision']
    issue = product.get(path + '/github/issues/10').json()
    content = {'title': '#10 ' + issue['title'][:105], 'body': '为公开 #10 固定规格，保留旧版本并重新验证变更条件。',
               'criteria': ['固定规格指向原始提交', '历史版本可查看旧验收记录']}
    linked = product.post(path + '/github/issues/10/link', json={'connection_revision': version, 'requirement': content})
    assert linked.status_code == 200, linked.text
    requirement = linked.json()['requirement_id']
    spec = path + f'/requirements/{requirement}/specification'
    assert tester.get(spec).json()['requirement']['specification'] is None
    group = create_group(product, project, {**group_input, 'requirement_id': requirement, 'source': issue['url']})
    group_path = '/api/groups/' + group['id']
    draft = {**content, 'expected_revision': 1, 'connection_revision': version, 'directory': DIRECTORY, 'commit': base}
    assert developer.put(spec, json=draft).status_code == 403
    assert product.put(spec, json={**draft, 'commit': 'main'}).status_code == 422
    assert product.put(spec, json={**draft, 'directory': 'openspec/changes/../outside'}).status_code == 422
    assert product.put(spec, json={**draft, 'directory': 'openspec/changes/does-not-exist'}).json()['error'] == 'specification_source_unavailable'
    first = product.put(spec, json=draft)
    assert first.status_code == 200, first.text
    assert first.json()['revision'] == 2
    assert product.put(spec, json=draft).status_code == 409
    assert product.get(group_path).json()['requirement']['specification']['commit'] == base

    original_plan = endpoint.plan
    seen_commits = []

    def plan(bundle, results, name, args):
        name, args = original_plan(bundle, results, name, args)
        if name == 'submit_handoff' and bundle['role'] == 'testing':
            requirement = bundle['requirement']
            seen_commits.append(requirement['specification']['commit'])
            args = {**args, 'case_results': [{'criterion': number, 'result': 'passed', 'evidence': text}
                                            for number, text in enumerate(requirement['criteria'], 1)]}
        return name, args

    endpoint.plan = plan

    def complete_current():
        assert stage_run(product, product.get(group_path).json())['status'] == 'completed'
        assert advance(product, product.get(group_path).json())['stage'] == 'development'
        assert stage_run(developer, product.get(group_path).json())['status'] == 'completed'
        result = stage_run(tester, product.get(group_path).json())
        assert result['status'] == 'completed', result['events'][-3:]
        before_gate = tester.get(spec).json()
        assert [case['status'] for case in before_gate['cases']] == ['passed', 'passed']
        assert before_gate['evidence'][0]['current']
        assert advance(product, product.get(group_path).json())['stage'] == 'completed'
        return result['id'], before_gate['evidence'][0]

    old_run, first_evidence = complete_current()
    original = tester.get(spec).json()
    assert original['requirement']['specification']['criteria_revision'] == 2
    prior_group = product.get(group_path).json()

    criteria = [content['criteria'][0], '历史版本可按候选运行查看旧验收记录']
    assert product.put(path + '/requirements', json={**content, 'id': requirement, 'expected_revision': 2,
                                                      'criteria': criteria}).json()['error'] == 'spec_confirmation_required'
    revision = product.put(spec, json={**draft, 'expected_revision': 2, 'commit': head, 'criteria': criteria})
    assert revision.status_code == 200, revision.text
    current = tester.get(spec).json()
    assert current['requirement']['specification']['commit'] == head
    assert current['requirement']['specification']['criteria_revision'] == 3
    assert [(case['status'], case['affected']) for case in current['cases']] == [
        ('historical', False), ('pending_reverification', True)]
    assert current['evidence'] == []
    assert product.get(group_path).json()['stage'] == 'product'
    assert product.post(group_path + '/advance', json={'expected_revision': prior_group['revision'],
        'action': 'approve', 'note': '历史结果不能放行新版本'}).status_code == 409
    assert tester.post(f'/api/runs/{old_run}/confirm', json={'expected_revision': 3, 'documents': {}}).json()['error'] == 'run_completed'
    history = tester.get(spec + '?revision=2').json()
    assert history['requirement']['specification']['commit'] == base
    assert history['requirement']['criteria'] == content['criteria']
    assert history['evidence'] == [{**first_evidence, 'current': False}]

    new_run, new_evidence = complete_current()
    assert first_evidence['candidate_run'] != new_evidence['candidate_run']
    assert old_run != new_run and seen_commits == [base, head]
    if changed_tree:
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: product.put(spec, json={**draft, 'expected_revision': 3,
                                                                      'commit': changed_tree, 'criteria': criteria}), range(2)))
        assert sorted(result.status_code for result in results) == [200, 409]
        after_tree_change = tester.get(spec).json()
        assert after_tree_change['requirement']['specification']['criteria_revision'] == 3
        assert [case['status'] for case in after_tree_change['cases']] == ['pending_reverification', 'pending_reverification']
    assert product.delete(path + '/members/' + tester.get('/api/auth/session').json()['user']['id']).status_code == 200
    assert tester.get(spec + '?revision=2').status_code == 404


@pytest.mark.parametrize('cases,verdict,code', [
    (None, 'passed', 'evidence_required'),
    ([{'criterion': 1, 'result': 'passed', 'evidence': 'proof'}], 'passed', 'evidence_required'),
    ([{'criterion': 1, 'result': 'passed', 'evidence': 'proof'}] * 2, 'passed', 'invalid_request'),
    ([{'criterion': 1, 'result': 'passed', 'evidence': 'proof'},
      {'criterion': 2, 'result': 'rejected', 'evidence': 'failed'}], 'passed', 'invalid_request'),
])
def test_acceptance_verdict_requires_distinct_evidence_for_all_frozen_cases(cases, verdict, code):
    with pytest.raises(Problem) as problem:
        acceptance_cases({'specification': {'commit': BASE}, 'criteria': ['one', 'two']}, cases, verdict)
    assert problem.value.code == code
