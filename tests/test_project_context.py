from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from threading import Event, Thread
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx
import pytest

from server.config import Settings
from server.db import connect
from server.main import create_app
from server.agents import AgentService
from server.project_context import authorized_run, context_tool
from server.errors import Problem
from test_accounts import FakeMailAuth, HEADERS, ORIGIN, OWNER, MEMBER, login


class ModelEndpoint(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        self.server.arrived.set()
        self.server.release.wait(timeout=8)
        if self.server.fail or len(self.server.requests) == self.server.fail_on:
            self.send_response(400)
            self.end_headers()
            return
        messages = body["messages"]
        user = next(item for item in messages if item["role"] == "user")
        bundle = json.loads(user["content"] if isinstance(user["content"], str) else user["content"][0]["text"])
        results = {row["tool"]: row["result"] for row in bundle["previous_receipts"]}
        names = {}
        for message in messages:
            if message["role"] == "assistant":
                names.update({call["id"]: call["function"]["name"] for call in message.get("tool_calls", [])})
            if message["role"] == "tool":
                results[names[message["tool_call_id"]]] = json.loads(message["content"])
        if "list_context" not in results:
            name, args = "list_context", {}
        elif "read_context" not in results:
            doc = results["list_context"]["documents"][0]
            name, args = "read_context", {"id": doc["id"], "revision": doc["revision"], "start": 0, "end": 2000}
        elif bundle["role"] == "testing" and "read_handoff" not in results:
            name, args = "read_handoff", {}
        else:
            name, args = "submit_handoff", {"summary": "按当前资料完成独立对照", "evidence": [results["read_context"]["text"]],
                                            "verdict": "passed" if bundle["role"] == "testing" else "delivered"}
        if hasattr(self.server, 'plan'):
            name, args = self.server.plan(bundle, results, name, args)
        call_id = "call_" + name
        chunk = {"id": "fixture", "object": "chat.completion.chunk", "created": 1, "model": "context-fixture",
                 "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [{"index": 0, "id": call_id,
                 "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}, "finish_reason": None}]}
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
        chunk["choices"] = [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]
        self.wfile.write(("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())


@pytest.fixture
def context_setup(database, tmp_path):
    endpoint = ThreadingHTTPServer(("127.0.0.1", 0), ModelEndpoint)
    endpoint.requests = []
    endpoint.arrived = Event()
    endpoint.release = Event()
    endpoint.release.set()
    endpoint.fail = False
    endpoint.fail_on = -1
    thread = Thread(target=endpoint.serve_forever, daemon=True)
    thread.start()
    config = tmp_path / "model.env"
    config.write_text(f"ATLAS_API_KEY=fixture-key\nATLAS_BASE_URL=http://127.0.0.1:{endpoint.server_port}/v1\nATLAS_MODEL=context-fixture\n")
    fake = FakeMailAuth()
    settings = Settings(database, ORIGIN, "https://auth.test", "publishable-test", Fernet.generate_key().decode(),
                        str(config), os.environ.get("MAD_NODE_BINARY", "node"))
    app = create_app(settings, auth_transport=httpx.MockTransport(fake.reply))
    owner, tester = [TestClient(app, base_url=ORIGIN, headers=HEADERS) for _ in range(2)]
    login(owner, OWNER)
    login(tester, MEMBER)
    project = owner.post("/api/projects", json={"name": "上下文闭环"}).json()["id"]
    invite = str(uuid4())
    owner.post(f"/api/projects/{project}/invites", json={"id": invite, "email": MEMBER})
    tester.post(f"/api/invites/{invite}/accept")
    identities = [client.get("/api/auth/session").json()["user"]["id"] for client in (owner, tester)]
    for identity, role in zip(identities, ("development", "testing")):
        assert owner.put(f"/api/projects/{project}/roles/{identity}", json={"role": role}).status_code == 200
    document = {"id": str(uuid4()), "expected_revision": 0, "title": "开发约定", "source": "repo://README.md",
                "body": "交付文字必须保留资料来源。只处理已确认的需求。"}
    requirement = {"id": str(uuid4()), "expected_revision": 0, "title": "整理项目交接", "body": "读取开发约定后交付摘要。",
                   "criteria": ["给出开发约定中的原文证据"]}
    assert owner.put(f"/api/projects/{project}/context", json=document).status_code == 200
    assert owner.put(f"/api/projects/{project}/requirements", json=requirement).status_code == 200
    try:
        yield owner, tester, project, document, requirement, settings, endpoint, identities
    finally:
        endpoint.release.set()
        endpoint.shutdown()
        endpoint.server_close()
        thread.join(timeout=2)


def new_run(client, project, requirement):
    run = str(uuid4())
    response = client.post(f"/api/projects/{project}/runs", json={"id": run, "requirement_id": requirement["id"]})
    assert response.status_code == 201, response.text
    return run


def test_pi_sdk_shared_context_and_independent_handoff(context_setup, database):
    owner, tester, project, doc, requirement, settings, endpoint, identities = context_setup
    with ThreadPoolExecutor(4) as pool:
        agents = list(pool.map(lambda _: owner.get("/api/agent").json()["agent"]["id"], range(4)))
    assert len(set(agents)) == 1
    config = owner.get("/api/agent").json()["agent"]
    run = new_run(owner, project, requirement)
    assert owner.put("/api/agent", json={"expected_revision": config["revision"], "model": config["model"], "skills": []}).status_code == 200
    response = owner.post(f"/api/runs/{run}/execute")
    assert response.status_code == 202
    result = owner.get(f"/api/runs/{run}").json()
    assert result["status"] == "completed", result["events"]
    assert result["config"]["skills"] == ["project-context-v1", "handoff-v1"]
    assert result["checkpoint"] == 3
    assert result["references"][0]["document_id"] == doc["id"]
    assert tester.get(f"/api/runs/{run}").status_code == 404
    other_project = owner.post('/api/projects', json={'name': '另一项目'}).json()['id']
    other_doc = {**doc, 'id': str(uuid4()), 'body': '另一项目的同名私有资料'}
    owner.put(f'/api/projects/{other_project}/context', json=other_doc)
    with connect(database) as db:
        active = authorized_run(db, UUID(run), identities[0])
        with pytest.raises(Problem) as refused:
            context_tool(db, active, 'read_context', {'id': other_doc['id'], 'revision': 1, 'start': 0, 'end': 100})
        assert refused.value.code == 'document_not_found'
    assert "fixture-key" not in json.dumps(result)
    message = endpoint.requests[0]["messages"][-1]["content"]
    initial = json.loads(message if isinstance(message, str) else message[0]["text"])
    assert "documents" not in initial and doc["body"] not in json.dumps(initial)
    assert len(endpoint.requests) == 3
    assert owner.post(f"/api/runs/{run}/execute").status_code == 202
    assert len(endpoint.requests) == 3
    independent = new_run(tester, project, requirement)
    tester.post(f"/api/runs/{independent}/execute")
    assert tester.get(f"/api/runs/{independent}").json()["status"] == "completed"
    handoffs = tester.get(f"/api/projects/{project}/handoffs").json()
    assert {item["verdict"] for item in handoffs} == {"delivered", "passed"}
    assert len(handoffs) == 2
    owner.put(f"/api/projects/{project}/roles/{identities[0]}", json={"role": "testing"})
    assert owner.post(f"/api/projects/{project}/runs", json={"id": str(uuid4()), "requirement_id": requirement["id"]}).status_code == 409
    assert owner.get(f"/api/projects/{uuid4()}/context").status_code == 404


def test_version_confirmation_cancel_resume_and_revocation(context_setup, database):
    owner, tester, project, doc, requirement, settings, endpoint, identities = context_setup
    run = new_run(owner, project, requirement)
    owner.post(f"/api/runs/{run}/execute")
    doc["expected_revision"] = 1
    doc["body"] = "新约定要求说明验收条件和资料来源。"
    owner.put(f"/api/projects/{project}/context", json=doc)
    assert owner.post(f"/api/runs/{run}/execute").status_code == 409
    assert owner.get(f"/api/projects/{project}/handoffs").json()[0]["outdated"]
    assert owner.post(f"/api/runs/{run}/confirm", json={"expected_revision": 1, "documents": {doc["id"]: 1}}).status_code == 409
    assert owner.get(f"/api/projects/{project}/context/{doc['id']}/1").json()['body'] != doc['body']
    assert owner.post(f"/api/runs/{run}/confirm", json={"expected_revision": 1, "documents": {doc["id"]: 2}}).status_code == 200
    owner.post(f"/api/runs/{run}/execute")
    result = owner.get(f"/api/runs/{run}").json()
    assert result["status"] == "completed" and result["generation"] == 1
    assert {ref["revision"] for ref in result["references"]} == {1, 2}
    assert len(owner.get(f"/api/projects/{project}/handoffs").json()) == 1
    assert not owner.get(f"/api/projects/{project}/handoffs").json()[0]["outdated"]
    requirement["expected_revision"] = 1
    owner.put(f"/api/projects/{project}/requirements", json=requirement)
    assert owner.post(f"/api/runs/{run}/execute").status_code == 409
    assert owner.post(f"/api/runs/{run}/confirm", json={"expected_revision": 1}).status_code == 409
    assert owner.post(f"/api/runs/{run}/confirm", json={"expected_revision": 2, "documents": {doc["id"]: 2}}).status_code == 200

    endpoint.release.clear()
    endpoint.arrived.clear()
    service = AgentService(settings)
    with ThreadPoolExecutor(2) as pool:
        future = pool.submit(service.execute, run, identities[0])
        assert endpoint.arrived.wait(timeout=10)
        duplicate = pool.submit(service.execute, run, identities[0])
        duplicate.result(timeout=3)
        owner.post(f"/api/runs/{run}/cancel")
        future.result(timeout=8)
    assert owner.get(f"/api/runs/{run}").json()["status"] == "cancelled"
    endpoint.release.set()
    owner.post(f"/api/runs/{run}/execute")
    assert owner.get(f"/api/runs/{run}").json()["status"] == "completed"
    pending = new_run(tester, project, requirement)
    owner.delete(f"/api/projects/{project}/members/{identities[1]}")
    assert tester.get(f"/api/runs/{pending}").status_code == 404
    assert tester.post(f"/api/runs/{pending}/execute").status_code == 404
    assert tester.get(f"/api/projects/{project}/context").status_code == 404


def test_failed_provider_and_abandoned_process_restore_receipts(context_setup, database):
    owner, _, project, doc, requirement, settings, endpoint, identities = context_setup
    run = new_run(owner, project, requirement)
    endpoint.fail_on = 2
    owner.post(f"/api/runs/{run}/execute")
    interrupted = owner.get(f"/api/runs/{run}").json()
    assert interrupted["status"] == "failed" and interrupted["checkpoint"] == 1
    with connect(database) as db:
        db.execute("UPDATE mad_agent_runs SET status='running' WHERE id=%s", (run,))
    assert owner.get(f'/api/runs/{run}').json()['status'] == 'paused'
    AgentService(settings).execute(run, identities[0])
    restored = owner.get(f"/api/runs/{run}").json()
    assert restored["status"] == "completed" and restored["checkpoint"] == 3
    assert len(owner.get(f"/api/projects/{project}/handoffs").json()) == 1
    with connect(database) as db:
        db.execute("UPDATE mad_agent_runs SET status='running' WHERE id=%s", (run,))
    owner.put(f"/api/projects/{project}/context", json={**doc, "expected_revision": 1, "body": "更新后的资料"})
    assert owner.post(f"/api/runs/{run}/confirm", json={"expected_revision": 1, "documents": {doc["id"]: 2}}).status_code == 200
