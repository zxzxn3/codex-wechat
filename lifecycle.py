"""Operation-specific lifecycle gates; callers still own locks and barriers.

Receiving input, preparing a turn, steering, and changing a session have different
rules. Keep those differences explicit rather than inventing one global busy bit.
"""
from records import InputPhase


def can_change_session(bridge, own_lock=False):
    """own_lock admits a caller that already holds the barrier, such as a command handler."""
    locked = bridge.session_lock.locked() and not own_lock
    return not (bridge.restarting or bridge.running or bridge.steering or bridge.active_turn()
                or bridge.state["inbox"] or locked)


def can_restart(bridge):
    current = bridge.state.get("currentInput")
    submitted = current and current.get("phase") != InputPhase.PREPARING
    return not (bridge.restarting or bridge.steering or bridge.active_turn() or submitted
                or (bridge.running and not current)
                or (bridge.session_lock.locked() and not bridge.backend_recovering))


def can_handoff_service(bridge):
    return not (bridge.restarting or bridge.running or bridge.steering or bridge.active_turn()
                or (bridge.session_lock.locked() and not bridge.backend_recovering)
                or bridge.backend.pending)


def can_prepare_input(bridge):
    # Authentication has its own wake-up path, checked separately by the worker.
    return not (bridge.restarting or bridge.steering or bridge.session_lock.locked()
                or bridge.input_paused or bridge.state.get("wechatContextPending"))


def can_steer_input(bridge):
    return bool((bridge.running or bridge.active_turn()) and bridge.state["inbox"]
                and not bridge.stopping and not bridge.restarting and not bridge.wechat_paused
                and not bridge.state.get("wechatContextPending"))
