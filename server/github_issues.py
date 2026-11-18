from datetime import datetime, timezone
import re
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Path
import httpx
from pydantic import BaseModel, Field, model_validator

from server.db import connect
from server.errors import Problem
from server.project_context import RequirementContent, RequirementInput, latest_requirement, save_requirement
from server.projects import membership, record


REPOSITORY_NAME = re.compile(r'[a-zA-Z0-9-]{1,39}/(?!\.{1,2}$)[a-zA-Z0-9_.-]{1,100}')
IssueNumber = Annotated[int, Path(ge=1, le=2147483647)]


class ConnectionInput(BaseModel):
    repository: str = Field(max_length=140)
    expected_revision: int = Field(ge=0)


class DisconnectInput(BaseModel):
    expected_revision: int = Field(ge=1)


class LinkInput(BaseModel):
    connection_revision: int = Field(ge=1)
    requirement_id: UUID | None = None
    requirement: RequirementContent | None = None

    @model_validator(mode='after')
    def one_target(self):
        if (self.requirement_id is None) == (self.requirement is None):
            raise ValueError('Choose exactly one requirement target')
        return self


class GitHubIssues:
    def __init__(self, token='', transport=None):
        self.transport = transport
        self.authenticated = bool(token)
        self.headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
                        'User-Agent': 'MyAgentDesk/0.1'}
        if token:
            self.headers['Authorization'] = f'Bearer {token}'

    def read(self, path):
        try:
            with httpx.Client(base_url='https://api.github.com', timeout=8, transport=self.transport,
                              headers=self.headers) as client:
                response = client.get(path)
        except httpx.RequestError:
            raise Problem('github_unavailable', 503) from None
        if response.status_code == 429 or (response.status_code == 403 and (
                response.headers.get('x-ratelimit-remaining') == '0' or response.headers.get('retry-after'))):
            raise Problem('github_rate_limited', 429)
        if response.status_code in {301, 302, 307, 308, 401, 403, 404, 410, 451}:
            raise Problem('github_source_unavailable', 404)
        if response.status_code != 200:
            raise Problem('github_unavailable', 503)
        try:
            data = response.json()
        except ValueError:
            raise Problem('github_unavailable', 503) from None
        if not isinstance(data, dict):
            raise Problem('github_unavailable', 503)
        return data

    def repository(self, path, expected_id=None):
        repo = self.read(path)
        name, identifier = repo.get('full_name'), repo.get('id')
        if (repo.get('private') is not False or repo.get('visibility', 'public') != 'public'
                or repo.get('has_issues') is False):
            raise Problem('github_source_unavailable', 404)
        if (type(identifier) is not int or not 0 < identifier < 2**63
                or (expected_id is not None and identifier != expected_id)
                or not isinstance(name, str) or not REPOSITORY_NAME.fullmatch(name)):
            raise Problem('github_source_unavailable', 404)
        return name, identifier

    def issue(self, repository_id, number):
        name, _ = self.repository(f'/repositories/{repository_id}', repository_id)
        issue = self.read(f'/repositories/{repository_id}/issues/{number}')
        if self.authenticated:
            # A credential could still read the issue if visibility changed during the first request.
            name, _ = self.repository(f'/repositories/{repository_id}', repository_id)
        if 'pull_request' in issue or issue.get('number') != number:
            raise Problem('github_issue_not_found', 404)
        if issue.get('state') not in {'open', 'closed'} or not isinstance(issue.get('title'), str):
            raise Problem('github_unavailable', 503)
        body = issue.get('body') if isinstance(issue.get('body'), str) else ''
        return {'repository': name, 'number': number, 'state': issue['state'], 'title': issue['title'],
                'body': body[:24000], 'body_truncated': len(body) > 24000, 'state_reason': issue.get('state_reason'),
                'url': f'https://github.com/{name}/issues/{number}', 'checked_at': datetime.now(timezone.utc)}


def connection(db, project_id):
    return db.execute('SELECT repository_id,revision,enabled FROM mad_github_connections WHERE project_id=%s',
                      (project_id,)).fetchone()


def connection_state(row):
    return {'connected': bool(row and row['enabled']), 'revision': row['revision'] if row else 0}


def active_connection(db, project_id):
    row = connection(db, project_id)
    if not row or not row['enabled']:
        raise Problem('github_not_connected', 404)
    return row


def issue_link(db, project_id, repository_id, number):
    return db.execute('''SELECT l.requirement_id,l.linked_revision,r.revision,r.title,g.stage,g.state AS stage_state
        FROM mad_github_issue_links l
        JOIN LATERAL (SELECT title,revision FROM mad_requirements
            WHERE project_id=l.project_id AND id=l.requirement_id ORDER BY revision DESC LIMIT 1) r ON true
        LEFT JOIN mad_requirement_groups g ON g.project_id=l.project_id AND g.requirement_id=l.requirement_id
        WHERE l.project_id=%s AND l.repository_id=%s AND l.issue_number=%s''', (project_id, repository_id, number)).fetchone()


