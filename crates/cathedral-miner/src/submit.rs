use cathedral_types::{ClaimVersion, Hotkey, PolarisAgentClaim};

use crate::config::MinerConfig;

#[derive(Debug, thiserror::Error)]
pub enum SubmitError {
    #[error("transport: {0}")]
    Transport(String),
    #[error("validator rejected claim: status {status}, body: {body}")]
    Rejected { status: u16, body: String },
    #[error("missing bearer env {0}")]
    MissingBearer(String),
}

pub struct ClaimInputs<'a> {
    pub work_unit: &'a str,
    pub polaris_agent_id: &'a str,
    pub polaris_deployment_id: Option<&'a str>,
    pub polaris_run_ids: Vec<String>,
    pub polaris_artifact_ids: Vec<String>,
}

pub async fn submit_claim(cfg: &MinerConfig, inputs: ClaimInputs<'_>) -> Result<(), SubmitError> {
    let claim = PolarisAgentClaim {
        version: ClaimVersion::V1,
        miner_hotkey: Hotkey(cfg.miner_hotkey.clone()),
        owner_wallet: cfg.owner_wallet.clone(),
        work_unit: inputs.work_unit.to_string(),
        polaris_agent_id: inputs.polaris_agent_id.to_string(),
        polaris_deployment_id: inputs.polaris_deployment_id.map(str::to_string),
        polaris_run_ids: inputs.polaris_run_ids,
        polaris_artifact_ids: inputs.polaris_artifact_ids,
        submitted_at: chrono::Utc::now(),
    };

    let bearer = std::env::var(&cfg.validator_bearer_env)
        .map_err(|_| SubmitError::MissingBearer(cfg.validator_bearer_env.clone()))?;

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .map_err(|e| SubmitError::Transport(e.to_string()))?;

    let url = format!("{}/v1/claim", cfg.validator_url.trim_end_matches('/'));
    let resp = client
        .post(url)
        .bearer_auth(bearer)
        .json(&claim)
        .send()
        .await
        .map_err(|e| SubmitError::Transport(e.to_string()))?;

    if resp.status().is_success() {
        Ok(())
    } else {
        let status = resp.status().as_u16();
        let body = resp.text().await.unwrap_or_default();
        Err(SubmitError::Rejected { status, body })
    }
}
