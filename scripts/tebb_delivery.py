"""Run a claimed MyAgentDesk dispatch in the explicitly approved tebb sandbox."""
from argparse import ArgumentParser
from base64 import b64decode, b64encode
from getpass import getpass
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
from shlex import join
from subprocess import check_output
from time import sleep
from urllib.parse import urlparse
from uuid import UUID, uuid4, uuid5
from zipfile import ZipFile
import json
import os
import re
import sys

import httpx

MAX_ARTIFACT = 512 * 1024


def git(repo, *args):
    return check_output(['git', '-C', str(repo), *args], timeout=40)


def candidate_source(repo, frozen):
    base, commit = frozen['base'], frozen['commit']
    if any(not re.fullmatch(r'[a-f0-9]{40}', item) for item in (base, commit)):
        raise ValueError('Expected full Git commits')
    name = git(repo, 'config', '--get', 'remote.origin.url').decode().strip().removesuffix('.git')
    if name not in {f"https://github.com/{frozen['repository']}", f"git@github.com:{frozen['repository']}"}:
        raise ValueError('The approved clone does not match the connected repository')
    if git(repo, 'merge-base', base, commit).decode().strip() != base:
        raise ValueError('The candidate does not descend from the frozen base')
    patch = git(repo, '-c', 'core.quotePath=true', 'diff', '--no-ext-diff', '--no-textconv', '--no-renames',
                '--no-color', '--src-prefix=a/', '--dst-prefix=b/', '--binary', base, commit, '--')
    if not patch or len(patch) > MAX_ARTIFACT:
        raise ValueError('The candidate patch must be between 1 byte and 512 KiB')
    return patch


def install_snapshot(repo, commit, folder):
    ready = folder.with_suffix('.ready')
    if ready.is_file():
        if ready.read_text(encoding='ascii') != commit:
            raise ValueError('The local workspace belongs to another candidate')
        return
    archive = git(repo, 'archive', '--format=zip', commit)
    with ZipFile(BytesIO(archive)) as files:
        total = sum(entry.file_size for entry in files.infolist())
        if total > 96 * 1024 * 1024:
            raise ValueError('Candidate workspace exceeds the pilot limit')
        for entry in files.infolist():
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename:
                raise ValueError('Unexpected candidate path')
            if entry.external_attr >> 28 == 0o12:
                raise ValueError('Symlinks are unavailable in the pilot workspace')
        location = str(folder.resolve())
        if os.name == 'nt' and not location.startswith('\\\\?\\'):
            location = '\\\\?\\UNC\\' + location[2:] if location.startswith('\\\\') else '\\\\?\\' + location
        files.extractall(location)
    ready.write_text(commit, encoding='ascii')


def run_command(manager, session, identifier, key, argv, environment):
    if not argv or not all(isinstance(item, str) and item for item in argv):
        raise ValueError(f'Invalid local recipe for {key}')
    command = join(argv)
    if len(command) > 4000:
        raise ValueError('Local command is too long to retain as evidence')
    request_id = uuid5(identifier, key).hex
    job = manager.start(command, session, timeout=90, request_id=request_id)['job']
    while job['state'] in {'starting', 'running', 'stopping'}:
        sleep(.25)
        job = manager.get(job['id'])
    output, offset = bytearray(), 0
    while len(output) < 16000:
        item = manager.read(job['id'], offset)
        output.extend(b64decode(item['data']))
        if offset == item['offset']:
            break
        offset = item['offset']
    result = {'key': key, 'run': job['id'], 'command': command, 'environment': environment,
              'exit_code': job['exit_code'] if job['exit_code'] is not None else -1,
              'output': bytes(output[:15000]).decode('utf-8', 'replace')}
    if len(output) > 15000:
        result['output'] += '\n[output truncated]'
    status = {'succeeded': 'succeeded', 'failed': 'failed', 'cancelled': 'cancelled'}.get(job['state'], 'needs_review')
    return status, result


