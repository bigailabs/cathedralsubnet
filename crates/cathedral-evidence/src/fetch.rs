//! Polaris HTTPS fetcher trait. Real impl wraps `reqwest`. Tests use a stub.

use async_trait::async_trait;
use cathedral_types::{PolarisArtifactRecord, PolarisManifest, PolarisRunRecord, PolarisUsageRecord};

use crate::EvidenceError;

#[async_trait]
pub trait PolarisFetcher: Send + Sync {
    async fn fetch_manifest(&self, polaris_agent_id: &str) -> Result<PolarisManifest, EvidenceError>;
    async fn fetch_run(&self, run_id: &str) -> Result<PolarisRunRecord, EvidenceError>;
    async fn fetch_artifact(&self, artifact_id: &str) -> Result<PolarisArtifactRecord, EvidenceError>;
    async fn fetch_artifact_bytes(&self, url: &str) -> Result<Vec<u8>, EvidenceError>;
    async fn fetch_usage(&self, polaris_agent_id: &str) -> Result<Vec<PolarisUsageRecord>, EvidenceError>;
}

pub struct HttpPolarisFetcher {
    client: reqwest::Client,
    base: url::Url,
}

impl HttpPolarisFetcher {
    pub fn new(base: url::Url, timeout_secs: u64) -> Self {
        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(timeout_secs))
            .user_agent(format!("cathedral-validator/{}", env!("CARGO_PKG_VERSION")))
            .build()
            .expect("reqwest client");
        Self { client, base }
    }
}

#[async_trait]
impl PolarisFetcher for HttpPolarisFetcher {
    async fn fetch_manifest(&self, agent_id: &str) -> Result<PolarisManifest, EvidenceError> {
        let url = self.base.join(&format!("v1/agents/{}/manifest", agent_id))
            .map_err(|e| EvidenceError::Fetch(e.to_string()))?;
        let resp = self.client.get(url).send().await.map_err(|e| EvidenceError::Transport(e.to_string()))?;
        if resp.status() == reqwest::StatusCode::NOT_FOUND {
            return Err(EvidenceError::Missing(format!("manifest for {agent_id}")));
        }
        resp.error_for_status()
            .map_err(|e| EvidenceError::Fetch(e.to_string()))?
            .json::<PolarisManifest>()
            .await
            .map_err(|e| EvidenceError::Fetch(e.to_string()))
    }

    async fn fetch_run(&self, run_id: &str) -> Result<PolarisRunRecord, EvidenceError> {
        let url = self.base.join(&format!("v1/runs/{}", run_id))
            .map_err(|e| EvidenceError::Fetch(e.to_string()))?;
        let resp = self.client.get(url).send().await.map_err(|e| EvidenceError::Transport(e.to_string()))?;
        if resp.status() == reqwest::StatusCode::NOT_FOUND {
            return Err(EvidenceError::Missing(format!("run {run_id}")));
        }
        resp.error_for_status()
            .map_err(|e| EvidenceError::Fetch(e.to_string()))?
            .json::<PolarisRunRecord>()
            .await
            .map_err(|e| EvidenceError::Fetch(e.to_string()))
    }

    async fn fetch_artifact(&self, artifact_id: &str) -> Result<PolarisArtifactRecord, EvidenceError> {
        let url = self.base.join(&format!("v1/artifacts/{}", artifact_id))
            .map_err(|e| EvidenceError::Fetch(e.to_string()))?;
        let resp = self.client.get(url).send().await.map_err(|e| EvidenceError::Transport(e.to_string()))?;
        if resp.status() == reqwest::StatusCode::NOT_FOUND {
            return Err(EvidenceError::Missing(format!("artifact {artifact_id}")));
        }
        resp.error_for_status()
            .map_err(|e| EvidenceError::Fetch(e.to_string()))?
            .json::<PolarisArtifactRecord>()
            .await
            .map_err(|e| EvidenceError::Fetch(e.to_string()))
    }

    async fn fetch_artifact_bytes(&self, url: &str) -> Result<Vec<u8>, EvidenceError> {
        let resp = self.client.get(url).send().await.map_err(|e| EvidenceError::Transport(e.to_string()))?;
        Ok(resp
            .error_for_status()
            .map_err(|e| EvidenceError::Fetch(e.to_string()))?
            .bytes()
            .await
            .map_err(|e| EvidenceError::Transport(e.to_string()))?
            .to_vec())
    }

    async fn fetch_usage(&self, agent_id: &str) -> Result<Vec<PolarisUsageRecord>, EvidenceError> {
        let url = self.base.join(&format!("v1/agents/{}/usage", agent_id))
            .map_err(|e| EvidenceError::Fetch(e.to_string()))?;
        let resp = self.client.get(url).send().await.map_err(|e| EvidenceError::Transport(e.to_string()))?;
        resp.error_for_status()
            .map_err(|e| EvidenceError::Fetch(e.to_string()))?
            .json::<Vec<PolarisUsageRecord>>()
            .await
            .map_err(|e| EvidenceError::Fetch(e.to_string()))
    }
}
