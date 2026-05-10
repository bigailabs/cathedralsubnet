//! Operator CLI: handoff-friendly commands per issue #79.

use anyhow::Context;
use clap::{Parser, Subcommand};
use tracing_subscriber::EnvFilter;

#[derive(Debug, Parser)]
#[command(name = "cathedral", about = "Cathedral operator CLI")]
struct Args {
    #[command(subcommand)]
    command: Cmd,
}

#[derive(Debug, Subcommand)]
enum Cmd {
    /// Print validator health snapshot.
    Health {
        #[arg(long, default_value = "http://127.0.0.1:9333")]
        validator_url: String,
    },
    /// Print weight-setting status as a single word.
    Weights {
        #[arg(long, default_value = "http://127.0.0.1:9333")]
        validator_url: String,
    },
    /// Verify the validator is registered on chain.
    Registration {
        #[arg(long, default_value = "http://127.0.0.1:9333")]
        validator_url: String,
    },
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")))
        .init();

    let args = Args::parse();
    match args.command {
        Cmd::Health { validator_url } => {
            let resp = reqwest::get(format!("{}/health", validator_url.trim_end_matches('/')))
                .await
                .context("requesting /health")?;
            let body: serde_json::Value = resp.json().await.context("decoding health body")?;
            println!("{}", serde_json::to_string_pretty(&body)?);
        }
        Cmd::Weights { validator_url } => {
            let resp = reqwest::get(format!("{}/health", validator_url.trim_end_matches('/')))
                .await
                .context("requesting /health")?;
            let body: serde_json::Value = resp.json().await?;
            let status = body
                .get("weight_status")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown");
            println!("{}", status);
        }
        Cmd::Registration { validator_url } => {
            let resp = reqwest::get(format!("{}/health", validator_url.trim_end_matches('/')))
                .await
                .context("requesting /health")?;
            let body: serde_json::Value = resp.json().await?;
            let registered = body.get("registered").and_then(|v| v.as_bool()).unwrap_or(false);
            println!("registered: {}", registered);
        }
    }
    Ok(())
}
