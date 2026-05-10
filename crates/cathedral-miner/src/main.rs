use anyhow::Context;
use cathedral_miner::{config::MinerConfig, submit::ClaimInputs};
use clap::{Parser, Subcommand};
use tracing_subscriber::EnvFilter;

#[derive(Debug, Parser)]
#[command(name = "cathedral-miner", about = "Cathedral subnet miner")]
struct Args {
    #[arg(long, default_value = "config/miner.toml")]
    config: String,
    #[command(subcommand)]
    command: Cmd,
}

#[derive(Debug, Subcommand)]
enum Cmd {
    /// Submit a Polaris agent claim to the configured validator.
    Submit {
        #[arg(long)]
        work_unit: String,
        #[arg(long)]
        polaris_agent_id: String,
        #[arg(long)]
        polaris_deployment_id: Option<String>,
        #[arg(long, value_delimiter = ',')]
        polaris_run_ids: Vec<String>,
        #[arg(long, value_delimiter = ',')]
        polaris_artifact_ids: Vec<String>,
    },
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")))
        .init();

    let args = Args::parse();
    let cfg = MinerConfig::load(&args.config)
        .with_context(|| format!("loading config from {}", args.config))?;

    match args.command {
        Cmd::Submit {
            work_unit,
            polaris_agent_id,
            polaris_deployment_id,
            polaris_run_ids,
            polaris_artifact_ids,
        } => {
            cathedral_miner::submit_claim(
                &cfg,
                ClaimInputs {
                    work_unit: &work_unit,
                    polaris_agent_id: &polaris_agent_id,
                    polaris_deployment_id: polaris_deployment_id.as_deref(),
                    polaris_run_ids,
                    polaris_artifact_ids,
                },
            )
            .await
            .context("submitting claim")?;
            tracing::info!("claim accepted by validator");
        }
    }

    Ok(())
}
