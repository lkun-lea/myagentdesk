import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import Field, field_validator

from server.db import connect
from server.errors import Problem
from server.github_issues import GitHubIssues, active_connection, connection, connection_state
from server.project_context import RequirementContent, RequirementInput, handoff_outdated, latest_requirement, save_requirement
from server.projects import membership


SHA = re.compile(r'[0-9a-f]{40}')
CHANGE_DIRECTORY = re.compile(r'openspec/changes/(?:archive/[0-9]{4}-[0-9]{2}-[0-9]{2}-)?[a-z0-9]+(?:-[a-z0-9]+)*')


class SpecificationInput(RequirementContent):
    expected_revision: int = Field(ge=1)
    connection_revision: int = Field(ge=1)
    commit: str = Field(pattern=r'^[0-9a-f]{40}$')
    directory: str = Field(max_length=140)

    @field_validator('directory')
    @classmethod
    def validate_directory(cls, directory):
        if not CHANGE_DIRECTORY.fullmatch(directory) or directory == 'openspec/changes/archive':
            raise ValueError('Expected an OpenSpec change directory')
        return directory

    @field_validator('criteria')
    @classmethod
    def distinct_criteria(cls, criteria):
        cleaned = [item.strip() for item in criteria]
        if len(set(cleaned)) != len(cleaned):
            raise ValueError('Expected distinct acceptance conditions')
        return cleaned


def group_for(db, project_id, requirement_id):
    return db.execute('''SELECT product_id,stage,last_handoff FROM mad_requirement_groups
                         WHERE project_id=%s AND requirement_id=%s''', (project_id, requirement_id)).fetchone()


def can_confirm(db, member, group, user_id):
    if not group:
        return member['role'] in ('owner', 'admin')
    return group['product_id'] == user_id and db.execute('''SELECT 1 FROM mad_project_roles
        WHERE project_id=%s AND user_id=%s AND role='product' ''', (member['id'], user_id)).fetchone() is not None


def resolve_reference(github, repository_id, commit, directory):
    if not SHA.fullmatch(commit):
        raise Problem('invalid_request', 422)
    root = f'/repositories/{repository_id}'
    repository, _ = github.repository(root, repository_id)
    verification = github.read(f'{root}/git/commits/{commit}')
    tree = verification.get('tree')
    tree_sha = tree.get('sha') if isinstance(tree, dict) else None
    if verification.get('sha') != commit or not isinstance(tree_sha, str) or not SHA.fullmatch(tree_sha):
        raise Problem('specification_source_unavailable', 404)

    def entries(sha):
        result = github.read(f'{root}/git/trees/{sha}')
        tree_entries = result.get('tree')
        if result.get('sha') != sha or result.get('truncated') is not False or not isinstance(tree_entries, list):
            raise Problem('specification_source_unavailable', 404)
        return tree_entries

    for segment in directory.split('/'):
        selected = next((item for item in entries(tree_sha)
                         if isinstance(item, dict) and item.get('path') == segment and item.get('type') == 'tree'), None)
        tree_sha = selected.get('sha') if selected else None
        if not isinstance(tree_sha, str) or not SHA.fullmatch(tree_sha):
            raise Problem('specification_source_unavailable', 404)
    if not any(isinstance(item, dict) and item.get('path') == 'proposal.md' and item.get('type') == 'blob'
               and item.get('mode') in ('100644', '100755') for item in entries(tree_sha)):
        raise Problem('specification_source_unavailable', 404)
    if github.authenticated:
        repository, _ = github.repository(root, repository_id)
    folder = directory.rsplit('/', 1)[-1]
    change_id = folder[11:] if directory.startswith('openspec/changes/archive/') else folder
    return {'repository_id': repository_id, 'repository': repository, 'commit': commit, 'directory': directory,
            'change_id': change_id, 'tree': tree_sha}


def testing_evidence(db, requirement):
    return db.execute('''SELECT h.* FROM mad_handoffs h
                         WHERE h.project_id=%s AND h.requirement_id=%s AND h.requirement_revision=%s AND h.role='testing'
                         ORDER BY h.created_at DESC,h.run_id LIMIT 50''',
                      (requirement['project_id'], requirement['id'], requirement['revision'])).fetchall()


def applicable_evidence(db, requirement, group, evidence):
    if group:
        valid = next((row for row in evidence if row['run_id'] == group['last_handoff']), None)
        if group['stage'] not in ('quality', 'completed'):
            return None
    else:
        candidate = db.execute('''SELECT h.run_id FROM mad_handoffs h JOIN mad_agent_runs r ON r.id=h.run_id
                                  WHERE h.project_id=%s AND h.requirement_id=%s AND h.requirement_revision=%s
                                  AND h.role='development' AND r.group_id IS NULL
                                  ORDER BY h.created_at DESC,h.run_id LIMIT 1''',
                               (requirement['project_id'], requirement['id'], requirement['revision'])).fetchone()
        valid = next((row for row in evidence if row['candidate_run'] == candidate['run_id']), None) if candidate else None
    return valid if valid and not handoff_outdated(db, valid) else None


