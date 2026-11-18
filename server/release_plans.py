from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, StringConstraints, field_validator

from server.db import connect
from server.errors import Problem
from server.project_context import handoff_outdated
from server.projects import membership, record


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Check = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class Milestone(BaseModel):
    name: Name
    date: date


class VersionInput(BaseModel):
    expected_revision: int = Field(ge=0, lt=2147483647)
    name: Name
    owner_id: UUID
    target_date: date | None = None
    status: Literal['draft', 'committed', 'cancelled']
    requirement_ids: list[UUID] = Field(max_length=100)
    milestones: list[Milestone] = Field(max_length=12)
    required_checks: list[Check] = Field(max_length=20)
    reason: Reason

    @field_validator('requirement_ids')
    @classmethod
    def unique_scope(cls, identifiers):
        if len(set(identifiers)) != len(identifiers):
            raise ValueError('Duplicate scope entry')
        return identifiers


CURRENT_PLANS = '''SELECT DISTINCT ON (plan_id) * FROM mad_version_plan_revisions
    WHERE project_id=%s ORDER BY plan_id, revision DESC'''


def project_requirements(db, project_id):
    rows = db.execute('''WITH latest AS (
            SELECT DISTINCT ON (id) id,title,criteria,revision FROM mad_requirements WHERE project_id=%s ORDER BY id,revision DESC
        ) SELECT r.*,g.id AS group_id,g.stage,g.state,g.last_handoff,
            CASE WHEN g.stage='quality' AND m.role IN ('owner','admin') OR position.role=g.stage
                 THEN person.email ELSE '' END AS owner,run.status AS run_status,decision.due_at
        FROM latest r LEFT JOIN mad_requirement_groups g ON g.project_id=%s AND g.requirement_id=r.id
        LEFT JOIN mad_memberships m ON m.project_id=g.project_id AND m.user_id=CASE g.stage
            WHEN 'product' THEN g.product_id WHEN 'development' THEN g.development_id
            WHEN 'testing' THEN g.testing_id WHEN 'quality' THEN g.quality_id END
        LEFT JOIN mad_users person ON person.id=m.user_id
        LEFT JOIN mad_project_roles position ON position.project_id=m.project_id AND position.user_id=m.user_id
        LEFT JOIN mad_agent_runs run ON run.group_id=g.id AND run.group_revision=g.revision
        LEFT JOIN LATERAL (SELECT due_at FROM mad_run_decisions WHERE run_id=run.id AND state='pending'
            ORDER BY created_at DESC LIMIT 1) decision ON true ORDER BY r.title,r.id''', (project_id, project_id)).fetchall()
    links = db.execute('''SELECT l.requirement_id,l.issue_number,c.revision AS connection_revision
        FROM mad_github_issue_links l JOIN mad_github_connections c ON c.project_id=l.project_id
        AND c.repository_id=l.repository_id AND c.enabled WHERE l.project_id=%s ORDER BY l.issue_number''', (project_id,)).fetchall()
    sources = {}
    for link in links:
        sources.setdefault(link['requirement_id'], []).append({key: link[key] for key in ('issue_number', 'connection_revision')})
    for row in rows:
        row['sources'] = sources.get(row['id'], [])
        row['blocker'] = requirement_blocker(row)
        row['acceptance'] = 'blocked' if row['blocker'] else 'pending'
        handoff_id = row.pop('last_handoff')
        if row['stage'] == 'completed':
            handoff = db.execute('SELECT * FROM mad_handoffs WHERE run_id=%s', (handoff_id,)).fetchone()
            if handoff and handoff['role'] == 'testing' and handoff['verdict'] == 'passed' and not handoff_outdated(db, handoff):
                row['acceptance'] = 'accepted'
            else:
                row['blocker'] = 'evidence_outdated'
                row['acceptance'] = 'blocked'
    return rows


def requirement_blocker(requirement):
    if not requirement['group_id'] or requirement['stage'] == 'completed':
        return None
    if not requirement['owner']:
        return 'owner_missing'
    if requirement['state'] in {'waiting_decision', 'waiting_authorization', 'awaiting_takeover'}:
        return requirement['state']
    if requirement['run_status'] in {'failed', 'stale', 'cancelled'}:
        return requirement['run_status']
    return None


def summarize(plan, requirements, managers):
    counts = {state: 0 for state in ('accepted', 'pending', 'blocked')}
    for identifier in plan['requirement_ids']:
        counts[requirements[identifier]['acceptance']] += 1
    plan['counts'] = counts
    plan['owner'] = managers.get(plan['owner_id'])
    plan['actual_published_at'] = None
    if plan['status'] == 'committed':
        plan['release_state'] = 'awaiting_release' if counts['accepted'] and not (counts['pending'] or counts['blocked']) else 'awaiting_acceptance'
    else:
        plan['release_state'] = plan['status']
    return plan


