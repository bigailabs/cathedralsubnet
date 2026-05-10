//! Polaris agent claim — the message a miner submits to the validator.
//!
//! Wire format: `cathedral.polaris_agent_claim.v1`. Acceptance criteria from
//! issue #77: claim must carry miner hotkey, owner wallet, work unit, and one
//! or more Polaris identifiers.

use serde::{Deserialize, Serialize};

use crate::hotkey::Hotkey;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ClaimVersion {
    V1,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename = "cathedral.polaris_agent_claim.v1")]
pub struct PolarisAgentClaim {
    pub version: ClaimVersion,
    pub miner_hotkey: Hotkey,
    pub owner_wallet: String,
    pub work_unit: String,
    pub polaris_agent_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub polaris_deployment_id: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub polaris_run_ids: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub polaris_artifact_ids: Vec<String>,
    pub submitted_at: chrono::DateTime<chrono::Utc>,
}

impl PolarisAgentClaim {
    pub fn validate_shape(&self) -> Result<(), ClaimError> {
        if self.owner_wallet.is_empty() {
            return Err(ClaimError::MissingField("owner_wallet"));
        }
        if self.work_unit.is_empty() {
            return Err(ClaimError::MissingField("work_unit"));
        }
        if self.polaris_agent_id.is_empty() {
            return Err(ClaimError::MissingField("polaris_agent_id"));
        }
        Ok(())
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ClaimError {
    #[error("missing required field: {0}")]
    MissingField(&'static str),
}
