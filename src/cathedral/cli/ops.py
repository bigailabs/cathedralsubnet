"""`cathedral` operator CLI — health, weights, registration."""

from __future__ import annotations

import asyncio
import json

import httpx
import typer

app = typer.Typer(no_args_is_help=True, help="Cathedral operator CLI")


@app.command()
def health(validator_url: str = typer.Option("http://127.0.0.1:9333")) -> None:
    """Print the validator health snapshot as JSON."""
    body = asyncio.run(_get(f"{validator_url.rstrip('/')}/health"))
    typer.echo(json.dumps(body, indent=2, default=str))


@app.command()
def weights(validator_url: str = typer.Option("http://127.0.0.1:9333")) -> None:
    """Print the current weight-setting status as a single word."""
    body = asyncio.run(_get(f"{validator_url.rstrip('/')}/health"))
    typer.echo(body.get("weight_status") or "unknown")


@app.command()
def registration(validator_url: str = typer.Option("http://127.0.0.1:9333")) -> None:
    """Confirm the validator's hotkey is on the metagraph."""
    body = asyncio.run(_get(f"{validator_url.rstrip('/')}/health"))
    typer.echo(f"registered: {bool(body.get('registered'))}")


async def _get(url: str) -> dict[str, object]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        body: dict[str, object] = r.json()
        return body


if __name__ == "__main__":
    app()
