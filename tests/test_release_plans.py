from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from test_github_issues import attach, github_project
from test_requirement_groups import advance, create_group, stage_run, workflow


def new_requirement(owner, path):
    identifier = str(uuid4())
    response = owner.put(path + '/requirements', json={'id': identifier, 'expected_revision': 0, 'title': '试点范围',
                                                        'body': '验证需求与发布排期', 'criteria': ['核对原始证据']})
    assert response.status_code == 200
    return identifier


def plan_payload(user_id, requirements, **overrides):
    return {'expected_revision': 0, 'name': 'v0.1 试点', 'owner_id': user_id, 'target_date': '2026-11-20',
            'status': 'committed', 'requirement_ids': requirements,
            'milestones': [{'name': '阶段验收', 'date': '2026-11-19'}], 'required_checks': ['核对同版本构建回执'],
            'reason': '首次纳入试点', **overrides}


def test_schedule_linked_issue_and_revise_with_current_membership(github_project):
    owner, member, path, _ = github_project
    owner_id = owner.get('/api/auth/session').json()['user']['id']
    member_id = member.get('/api/auth/session').json()['user']['id']
    requirement = new_requirement(owner, path)
    left_out = new_requirement(owner, path)
    repo_revision = attach(owner, path)
    assert owner.post(path + '/github/issues/9/link', json={'connection_revision': repo_revision, 'requirement_id': requirement}).status_code == 200
    plans = path + '/versions'
    version = plans + '/' + str(uuid4())
    initial = plan_payload(owner_id, [requirement])
    assert member.put(version, json=initial).status_code == 403
    assert owner.put(version, json={**initial, 'owner_id': member_id}).json()['error'] == 'version_owner_required'
    assert owner.put(version, json=initial).json()['revision'] == 1
    current = member.get(plans).json()
    assert current['unscheduled_ids'] == [left_out]
    source = next(item for item in current['requirements'] if item['id'] == requirement)
    assert source['sources'] == [{'issue_number': 9, 'connection_revision': repo_revision}]
    assert current['plans'][0]['counts'] == {'accepted': 0, 'pending': 1, 'blocked': 0}
    assert current['plans'][0]['actual_published_at'] is None
    update = {**initial, 'expected_revision': 1, 'requirement_ids': [], 'reason': '延期并移出试点'}
    assert owner.patch(path + '/members/' + member_id, json={'role': 'admin'}).status_code == 200
    with ThreadPoolExecutor(2) as workers:
        first = workers.submit(owner.put, version, json={**update, 'target_date': '2026-11-22'})
        second = workers.submit(member.put, version, json={**update, 'target_date': '2026-11-24'})
        replies = [first.result(), second.result()]
    assert sorted(reply.status_code for reply in replies) == [200, 409]
    history = member.get(version + '/history').json()['revisions']
    assert [row['revision'] for row in history] == [2, 1]
    winner = owner_id if replies[0].status_code == 200 else member_id
    assert history[0]['reason'] == update['reason'] and history[0]['actor_id'] == winner
    assert history[0]['requirement_ids'] == [] and history[1]['requirement_ids'] == [requirement]
    assert member.get(version + '/history/1').json()['target_date'] == initial['target_date']
    assert member.get(plans).json()['plans'][0]['counts']['pending'] == 0
    actions = [item for item in member.get(path + '/timeline').json() if item['action'].startswith('version_')]
    assert [item['target'].split(':')[-1] for item in actions] == ['1', '2']
    assert owner.delete(path + '/members/' + member_id).status_code == 200
    assert member.get(plans).status_code == 404
    assert member.get(version + '/history/1').status_code == 404
    assert member.get(path + '/timeline').status_code == 404


def test_undated_cancelled_versions_and_cross_project_scope(github_project):
    owner, _, path, _ = github_project
    owner_id = owner.get('/api/auth/session').json()['user']['id']
    requirement = new_requirement(owner, path)
    versions = path + '/versions'
    first = versions + '/' + str(uuid4())
    second = versions + '/' + str(uuid4())
    draft = plan_payload(owner_id, [requirement], target_date=None, status='draft', milestones=[])
    assert owner.put(first, json=draft).status_code == 200
    assert owner.put(second, json=draft).json()['error'] == 'requirement_already_scheduled'
    response = owner.get(versions).json()
    assert response['plans'][0]['target_date'] is None and response['plans'][0]['release_state'] == 'draft'
    assert owner.put(first, json={**draft, 'expected_revision': 1, 'status': 'cancelled', 'reason': '释放原排期'}).status_code == 200
    assert owner.get(versions).json()['unscheduled_ids'] == [requirement]
    assert owner.put(second, json=draft).status_code == 200
    another = owner.post('/api/projects', json={'name': '另一个项目'}).json()['id']
    other = f'/api/projects/{another}/versions'
    assert owner.get(other + '/' + first.split('/')[-1] + '/history/1').status_code == 404
    assert owner.put(other + '/' + str(uuid4()), json=draft).json()['error'] == 'requirement_not_found'


def test_phase_blocker_then_accepted_version_still_waits_for_release(workflow):
    (product, developer, tester, _), ids, project, document, requirement, spec, endpoint = workflow
    path = '/api/projects/' + project
    versions = path + '/versions'
    assert product.put(versions + '/' + str(uuid4()), json=plan_payload(ids[0], [requirement['id']], target_date='2020-01-01')).status_code == 200
    group = create_group(product, project, spec)
    group_path = '/api/groups/' + group['id']
    stage_run(product, group)
    group = advance(product, product.get(group_path).json())
    endpoint.ask = True
    stage_run(developer, group)
    snapshot = product.get(versions).json()
    assert snapshot['plans'][0]['counts'] == {'accepted': 0, 'pending': 0, 'blocked': 1}
    blocked = snapshot['requirements'][0]
    assert blocked['group_id'] == group['id'] and blocked['stage'] == 'development'
    assert blocked['owner'] == 'developer@example.com' and blocked['due_at'] and blocked['blocker'] == 'waiting_decision'
    decision = developer.get('/api/decisions').json()[0]
    assert developer.post(f"/api/decisions/{decision['id']}/resolve", json={
        'expected_revision': decision['revision'], 'answer': decision['options'][0], 'note': '继续试点',
    }).status_code == 200
    group = product.get(group_path).json()
    assert stage_run(tester, group)['status'] == 'completed'
    assert advance(product, product.get(group_path).json())['stage'] == 'completed'
    plan = product.get(versions).json()['plans'][0]
    assert plan['counts'] == {'accepted': 1, 'pending': 0, 'blocked': 0}
    assert plan['release_state'] == 'awaiting_release' and plan['actual_published_at'] is None
    assert product.put(path + '/context', json={**document, 'expected_revision': 1, 'body': '资料更新，要重新验收。'}).status_code == 200
    revised = product.get(versions).json()['plans'][0]
    assert revised['counts']['accepted'] == 0 and revised['release_state'] == 'awaiting_acceptance'
