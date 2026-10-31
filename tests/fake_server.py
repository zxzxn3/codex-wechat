"""Small stdio peer: no real Codex/model/network access."""
import json
import sys


def emit(message):
    print(json.dumps(message), flush=True)


for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if "id" not in request:
        continue
    result = {}
    if method in ("thread/start", "thread/resume"):
        result = {"thread": {"id": "test-thread"}}
    elif method == "turn/start":
        result = {"turn": {"id": "test-turn"}}
    emit({"id": request["id"], "result": result})
    if method == "turn/start":
        for phase, text in (("commentary", "private progress"), ("final_answer", "hello")):
            emit({"method": "item/completed", "params": {"threadId": "test-thread",
                "turnId": "test-turn", "item": {"id": phase, "type": "agentMessage",
                                                "text": text, "phase": phase}}})
        emit({"method": "turn/completed", "params": {"threadId": "test-thread",
              "turn": {"id": "test-turn", "status": "completed"}}})