def execute(args, dispatch, device):
    sys.path.insert(0, str(args.tebb.resolve(strict=True)))
    os.environ['DESK_COMPANION_DATA_DIR'] = str((args.state / 'tebb').resolve())
    from desk_companion.projects.terminal import manager, workspace

    terminal = manager()
    sandbox = terminal.status()
    if not sandbox['ready']:
        raise RuntimeError(sandbox['error'])
    frozen = dispatch['input']
    patch = candidate_source(args.repo, frozen)
    digest = sha256(patch).hexdigest()
    testing = dispatch['stage'] == 'testing'
    if testing and digest != frozen['digest']:
        raise ValueError('Candidate differs from the approved artifact')

    session = 'mad-delivery-' + args.dispatch.hex
    folder = workspace(session)
    install_snapshot(args.repo, frozen['commit'], folder)
    recipe = json.loads(args.recipe.read_text(encoding='utf-8-sig'))
    keys = ([f'criterion:{i+1}' for i in range(len(frozen['criteria']))] +
            [f'check:{i+1}' for i in range(len(frozen['checks']))]) if testing else ['implementation']
    if any(key not in recipe for key in keys):
        raise ValueError('The local recipe must cover all frozen criteria and release checks')

    checks = []
    status = 'succeeded'
    for key in keys:
        status, evidence = run_command(terminal, session, args.dispatch, key, recipe[key],
                                       f"{sandbox['provider']} / {sandbox['distro']}")
        checks.append(evidence)
        if status != 'succeeded':
            break
    return {'device': str(device), 'commit': frozen['commit'], 'status': status, 'digest': digest,
            'artifact': b64encode(patch).decode() if not testing and status == 'succeeded' else None, 'checks': checks}


def receipt_file(path, data):
    pending = path.with_suffix('.tmp')
    pending.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    pending.replace(path)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--server', required=True)
    parser.add_argument('--dispatch', required=True, type=UUID)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--tebb', required=True, type=Path)
    parser.add_argument('--recipe', required=True, type=Path)
    parser.add_argument('--state', required=True, type=Path)
    args = parser.parse_args()
    target = urlparse(args.server)
    if (not target.hostname or target.username or target.password or target.query or target.fragment or target.path not in ('', '/')
            or not (target.scheme == 'https' or target.scheme == 'http' and target.hostname in {'127.0.0.1', 'localhost', '::1'})):
        parser.error('Use HTTPS or a loopback HTTP origin')
    args.state.mkdir(parents=True, exist_ok=True)
    marker = args.state / 'device'
    try:
        with marker.open('x', encoding='ascii') as file:
            file.write(str(uuid4()))
    except FileExistsError:
        pass
    device = UUID(marker.read_text(encoding='ascii'))
    origin = args.server.rstrip('/')
    secret = os.environ.get('MAD_DISPATCH_SECRET') or getpass('Dispatch token: ')
    headers = {'Origin': origin, 'X-MAD-Request': '1', 'Authorization': 'Bearer ' + secret}
    path = f'/api/executor/{args.dispatch}'
    saved = args.state / f'{args.dispatch}.json'
    with httpx.Client(base_url=origin, headers=headers, timeout=20) as client:
        if saved.exists():
            receipt = json.loads(saved.read_text(encoding='utf-8'))
        else:
            response = client.post(path + '/claim', json={'device': str(device)})
            response.raise_for_status()
            dispatch = response.json()
            if dispatch['status'] != 'running':
                print(dispatch['status'])
                return
            receipt = execute(args, dispatch, device)
            receipt_file(saved, receipt)
        response = client.put(path + '/receipt', json=receipt)
        response.raise_for_status()
        print(json.dumps({'dispatch': str(args.dispatch), 'status': response.json()['status'],
                          'commit': receipt['commit'], 'digest': receipt['digest']}))


if __name__ == '__main__':
    main()
