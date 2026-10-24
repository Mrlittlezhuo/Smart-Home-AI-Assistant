from app.tools.common import run_get_state, run_set_state

HANDLERS = {
    "light.set_state": run_set_state,
    "light.get_state": run_get_state,
}
