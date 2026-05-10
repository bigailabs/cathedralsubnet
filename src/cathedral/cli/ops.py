"""`cathedral` operator CLI — health, weights, registration, chain check."""

from __future__ import annotations

import asyncio
import json

import httpx
import typer

app = typer.Typer(no_args_is_help=True, help="Cathedral operator CLI")


@app.callback()
def _root() -> None:
    """Cathedral operator CLI — inspect health, weights, registration, chain."""


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


@app.command(name="chain-check")
def chain_check(
    config: str = typer.Option("config/testnet.toml", "--config", "-c"),
) -> None:
    """Smoke-test the Bittensor chain connection without starting the validator.

    Reads the validator config, opens a Subtensor connection, prints the
    current block, the validator's registration status, and the size of the
    metagraph. Exits non-zero on any failure.
    """
    from cathedral.chain import BittensorChain  # heavy import
    from cathedral.config import ValidatorSettings

    settings = ValidatorSettings.from_toml(config)
    chain = BittensorChain(
        network=settings.network.name,
        netuid=settings.network.netuid,
        wallet_name=settings.network.wallet_name,
        wallet_hotkey=settings.network.validator_hotkey,
        wallet_path=settings.network.wallet_path,
    )

    async def _run() -> None:
        block = await chain.current_block()
        registered = await chain.is_registered()
        mg = await chain.metagraph()
        typer.echo(
            json.dumps(
                {
                    "network": settings.network.name,
                    "netuid": settings.network.netuid,
                    "wallet_hotkey": settings.network.validator_hotkey,
                    "current_block": block,
                    "registered": registered,
                    "metagraph_block": mg.block,
                    "metagraph_size": len(mg.miners),
                },
                indent=2,
            )
        )

    asyncio.run(_run())


async def _get(url: str) -> dict[str, object]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        body: dict[str, object] = r.json()
        return body


if __name__ == "__main__":
    app()