def github_router(database, identity, token='', transport=None):
    router = APIRouter(prefix='/api/projects/{project_id}/github')
    authenticated = Depends(identity)
    github = GitHubIssues(token, transport)

    @router.get('/connection')
    def connection_status(project_id: UUID, user=authenticated):
        with connect(database) as db:
            membership(db, project_id, user['id'])
            return connection_state(connection(db, project_id))

    @router.put('/connection')
    def attach(project_id: UUID, data: ConnectionInput, user=authenticated):
        name = data.repository.strip()
        if not REPOSITORY_NAME.fullmatch(name):
            raise Problem('github_invalid_repository', 422)
        with connect(database) as db:
            membership(db, project_id, user['id'], manage=True)
        _, repository_id = github.repository(f'/repos/{name}')
        with connect(database) as db:
            membership(db, project_id, user['id'], write=True, manage=True)
            current = connection(db, project_id)
            if current and current['enabled'] and current['repository_id'] == repository_id:
                return connection_state(current)
            if connection_state(current)['revision'] != data.expected_revision:
                raise Problem('version_conflict', 409)
            updated = db.execute('''INSERT INTO mad_github_connections(project_id,repository_id) VALUES (%s,%s)
                ON CONFLICT(project_id) DO UPDATE SET repository_id=excluded.repository_id,enabled=true,
                revision=mad_github_connections.revision+1 RETURNING *''', (project_id, repository_id)).fetchone()
            record(db, project_id, user['id'], repository_id, 'github_repository_connected')
            return connection_state(updated)

    @router.post('/disconnect')
    def detach(project_id: UUID, data: DisconnectInput, user=authenticated):
        with connect(database) as db:
            membership(db, project_id, user['id'], write=True, manage=True)
            current = connection(db, project_id)
            if not current or not current['enabled']:
                return connection_state(current)
            if current['revision'] != data.expected_revision:
                raise Problem('version_conflict', 409)
            updated = db.execute('''UPDATE mad_github_connections SET enabled=false,revision=revision+1
                WHERE project_id=%s RETURNING *''', (project_id,)).fetchone()
            record(db, project_id, user['id'], current['repository_id'], 'github_repository_disconnected')
            return connection_state(updated)

    def load_issue(project_id, number, user_id, expected_revision=None, *, manage=False):
        with connect(database) as db:
            membership(db, project_id, user_id, manage=manage)
            current = active_connection(db, project_id)
            if expected_revision is not None and current['revision'] != expected_revision:
                raise Problem('version_conflict', 409)
        return current, github.issue(current['repository_id'], number)

    @router.get('/issues/{number}')
    def detail(project_id: UUID, number: IssueNumber, revision: int | None = None, user=authenticated):
        current, source = load_issue(project_id, number, user['id'], revision)
        with connect(database) as db:
            membership(db, project_id, user['id'], write=True)
            if active_connection(db, project_id) != current:
                raise Problem('version_conflict', 409)
            linked = issue_link(db, project_id, current['repository_id'], number)
            return {**source, 'connection_revision': current['revision'], 'linked': linked}

    @router.post('/issues/{number}/link')
    def link(project_id: UUID, number: IssueNumber, data: LinkInput, user=authenticated):
        current, _ = load_issue(project_id, number, user['id'], data.connection_revision, manage=True)
        with connect(database) as db:
            membership(db, project_id, user['id'], write=True, manage=True)
            if active_connection(db, project_id) != current:
                raise Problem('version_conflict', 409)
            repository = current['repository_id']
            prior = issue_link(db, project_id, repository, number)
            if prior:
                return prior
            if data.requirement:
                target = save_requirement(db, project_id, RequirementInput(
                    id=uuid4(), expected_revision=0, **data.requirement.model_dump()), user['id'])
            else:
                target = latest_requirement(db, project_id, data.requirement_id)
            db.execute('''INSERT INTO mad_github_issue_links(project_id,repository_id,issue_number,requirement_id,linked_revision,linked_by)
                VALUES (%s,%s,%s,%s,%s,%s)''', (project_id, repository, number, target['id'], target['revision'], user['id']))
            record(db, project_id, user['id'], target['id'], 'github_issue_linked')
            return issue_link(db, project_id, repository, number)

    @router.get('/requirements/{requirement_id}/links')
    def requirement_links(project_id: UUID, requirement_id: UUID, user=authenticated):
        with connect(database) as db:
            membership(db, project_id, user['id'], write=True)
            latest_requirement(db, project_id, requirement_id)
            current = connection(db, project_id)
            if not current or not current['enabled']:
                return []
            return db.execute('''SELECT issue_number,linked_revision,%s::integer AS connection_revision FROM mad_github_issue_links
                WHERE project_id=%s AND requirement_id=%s AND repository_id=%s ORDER BY issue_number''',
                (current['revision'], project_id, requirement_id, current['repository_id'])).fetchall()

    return router
