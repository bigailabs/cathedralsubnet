use anyhow::Context;
use cathedral_validator::{config::ValidatorConfig, http::router, state::ValidatorState};
use clap::Parser;
use std::net::SocketAddr;
use tracing_subscriber::EnvFilter;

#[derive(Debug, Parser)]
#[command(name = "cathedral-validator", about = "Cathedral subnet validator")]
struct Args {
    #[arg(long, default_value = "config/testnet.toml")]
    config: String,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")))
        .json()
        .init();

    let args = Args::parse();
    let cfg = ValidatorConfig::load(&args.config)
        .with_context(|| format!("loading config from {}", args.config))?;

    let bearer = std::env::var(&cfg.http.bearer_token_env).with_context(|| {
        format!(
            "missing bearer token: env {} not set",
            cfg.http.bearer_token_env
        )
    })?;

    let state = ValidatorState::new(bearer);
    let app = router(state.clone());

    let listen: SocketAddr = cfg.http.listen.parse().context("parsing listen address")?;
    tracing::info!(%listen, network = %cfg.network.name, netuid = cfg.network.netuid, "starting validator");

    let listener = tokio::net::TcpListener::bind(listen).await?;

    let stall_health = state.health.clone();
    tokio::spawn(async move {
        cathedral_validator::loops::run_stall_watchdog(stall_health, 600).await;
    });

    axum::serve(listener, app).await?;
    Ok(())
}
