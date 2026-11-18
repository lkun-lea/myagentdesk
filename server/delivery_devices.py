from hashlib import sha256
from hmac import compare_digest
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, StringConstraints

from server.db import connect
from server.errors import Problem


class Pairing(BaseModel):
    id: UUID
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    secret: Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$')]


class Activation(BaseModel):
    code: Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$')]
    credential: Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$')]


def token_matches(digest, authorization):
    return authorization.startswith('Bearer ') and compare_digest(digest, sha256(authorization[7:].encode()).digest())


def authorize_device(db, identifier, authorization):
    row = db.execute('SELECT * FROM mad_delivery_devices WHERE id=%s AND revoked_at IS NULL FOR SHARE',
                     (identifier,)).fetchone()
    if not row or not row['credential_digest'] or not token_matches(row['credential_digest'], authorization):
        raise Problem('device_not_paired', 401)
    return row


def delivery_devices_router(database, identity):
    router = APIRouter(prefix='/api')
    authenticated = Depends(identity)

    @router.get('/delivery/devices')
    def devices(user=authenticated):
        with connect(database) as db:
            return db.execute('''SELECT id,label,created_at,revoked_at,credential_digest IS NOT NULL AS activated FROM mad_delivery_devices
                                 WHERE actor=%s ORDER BY created_at DESC''', (user['id'],)).fetchall()

    @router.post('/delivery/devices')
    def pair(data: Pairing, user=authenticated):
        digest = sha256(data.secret.encode()).digest()
        with connect(database) as db:
            db.execute('''INSERT INTO mad_delivery_devices (id,actor,label,secret_digest) VALUES (%s,%s,%s,%s)
                          ON CONFLICT DO NOTHING''', (data.id, user['id'], data.label, digest))
            row = db.execute('SELECT * FROM mad_delivery_devices WHERE id=%s', (data.id,)).fetchone()
            if row['actor'] != user['id'] or row['label'] != data.label or row['revoked_at'] or not compare_digest(row['secret_digest'], digest):
                raise Problem('operation_conflict', 409)
            return {'id': row['id'], 'label': row['label']}

    @router.delete('/delivery/devices/{device}')
    def revoke(device: UUID, user=authenticated):
        with connect(database) as db:
            row = db.execute('''UPDATE mad_delivery_devices SET revoked_at=coalesce(revoked_at,now())
                                WHERE id=%s AND actor=%s RETURNING id''', (device, user['id'])).fetchone()
            if not row:
                raise Problem('device_not_paired', 404)
            return {'revoked': True}

    @router.post('/executor/devices/{device}/activate')
    def activate(device: UUID, data: Activation):
        digest = sha256(data.credential.encode()).digest()
        with connect(database) as db:
            row = db.execute('''SELECT * FROM mad_delivery_devices WHERE id=%s AND revoked_at IS NULL FOR UPDATE''',
                             (device,)).fetchone()
            if not row or not token_matches(row['secret_digest'], 'Bearer ' + data.code):
                raise Problem('device_not_paired', 401)
            if row['credential_digest'] is not None:
                if not compare_digest(row['credential_digest'], digest):
                    raise Problem('device_claimed', 409)
            else:
                db.execute('UPDATE mad_delivery_devices SET credential_digest=%s WHERE id=%s', (digest, device))
            return {'label': row['label']}

    @router.get('/executor/devices/{device}/dispatches')
    def queue(device: UUID, authorization: Annotated[str, Header()] = ''):
        with connect(database) as db:
            paired = authorize_device(db, device, authorization)
            rows = db.execute('''SELECT d.id,d.group_id,d.project_id,d.stage,d.input,d.status,d.device,
                                      g.source,r.title FROM mad_delivery_dispatches d
                JOIN mad_requirement_groups g ON g.id=d.group_id AND g.revision=d.group_revision
                JOIN mad_memberships m ON m.project_id=d.project_id AND m.user_id=d.actor
                JOIN mad_project_roles p ON p.project_id=d.project_id AND p.user_id=d.actor AND p.role=d.stage
                JOIN mad_requirements r ON r.project_id=g.project_id AND r.id=g.requirement_id AND r.revision=g.requirement_revision
                JOIN mad_github_connections c ON c.project_id=d.project_id AND c.enabled
                    AND c.revision=(d.input->>'connection_revision')::int
                JOIN LATERAL (SELECT revision FROM mad_version_plan_revisions v WHERE v.project_id=d.project_id
                    AND v.plan_id=(d.input->>'plan_id')::uuid ORDER BY revision DESC LIMIT 1) v
                    ON v.revision=(d.input->>'plan_revision')::int
                WHERE d.actor=%s AND d.desktop AND g.stage=d.stage AND g.state='active'
                    AND d.actor=CASE d.stage WHEN 'development' THEN g.development_id ELSE g.testing_id END
                    AND (d.status='queued' OR d.status='running' AND d.device=%s)
                ORDER BY d.created_at DESC LIMIT 100''', (paired['actor'], device)).fetchall()
            return {'label': paired['label'], 'dispatches': rows}

    return router
