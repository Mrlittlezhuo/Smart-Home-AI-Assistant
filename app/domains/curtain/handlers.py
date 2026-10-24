from app.tools.common import run_get_state, run_set_state

HANDLERS = {
    "curtain.set_position": run_set_state,
    "curtain.get_state": run_get_state,
}