def release_plan_router(url, identity):
    router = APIRouter(prefix='/api/projects/{project_id}/versions')
    authenticated = Depends(identity)

    @router.get('')
    def list_versions(project_id: UUID, user=authenticated):
        with connect(url) as db:
            membership(db, project_id, user['id'], write=True)
            requirements = project_requirements(db, project_id)
            by_id = {row['id']: row for row in requirements}
            managers = db.execute('''SELECT u.id,u.email FROM mad_memberships m JOIN mad_users u ON u.id=m.user_id
                WHERE m.project_id=%s AND m.role IN ('owner','admin') ORDER BY u.email''', (project_id,)).fetchall()
            emails = {row['id']: row['email'] for row in managers}
            plans = [summarize(row, by_id, emails) for row in db.execute(CURRENT_PLANS, (project_id,))]
            scheduled = {identifier for plan in plans if plan['status'] != 'cancelled' for identifier in plan['requirement_ids']}
            return {'plans': sorted(plans, key=lambda row: (row['target_date'] or date.max, row['name'], row['plan_id'])),
                    'requirements': requirements, 'managers': managers,
                    'unscheduled_ids': [row['id'] for row in requirements if row['id'] not in scheduled]}

    @router.put('/{plan_id}')
    def save_version(project_id: UUID, plan_id: UUID, data: VersionInput, user=authenticated):
        with connect(url) as db:
            membership(db, project_id, user['id'], write=True, manage=True)
            current = db.execute('''SELECT revision FROM mad_version_plan_revisions WHERE project_id=%s AND plan_id=%s
                ORDER BY revision DESC LIMIT 1''', (project_id, plan_id)).fetchone()
            revision = current['revision'] if current else 0
            if revision != data.expected_revision:
                raise Problem('version_conflict', 409)
            manager = db.execute('''SELECT 1 FROM mad_memberships WHERE project_id=%s AND user_id=%s
                AND role IN ('owner','admin')''', (project_id, data.owner_id)).fetchone()
            if not manager:
                raise Problem('version_owner_required', 422)
            found = db.execute('SELECT count(DISTINCT id) AS count FROM mad_requirements WHERE project_id=%s AND id=ANY(%s)',
                               (project_id, data.requirement_ids)).fetchone()['count']
            if found != len(data.requirement_ids):
                raise Problem('requirement_not_found', 404)
            if data.status != 'cancelled' and db.execute('''SELECT 1 FROM (''' + CURRENT_PLANS + ''') current
                WHERE plan_id<>%s AND status<>'cancelled' AND requirement_ids && %s::uuid[] LIMIT 1''',
                (project_id, plan_id, data.requirement_ids)).fetchone():
                raise Problem('requirement_already_scheduled', 409)
            if revision == 0:
                db.execute('INSERT INTO mad_version_plans (project_id,id) VALUES (%s,%s)', (project_id, plan_id))
            db.execute('''INSERT INTO mad_version_plan_revisions (project_id,plan_id,revision,name,owner_id,target_date,status,
                requirement_ids,milestones,required_checks,reason,actor_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (project_id, plan_id, revision+1, data.name, data.owner_id, data.target_date, data.status, data.requirement_ids,
                 Jsonb([item.model_dump(mode='json') for item in data.milestones]), Jsonb(data.required_checks), data.reason, user['id']))
            record(db, project_id, user['id'], f'{plan_id}:{revision+1}', 'version_planned' if revision == 0 else 'version_revised')
            return {'plan_id': plan_id, 'revision': revision+1}

    @router.get('/{plan_id}/history')
    def history(project_id: UUID, plan_id: UUID, before: int | None = Query(default=None, ge=1, le=2147483647), user=authenticated):
        with connect(url) as db:
            membership(db, project_id, user['id'], write=True)
            if not db.execute('SELECT 1 FROM mad_version_plans WHERE project_id=%s AND id=%s', (project_id, plan_id)).fetchone():
                raise Problem('version_not_found', 404)
            rows = db.execute('''SELECT r.*,u.email AS actor FROM mad_version_plan_revisions r JOIN mad_users u ON u.id=r.actor_id
                WHERE r.project_id=%s AND r.plan_id=%s AND (%s::integer IS NULL OR r.revision<%s)
                ORDER BY r.revision DESC LIMIT 26''', (project_id, plan_id, before, before)).fetchall()
            return {'revisions': rows[:25], 'next_before': rows[24]['revision'] if len(rows) > 25 else None}

    @router.get('/{plan_id}/history/{revision}')
    def historical_revision(project_id: UUID, plan_id: UUID, revision: int, user=authenticated):
        if not 1 <= revision <= 2147483647:
            raise Problem('version_not_found', 404)
        with connect(url) as db:
            membership(db, project_id, user['id'], write=True)
            row = db.execute('''SELECT r.*,u.email AS actor FROM mad_version_plan_revisions r JOIN mad_users u ON u.id=r.actor_id
                WHERE r.project_id=%s AND r.plan_id=%s AND r.revision=%s''', (project_id, plan_id, revision)).fetchone()
            if not row:
                raise Problem('version_not_found', 404)
            return row

    return router
