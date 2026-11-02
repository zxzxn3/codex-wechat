"""Commit an explicitly requested identity change without replaying old delivery."""
from platform_support import IS_WINDOWS

REBIND_HINT = "请在本机运行 login -Rebind。" if IS_WINDOWS else "请在本机运行 login --rebind。"

PREFERENCE_KEYS = ("backendName", "backendSessions", "thread", "processingNotice", "completionNotice")

def commit_binding(store, account, *, rebind=False):
    previous = store.load("account") or {}
    pending = previous.get("bindingPending", False)
    changed = previous and (previous.get("bot"), previous.get("user")) != (account["bot"], account["user"])
    if (pending or changed) and not rebind:
        raise RuntimeError("微信身份已改变或绑定未完成；" + REBIND_HINT)
    if rebind:
        old = store.load("bridge", {})
        fresh = {"cursor": "", "seen": [], "inbox": [], "outbox": [], "running": False}
        fresh.update({key: old[key] for key in PREFERENCE_KEYS if key in old})
        # The marker blocks startup if either later write fails. Never allow an
        # old account to poll with the new delivery state after a partial commit.
        store.save("account", {"bindingPending": True})
        store.save("bridge", fresh)
    store.save("account", account)
