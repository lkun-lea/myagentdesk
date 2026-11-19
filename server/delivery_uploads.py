"""Immutable upload manifests and verified chunks scoped to an existing dispatch."""
from base64 import b64decode
from hashlib import sha256
from typing import Annotated
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, StringConstraints, model_validator

from server.errors import Problem

CHUNK_SIZE = 32 * 1024
MAX_ARTIFACT = 512 * 1024
Digest = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$')]


class Manifest(BaseModel):
    device: UUID
    digest: Digest
    size: int = Field(ge=1, le=MAX_ARTIFACT)
    chunk_bytes: int = Field(default=CHUNK_SIZE, ge=128, le=CHUNK_SIZE)
    chunks: list[Digest] = Field(min_length=1, max_length=256)

    @model_validator(mode='after')
    def count_matches_size(self):
        if len(self.chunks) != (self.size + self.chunk_bytes - 1) // self.chunk_bytes:
            raise ValueError('Unexpected chunk count')
        return self


class Chunk(BaseModel):
    device: UUID
    data: str = Field(min_length=4, max_length=((CHUNK_SIZE + 2) // 3) * 4)


def get_manifest(db, dispatch):
    return db.execute('SELECT * FROM mad_delivery_uploads WHERE dispatch=%s', (dispatch,)).fetchone()


def missing_chunks(db, manifest):
    received = {row['position'] for row in db.execute(
        'SELECT position FROM mad_delivery_chunks WHERE dispatch=%s', (manifest['dispatch'],))}
    return [index for index in range(len(manifest['chunks'])) if index not in received]


def declare(db, dispatch, data):
    manifest = get_manifest(db, dispatch['id'])
    if manifest is None:
        if dispatch['status'] != 'running':
            raise Problem('stage_changed', 409)
        manifest = db.execute('''INSERT INTO mad_delivery_uploads (dispatch,digest,size,chunk_bytes,chunks)
                              VALUES (%s,%s,%s,%s,%s) RETURNING *''',
                              (dispatch['id'], data.digest, data.size, data.chunk_bytes, Jsonb(data.chunks))).fetchone()
    if (manifest['digest'], manifest['size'], manifest['chunk_bytes'], manifest['chunks']) != (data.digest, data.size, data.chunk_bytes, data.chunks):
        raise Problem('operation_conflict', 409)
    if dispatch['artifact'] is not None:
        if (len(dispatch['artifact']) != data.size or sha256(dispatch['artifact']).hexdigest() != data.digest):
            raise Problem('artifact_mismatch', 409)
        return {'missing': [], 'received': len(data.chunks), 'total': len(data.chunks)}
    if dispatch['status'] != 'running':
        raise Problem('stage_changed', 409)
    missing = missing_chunks(db, manifest)
    return {'missing': missing, 'received': len(data.chunks) - len(missing), 'total': len(data.chunks)}


def store_chunk(db, dispatch, position, data):
    manifest = get_manifest(db, dispatch['id'])
    if not manifest or not 0 <= position < len(manifest['chunks']):
        raise Problem('artifact_upload_required', 409)
    try:
        chunk = b64decode(data.data, validate=True)
    except ValueError as exc:
        raise Problem('artifact_mismatch', 422) from exc
    offset = position * manifest['chunk_bytes']
    size = min(manifest['chunk_bytes'], manifest['size'] - offset)
    if len(chunk) != size or sha256(chunk).hexdigest() != manifest['chunks'][position]:
        raise Problem('artifact_mismatch', 422)
    if dispatch['artifact'] is None:
        if dispatch['status'] != 'running':
            raise Problem('stage_changed', 409)
        db.execute('''INSERT INTO mad_delivery_chunks (dispatch,position,payload) VALUES (%s,%s,%s)
                      ON CONFLICT DO NOTHING''', (dispatch['id'], position, chunk))
    elif dispatch['artifact'][offset:offset + size] != chunk:
        raise Problem('artifact_mismatch', 409)
    return {'index': position, 'accepted': True}


def assemble(db, dispatch, digest):
    manifest = get_manifest(db, dispatch)
    if not manifest:
        raise Problem('artifact_required', 409)
    if manifest['digest'] != digest:
        raise Problem('artifact_mismatch', 422)
    chunks = db.execute('SELECT position,payload FROM mad_delivery_chunks WHERE dispatch=%s ORDER BY position',
                        (dispatch,)).fetchall()
    if [row['position'] for row in chunks] != list(range(len(manifest['chunks']))):
        raise Problem('artifact_incomplete', 409)
    payload = b''.join(row['payload'] for row in chunks)
    if len(payload) != manifest['size'] or sha256(payload).hexdigest() != digest:
        raise Problem('artifact_mismatch', 422)
    return payload
