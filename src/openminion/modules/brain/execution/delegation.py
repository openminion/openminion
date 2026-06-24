def _runner_delegate(name: str, runner, *args, **kwargs):
    override = getattr(runner, name, None)
    if callable(override):
        return override(*args, **kwargs)
    from openminion.modules.brain.runner.delegates import RUNNER_DELEGATES

    return RUNNER_DELEGATES[name](runner, *args, **kwargs)
