from server.db import connect
from server.errors import Problem
from server.project_context import authorized_run, context_changed
from server.requirement_groups import check_group_run
from server.resources import missing_resources


def enqueue(db, run):
    current = db.execute('SELECT * FROM mad_resume_jobs WHERE run_id=%s', (run['id'],)).fetchone()
    if current and current['execution_revision'] == run['execution_revision'] and current['state'] in {'queued', 'running'}:
        return current
    return db.execute('''INSERT INTO mad_resume_jobs (run_id,user_id,execution_revision) VALUES (%s,%s,%s)
        ON CONFLICT(run_id) DO UPDATE SET user_id=excluded.user_id,execution_revision=excluded.execution_revision,
        revision=mad_resume_jobs.revision+1,state='queued',reason=NULL,updated_at=now() RETURNING *''',
                      (run['id'], run['user_id'], run['execution_revision'])).fetchone()


def block_run(db, run, reason):
    if reason == 'authorization_required':
        db.execute("UPDATE mad_agent_runs SET status='waiting_authorization' WHERE id=%s", (run['id'],))
        if run['group_id']:
            db.execute("UPDATE mad_requirement_groups SET state='waiting_authorization' WHERE id=%s AND revision=%s",
                       (run['group_id'], run['group_revision']))


def process_job(service, run_id):
    with connect(service.url) as lease:
        key = 'mad:resume:' + str(run_id)
        if not lease.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS acquired', (key,)).fetchone()['acquired']:
            return False
        lease.commit()
        try:
            with connect(service.url) as db:
                job = db.execute('SELECT * FROM mad_resume_jobs WHERE run_id=%s', (run_id,)).fetchone()
                if not job or job['state'] not in {'queued', 'running'}:
                    return False
                try:
                    run = authorized_run(db, run_id, job['user_id'], lock=True)
                    if run['execution_revision'] != job['execution_revision']:
                        raise Problem('execution_replaced', 409)
                    if run['status'] == 'completed':
                        db.execute("UPDATE mad_resume_jobs SET state='completed',reason=NULL WHERE run_id=%s", (run_id,))
                        return True
                    check_group_run(db, run)
                    if context_changed(db, run):
                        raise Problem('context_changed', 409)
                    if run['status'] in {'cancelled','waiting_decision'}:
                        raise Problem('decision_pending' if run['status']=='waiting_decision' else 'run_cancelled', 409)
                    if missing_resources(db, run):
                        raise Problem('authorization_required', 409)
                except Problem as exc:
                    db.execute("UPDATE mad_resume_jobs SET state='blocked',reason=%s,updated_at=now() WHERE run_id=%s", (exc.code, run_id))
                    if 'run' in locals():
                        block_run(db, run, exc.code)
                    return True
                db.execute("UPDATE mad_resume_jobs SET state='running',attempts=attempts+1,updated_at=now() WHERE run_id=%s", (run_id,))
                if run['group_id']:
                    db.execute("UPDATE mad_requirement_groups SET state='active' WHERE id=%s AND revision=%s", (run['group_id'],run['group_revision']))
            result = service.execute(run_id, job['user_id'])
            with connect(service.url) as db:
                current = db.execute('SELECT * FROM mad_agent_runs WHERE id=%s', (run_id,)).fetchone()
                if result is False or service.stopping.is_set():
                    state, reason = 'queued', None
                elif current['status'] == 'completed':
                    state, reason = 'completed', None
                elif current['status'] == 'waiting_authorization':
                    state, reason = 'blocked', 'authorization_required'
                else:
                    last=db.execute("SELECT data->>'reason' AS reason FROM mad_run_events WHERE run_id=%s AND kind='stopped' ORDER BY id DESC LIMIT 1",(run_id,)).fetchone()
                    state, reason = 'blocked', (last['reason'] if last else current['status'])
                db.execute('''UPDATE mad_resume_jobs SET state=%s,reason=%s,updated_at=now()
                    WHERE run_id=%s AND revision=%s''', (state, reason, run_id, job['revision']))
            if state == 'completed' and current['group_id'] and not service.stopping.is_set():
                service.drive(current['group_id'])
            return True
        finally:
            lease.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))', (key,))


def drain_jobs(service, limit=20):
    with connect(service.url) as db:
        rows = db.execute("SELECT run_id FROM mad_resume_jobs WHERE state IN ('queued','running') ORDER BY updated_at LIMIT %s", (limit,)).fetchall()
    for row in rows:
        if service.stopping.is_set():
            break
        process_job(service, row['run_id'])
