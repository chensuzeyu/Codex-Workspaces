#!/usr/bin/env python3
"""Offline protocol fixture: never contacts a provider or reads real credentials."""
import json
import os
import sys
import time

args = sys.argv[1:]
if "--version" in args:
    print("codex-cli fixture")
elif "app-server" in args:
    for line in sys.stdin:
        request = json.loads(line)
        method = request.get("method")
        if method == "initialize":
            print(json.dumps({"id": request["id"], "result": {"platformOs": "linux"}}), flush=True)
        elif method == "account/read":
            print(json.dumps({"id": request["id"], "result": {"account": {"type": "chatgpt", "email": "user@example.invalid"}}}), flush=True)
        elif method == "thread/start":
            print(json.dumps({"id": request["id"], "result": {"thread": {"id": "fixture", "ephemeral": True}}}), flush=True)
        elif method == "turn/start":
            print(json.dumps({"id": request["id"], "result": {}}), flush=True)
            print(json.dumps({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "CODEX_WORKSPACE_APP_SERVER_OK"}}}), flush=True)
            print(json.dumps({"method": "turn/completed", "params": {"turn": {"status": "completed"}}}), flush=True)
elif "exec" in args:
    if os.environ.get("CWS_FIXTURE_TIMEOUT"):
        time.sleep(30)
    else:
        print(json.dumps({"type": "turn.started"}))
        print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "CODEX_WORKSPACE_OK"}}))
        print(json.dumps({"type": "turn.completed"}))
else:
    print(json.dumps({key: os.environ.get(key) for key in ["CODEX_HOME", "CWS_PROFILE", "HTTP_PROXY", "HTTPS_PROXY", "OPENAI_API_KEY"]}))
