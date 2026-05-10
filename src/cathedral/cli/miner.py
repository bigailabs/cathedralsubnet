"""`cathedral-miner` CLI."""

from __future__ import annotations

import asyncio

import typer

from cathedral.config import MinerSettings
from cathedral.logging import configure
from cathedral.miner import submit_claim

app = typer.Typer(no_args_is_help=True, help="Cathedral subnet miner")


@app.command()
def submit(
    work_unit: str = typer.Option(..., help="e.g. 'card:eu-ai-act'"),
    polaris_agent_id: str = typer.Option(..., help="Polaris agent identifier"),
    polaris_deployment_id: str | None = typer.Option(None),
    polaris_run_ids: str = typer.Option("", help="comma-separated run IDs"),
    polaris_artifact_ids: str = typer.Option("", help="comma-separated artifact IDs"),
    config: str = typer.Option("config/miner.toml", help="path to miner.toml"),
    json_logs: bool = typer.Option(False, "--json-logs"),
) -> None:
    configure(json_logs=json_logs)
    settings = MinerSettings.from_toml(config)
    runs = [s for s in polaris_run_ids.split(",") if s]
    arts = [s for s in polaris_artifact_ids.split(",") if s]
    claim_id = asyncio.run(
        submit_claim(
            settings,
            work_unit=work_unit,
            polaris_agent_id=polaris_agent_id,
            polaris_deployment_id=polaris_deployment_id,
            polaris_run_ids=runs,
            polaris_artifact_ids=arts,
        )
    )
    typer.echo(f"claim accepted: id={claim_id}")


if __name__ == "__main__":
    app()
