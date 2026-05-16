"""Validator: dispatches jobs, owns the ToolBus, observes trajectories."""

from cathedral.v3.validator.toolbus import ToolBus, ToolError, ToolHandler


# Observer imports MinerAgent which imports ToolBus — keep observer
# importable from this package but as a deferred attribute to avoid
# the miner <-> validator cycle.
def __getattr__(name):
    if name == "Validator":
        from cathedral.v3.validator.observer import Validator

        return Validator
    raise AttributeError(name)


__all__ = ["ToolBus", "ToolError", "ToolHandler", "Validator"]