def case_statuses(db, target, previous, current_evidence):
    # A new source tree or requirement description can affect every criterion.
    def source_key(row):
        pin = row['specification']
        return (pin['repository_id'], pin['change_id'], pin['tree'], row['title'], row['body'])

    prior_criteria = previous['criteria'] if previous and previous['specification'] else []
    same_source = bool(prior_criteria) and source_key(target) == source_key(previous)
    prior_cases = {}
    if same_source:
        historical = next(iter(testing_evidence(db, previous)), None)
        if historical:
            prior_cases = {prior_criteria[item['criterion'] - 1]: item['result'] for item in historical['case_results']}
    verified = {item['criterion']: item for item in current_evidence['case_results']} if current_evidence else {}
    statuses = []
    for number, criterion in enumerate(target['criteria'], start=1):
        affected = bool(prior_criteria) and (not same_source or criterion not in prior_criteria)
        result = verified.get(number)
        status = (result['result'] if result else 'pending_reverification' if affected else
                  'historical' if prior_cases.get(criterion) == 'passed' else 'pending')
        statuses.append({'criterion': number, 'text': criterion, 'affected': affected, 'status': status})
    return statuses


def specifications_router(database, identity, token='', transport=None):
    router = APIRouter(prefix='/api/projects/{project_id}/requirements/{requirement_id}/specification')
    authenticated = Depends(identity)
    github = GitHubIssues(token, transport)

    @router.get('')
    def detail(project_id: UUID, requirement_id: UUID, revision: Annotated[int | None, Query(ge=1)] = None,
               user=authenticated):
        with connect(database) as db:
            member = membership(db, project_id, user['id'], write=True)
            current = latest_requirement(db, project_id, requirement_id)
            group = group_for(db, project_id, requirement_id)
            target = current if revision is None or revision == current['revision'] else db.execute(
                'SELECT * FROM mad_requirements WHERE project_id=%s AND id=%s AND revision=%s',
                (project_id, requirement_id, revision)).fetchone()
            if not target:
                raise Problem('requirement_not_found', 404)
            history = db.execute('''SELECT revision,specification,created_at FROM mad_requirements
                                    WHERE project_id=%s AND id=%s ORDER BY revision DESC''',
                                 (project_id, requirement_id)).fetchall()
            previous = db.execute('''SELECT * FROM mad_requirements WHERE project_id=%s AND id=%s AND revision<%s
                                     ORDER BY revision DESC LIMIT 1''', (project_id, requirement_id, target['revision'])).fetchone()
            evidence = testing_evidence(db, target)
            is_current = target['revision'] == current['revision']
            valid = applicable_evidence(db, target, group, evidence) if is_current and target['specification'] else None
            cases = case_statuses(db, target, previous, valid) if is_current and target['specification'] else []
            return {'requirement': target, 'current_revision': current['revision'], 'history': history,
                    'cases': cases, 'can_confirm': can_confirm(db, member, group, user['id']),
                    'connection': connection_state(connection(db, project_id)),
                    'evidence': [{'run_id': row['run_id'], 'candidate_run': row['candidate_run'], 'verdict': row['verdict'],
                                  'summary': row['summary'], 'evidence': row['evidence'], 'case_results': row['case_results'],
                                  'current': bool(valid and row['run_id'] == valid['run_id'])} for row in evidence]}

    def confirm_scope(db, project_id, requirement_id, data, user_id, *, lock=False):
        member = membership(db, project_id, user_id, write=lock)
        requirement = latest_requirement(db, project_id, requirement_id)
        if not can_confirm(db, member, group_for(db, project_id, requirement_id), user_id):
            raise Problem('permission_denied', 403)
        connected = active_connection(db, project_id)
        if data.expected_revision != requirement['revision'] or data.connection_revision != connected['revision']:
            raise Problem('version_conflict', 409)
        return connected['repository_id']

    @router.put('')
    def confirm(project_id: UUID, requirement_id: UUID, data: SpecificationInput, user=authenticated):
        with connect(database) as db:
            repository_id = confirm_scope(db, project_id, requirement_id, data, user['id'])
        pinned = resolve_reference(github, repository_id, data.commit, data.directory)
        with connect(database) as db:
            if confirm_scope(db, project_id, requirement_id, data, user['id'], lock=True) != repository_id:
                raise Problem('version_conflict', 409)
            return save_requirement(db, project_id, RequirementInput(id=requirement_id, expected_revision=data.expected_revision,
                title=data.title, body=data.body, criteria=data.criteria), user['id'], specification=pinned)

    return router
