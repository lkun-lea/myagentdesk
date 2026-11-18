import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
from threading import Thread
import time

from dotenv import dotenv_values

from server.errors import Problem


SKILLS = {
    "project-context-v1": "先读取项目资料目录，再按需读取原文。将资料中的要求与当前需求、验收条件对照。保留来源。",
    "handoff-v1": "研发交接须给出可核对的结论和证据。测试先读研发交接，再自行读取资料对照验收。缺乏证据时拒绝通过。",
}


def model_configuration(settings):
    values = dotenv_values(settings.model_env_file) if settings.model_env_file else os.environ
    key = values.get("ATLAS_API_KEY") or values.get("MAD_MODEL_API_KEY", "")
    base = values.get("ATLAS_BASE_URL") or values.get("MAD_MODEL_BASE_URL", "")
    model = values.get("ATLAS_MODEL") or values.get("MAD_MODEL", "")
    return {"key": key or "", "base": base or "", "model": model or ""}


class PiRuntime:
    def __init__(self, settings):
        self.settings = settings

    def configuration(self):
        return model_configuration(self.settings)

    def run(self, bundle, tools, callback, check):
        config = self.configuration()
        if not all(config.values()):
            raise Problem("model_unavailable", 503)
        worker = Path(__file__).resolve().parent.parent / "worker" / "pi.mjs"
        environment = {key: value for key, value in os.environ.items()
                       if not any(secret in key.upper() for secret in ("API_KEY", "SECRET", "PASSWORD", "TOKEN"))}
        process = subprocess.Popen([self.settings.node_binary, str(worker)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                   env=environment, cwd=worker.parent.parent,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        queue = Queue()

        def read():
            try:
                for line in process.stdout:
                    if len(line) > 150000:
                        break
                    queue.put(line)
            finally:
                queue.put(None)

        thread = Thread(target=read, daemon=True)
        thread.start()
        try:
            process.stdin.write(json.dumps({"type": "start", "config": config, "bundle": bundle, "tools": tools}, default=str) + "\n")
            process.stdin.flush()
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                check()
                try:
                    line = queue.get(timeout=0.25)
                except Empty:
                    continue
                if line is None:
                    raise Problem("model_failed", 503)
                message = json.loads(line)
                if message["type"] == "tool":
                    value = callback(message["id"], message["name"], message["arguments"])
                    process.stdin.write(json.dumps({"type": "result", "id": message["id"], "value": value}, default=str) + "\n")
                    process.stdin.flush()
                elif message["type"] == "event":
                    callback(None, "runtime_event", message["value"])
                elif message["type"] == "done":
                    if not message["ok"]:
                        raise Problem("model_failed", 503)
                    return
            raise Problem("model_timeout", 503)
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=4)
            process.stdin.close()
            process.stdout.close()
            thread.join(timeout=1)


TOOLS = [
    {"name": "list_context", "description": "列出当前项目资料的标题、摘要、来源、版本，按需检索。", "parameters": {
        "type": "object", "properties": {"query": {"type": "string"}}, "additionalProperties": False}},
    {"name": "read_context", "description": "读取当前项目某个资料的指定版本片段，最多4000字符，并保存引用。", "parameters": {
        "type": "object", "properties": {"id": {"type": "string"}, "revision": {"type": "integer"},
        "start": {"type": "integer"}, "end": {"type": "integer"}}, "required": ["id", "revision", "start", "end"], "additionalProperties": False}},
    {"name": "read_handoff", "description": "独立测试读取当前需求修订对应的研发交接和证据。", "parameters": {
        "type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "submit_handoff", "description": "提交本阶段结论和具体证据。测试必须先读取研发交接并独立读资料。", "parameters": {
        "type": "object", "properties": {"summary": {"type": "string"}, "evidence": {"type": "array", "items": {"type": "string"}},
        "verdict": {"type": "string", "enum": ["delivered", "passed", "rejected"]}}, "required": ["summary", "evidence", "verdict"], "additionalProperties": False}},
]
