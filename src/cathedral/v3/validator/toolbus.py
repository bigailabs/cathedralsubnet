"""ToolBus — the observation primitive.

A ToolBus is the only side-effect channel a miner gets. Every (tool_name,
args) -> result is recorded with timing. Tools are wired per job from the
job's tool catalog; unknown tool names fail closed.

The bus is owned by the validator and handed to the miner for the
duration of one job. After the job, `bus.flush_calls()` returns the
ordered list of ToolCalls for inclusion in the Trajectory.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from cathedral.v3.types import ToolCall

ToolHandler = Callable[[dict[str, Any]], Any | Awaitable[Any]]


class ToolBus:
    """Routes tool calls, records the trace."""

    def __init__(
        self,
        handlers: dict[str, ToolHandler],
        timeout_seconds: float = 30.0,
    ) -> None:
        self._handlers = handlers
        self._calls: list[ToolCall] = []
        self._step = 0
        self._timeout = timeout_seconds

    @property
    def available(self) -> list[str]:
        return list(self._handlers.keys())

    async def call(self, tool_name: str, args: dict[str, Any] | None = None) -> Any:
        self._step += 1
        args = args or {}
        started = datetime.now(UTC)
        if tool_name not in self._handlers:
            ended = datetime.now(UTC)
            err = f"unknown tool: {tool_name}"
            self._calls.append(
                ToolCall(
                    step=self._step,
                    tool_name=tool_name,
                    args=args,
                    result=None,
                    ok=False,
                    error=err,
                    started_at=started,
                    ended_at=ended,
                    latency_ms=_ms(started, ended),
                )
            )
            raise ToolError(err)

        handler = self._handlers[tool_name]
        try:
            r = handler(args)
            if inspect.isawaitable(r):
                r = await asyncio.wait_for(r, timeout=self._timeout)
            ended = datetime.now(UTC)
            self._calls.append(
                ToolCall(
                    step=self._step,
                    tool_name=tool_name,
                    args=args,
                    result=_safe_result(r),
                    ok=True,
                    started_at=started,
                    ended_at=ended,
                    latency_ms=_ms(started, ended),
                )
            )
            return r
        except TimeoutError:
            ended = datetime.now(UTC)
            self._calls.append(
                ToolCall(
                    step=self._step,
                    tool_name=tool_name,
                    args=args,
                    result=None,
                    ok=False,
                    error="timeout",
                    started_at=started,
                    ended_at=ended,
                    latency_ms=_ms(started, ended),
                )
            )
            raise
        except ToolError:
            raise
        except Exception as e:
            ended = datetime.now(UTC)
            self._calls.append(
                ToolCall(
                    step=self._step,
                    tool_name=tool_name,
                    args=args,
                    result=None,
                    ok=False,
                    error=str(e),
                    started_at=started,
                    ended_at=ended,
                    latency_ms=_ms(started, ended),
                )
            )
            raise

    def flush_calls(self) -> list[ToolCall]:
        out = list(self._calls)
        self._calls = []
        self._step = 0
        return out


class ToolError(Exception):
    """Raised for known tool errors (vs. infra exceptions)."""


def _ms(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() * 1000.0


def _safe_result(r: Any) -> Any:
    """Limit what gets stored in the trace — keep it JSON-friendly."""
    if isinstance(r, (str, int, float, bool, type(None))):
        return r
    if isinstance(r, (list, tuple)):
        return [_safe_result(x) for x in r]
    if isinstance(r, dict):
        return {k: _safe_result(v) for k, v in r.items()}
    return repr(r)
