//! Shared health surface. The HTTP `/health` endpoint serializes this.

use cathedral_chain::WeightStatus;
use serde::Serialize;
use std::sync::{Arc, Mutex};

#[derive(Debug, Clone, Serialize)]
pub struct HealthSnapshot {
    pub registered: bool,
    pub current_block: u64,
    pub last_metagraph_at: Option<chrono::DateTime<chrono::Utc>>,
    pub last_evidence_pass_at: Option<chrono::DateTime<chrono::Utc>>,
    pub last_weight_set_at: Option<chrono::DateTime<chrono::Utc>>,
    pub weight_status: Option<SerializableWeightStatus>,
    pub stalled: bool,
    pub claims_pending: u32,
    pub claims_verified: u64,
    pub claims_rejected: u64,
}

#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SerializableWeightStatus {
    Healthy,
    BlockedByStake,
    BlockedByTransactionError,
    Disabled,
}

impl From<WeightStatus> for SerializableWeightStatus {
    fn from(s: WeightStatus) -> Self {
        match s {
            WeightStatus::Healthy => Self::Healthy,
            WeightStatus::BlockedByStake => Self::BlockedByStake,
            WeightStatus::BlockedByTransactionError => Self::BlockedByTransactionError,
            WeightStatus::Disabled => Self::Disabled,
        }
    }
}

#[derive(Debug, Clone)]
pub struct Health(pub Arc<Mutex<HealthSnapshot>>);

impl Default for Health {
    fn default() -> Self {
        Self::new()
    }
}

impl Health {
    pub fn new() -> Self {
        Self(Arc::new(Mutex::new(HealthSnapshot {
            registered: false,
            current_block: 0,
            last_metagraph_at: None,
            last_evidence_pass_at: None,
            last_weight_set_at: None,
            weight_status: None,
            stalled: false,
            claims_pending: 0,
            claims_verified: 0,
            claims_rejected: 0,
        })))
    }

    pub fn snapshot(&self) -> HealthSnapshot {
        self.0.lock().unwrap().clone()
    }

    pub fn update(&self, f: impl FnOnce(&mut HealthSnapshot)) {
        let mut s = self.0.lock().unwrap();
        f(&mut s);
    }
}
