//! Records pulled from Polaris and the verified bundle Cathedral produces.
//!
//! Issue #77 acceptance criteria: pull manifest, run, artifact, and usage
//! records by Polaris ID; verify signatures against a configured public key;
//! verify artifact hash against served content.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PolarisManifest {
    pub polaris_agent_id: String,
    pub owner_wallet: String,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub schema: String,
    pub signature: String,
    #[serde(flatten)]
    pub extra: serde_json::Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PolarisRunRecord {
    pub run_id: String,
    pub polaris_agent_id: String,
    pub started_at: chrono::DateTime<chrono::Utc>,
    pub ended_at: Option<chrono::DateTime<chrono::Utc>>,
    pub outcome: String,
    pub signature: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PolarisArtifactRecord {
    pub artifact_id: String,
    pub polaris_agent_id: String,
    pub run_id: Option<String>,
    pub content_url: String,
    pub content_hash: String,
    pub report_hash: Option<String>,
    pub signature: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PolarisUsageRecord {
    pub usage_id: String,
    pub polaris_agent_id: String,
    pub consumer: ConsumerClass,
    pub used_at: chrono::DateTime<chrono::Utc>,
    pub flagged: bool,
    pub refunded: bool,
    pub signature: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ConsumerClass {
    External,
    Creator,
    Platform,
    Test,
    SelfLoop,
}

impl ConsumerClass {
    pub fn counts_for_rewards(self) -> bool {
        matches!(self, ConsumerClass::External)
    }
}

/// Bundle Cathedral produces after verification, ready for the card scorer.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EvidenceBundle {
    pub manifest: PolarisManifest,
    pub runs: Vec<PolarisRunRecord>,
    pub artifacts: Vec<PolarisArtifactRecord>,
    pub usage: Vec<PolarisUsageRecord>,
    pub verified_at: chrono::DateTime<chrono::Utc>,
    pub filtered_usage_count: u32,
}
