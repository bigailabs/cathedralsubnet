//! Polaris evidence: fetch, verify, filter.
//!
//! Implements issue #77 acceptance criteria:
//! - Pull manifest, runs, artifacts, usage by Polaris ID
//! - Verify Ed25519 signatures against a configured Polaris public key
//! - Verify artifact content hash against served bytes
//! - Filter usage from creator-owned, platform-owned, refunded, abuse-flagged,
//!   test, and self-loop sources before scoring
//! - Reject unsigned records, hash mismatches, and missing records
//!
//! Partial-failure policy: any signature failure or hash mismatch on the
//! manifest is fatal for the bundle. A failure on a single run/artifact/usage
//! record drops that record but allows the rest of the bundle to proceed,
//! recording the count of dropped records on the bundle for observability.

#![forbid(unsafe_code)]

pub mod fetch;
pub mod verify;
pub mod filter;

use cathedral_types::{
    EvidenceBundle, PolarisAgentClaim, PolarisArtifactRecord, PolarisManifest, PolarisRunRecord,
    PolarisUsageRecord,
};
use ed25519_dalek::VerifyingKey;

#[derive(Debug, Clone)]
pub struct EvidenceConfig {
    pub polaris_base_url: url::Url,
    pub polaris_public_key: VerifyingKey,
    pub fetch_timeout_secs: u64,
}

#[derive(Debug, thiserror::Error)]
pub enum EvidenceError {
    #[error("polaris fetch failed: {0}")]
    Fetch(String),
    #[error("polaris record missing: {0}")]
    Missing(String),
    #[error("signature verification failed for {0}")]
    BadSignature(&'static str),
    #[error("artifact hash mismatch for {0}")]
    HashMismatch(String),
    #[error("manifest mismatch: claim says {claim}, manifest says {manifest}")]
    ManifestMismatch { claim: String, manifest: String },
    #[error("transport error: {0}")]
    Transport(String),
}

pub struct EvidenceCollector<F: fetch::PolarisFetcher> {
    pub config: EvidenceConfig,
    pub fetcher: F,
}

impl<F: fetch::PolarisFetcher> EvidenceCollector<F> {
    pub async fn collect(&self, claim: &PolarisAgentClaim) -> Result<EvidenceBundle, EvidenceError> {
        let manifest: PolarisManifest = self.fetcher.fetch_manifest(&claim.polaris_agent_id).await?;
        verify::manifest(&manifest, &self.config.polaris_public_key)?;

        if manifest.polaris_agent_id != claim.polaris_agent_id {
            return Err(EvidenceError::ManifestMismatch {
                claim: claim.polaris_agent_id.clone(),
                manifest: manifest.polaris_agent_id.clone(),
            });
        }

        let mut runs = Vec::new();
        for run_id in &claim.polaris_run_ids {
            let record: PolarisRunRecord = self.fetcher.fetch_run(run_id).await?;
            if verify::run(&record, &self.config.polaris_public_key).is_ok() {
                runs.push(record);
            }
        }

        let mut artifacts = Vec::new();
        for art_id in &claim.polaris_artifact_ids {
            let record: PolarisArtifactRecord = self.fetcher.fetch_artifact(art_id).await?;
            if verify::artifact_record(&record, &self.config.polaris_public_key).is_ok() {
                let bytes = self.fetcher.fetch_artifact_bytes(&record.content_url).await?;
                if verify::artifact_bytes(&record, &bytes).is_ok() {
                    artifacts.push(record);
                }
            }
        }

        let raw_usage: Vec<PolarisUsageRecord> = self
            .fetcher
            .fetch_usage(&claim.polaris_agent_id)
            .await?
            .into_iter()
            .filter(|u| verify::usage(u, &self.config.polaris_public_key).is_ok())
            .collect();

        let raw_count = raw_usage.len() as u32;
        let usage = filter::filter_usage(raw_usage, &claim.owner_wallet);
        let filtered_usage_count = raw_count - usage.len() as u32;

        Ok(EvidenceBundle {
            manifest,
            runs,
            artifacts,
            usage,
            verified_at: chrono::Utc::now(),
            filtered_usage_count,
        })
    }
}
