"""`cathedral-validator` CLI."""

from __future__ import annotations

import asyncio

import typer
import uvicorn

from cathedral.logging import configure
from cathedral.validator.db import connect

app = typer.Typer(no_args_is_help=True, help="Cathedral subnet validator")


@app.command()
def serve(
    config: str = typer.Option("config/testnet.toml", "--config", "-c"),
    json_logs: bool = typer.Option(True, "--json-logs/--no-json-logs"),
    log_level: str = typer.Option("info"),
) -> None:
    """Run the validator HTTP server with all background loops."""
    configure(level=log_level.upper(), json_logs=json_logs)
    from cathedral.config import ValidatorSettings
    from cathedral.validator import from_settings

    settings = ValidatorSettings.from_toml(config)
    application = from_settings(config)
    uvicorn.run(
        application,
        host=settings.http.listen_host,
        port=settings.http.listen_port,
        log_level=log_level,
    )


@app.command()
def migrate(
    config: str = typer.Option("config/testnet.toml", "--config", "-c"),
) -> None:
    """Initialize the sqlite schema. Idempotent."""
    configure()
    from cathedral.config import ValidatorSettings

    settings = ValidatorSettings.from_toml(config)

    async def _run() -> None:
        conn = await connect(settings.storage.database_path)
        await conn.close()

    asyncio.run(_run())
    typer.echo(f"schema ready at {settings.storage.database_path}")


if __name__ == "__main__":
    app()
