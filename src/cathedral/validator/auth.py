"""Bearer-token dependency for mutating routes (issue #1)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Header, HTTPException, status


def make_bearer_dep(expected_token: str) -> Callable[[str], Awaitable[None]]:
    async def dep(authorization: str = Header(default="")) -> None:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer")
        provided = authorization.removeprefix("Bearer ").strip()
        if provided != expected_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad bearer")

    return dep
