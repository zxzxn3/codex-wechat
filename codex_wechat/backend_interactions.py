"""In-memory ownership of native questions and approvals."""
from collections import deque
import secrets

from .questions import ASYNC_QUESTION_METHOD, QUESTION_METHOD

PENDING_TOKEN_ALPHABET = "023456789abcdefghjkmnpqrstuvwxyz"
PENDING_TOKEN_LENGTH = 3
PENDING_TOKEN_RECENT = 128


class InteractionRegistry:
    """Keep pending interaction tokens and their bounded invalidation history."""

    def __init__(self):
        self.items = {}
        self.recent_tokens = deque(maxlen=PENDING_TOKEN_RECENT)

    def new_token(self):
        while True:
            token = "".join(secrets.choice(PENDING_TOKEN_ALPHABET)
                            for _ in range(PENDING_TOKEN_LENGTH))
            if token not in self.items and token not in self.recent_tokens:
                self.recent_tokens.append(token)
                return token

    def create(self, request):
        token = self.new_token()
        self.items[token] = request
        return token

    def replace(self, items):
        self.items = dict(items)

    def clear(self):
        self.items.clear()

    def get(self, token, default=None):
        return self.items.get(token, default)

    def pop(self, token, default=None):
        return self.items.pop(token, default)

    def has_request_id(self, request_id):
        return any(request.get("id") == request_id for request in self.items.values())

    def resolve(self, request_id, thread_id):
        for token, request in list(self.items.items()):
            if (request.get("id") == request_id
                    and request.get("params", {}).get("threadId") == thread_id):
                self.items.pop(token, None)

    def discard_answered(self, question_ids):
        if not question_ids:
            return
        for token, request in list(self.items.items()):
            state = request.get("_async")
            if state and any(question["id"] in question_ids
                             for question in state["questions"]):
                self.items.pop(token, None)

    def __len__(self):
        return len(self.items)

    def __bool__(self):
        return bool(self.items)


def interaction_is_question(request):
    return request.get("method") in (QUESTION_METHOD, ASYNC_QUESTION_METHOD)
