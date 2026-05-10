//! Bittensor chain integration: register, read metagraph, set weights.
//!
//! Scope: only what Cathedral needs. Reading the metagraph, setting weights
//! based on miner uids and scores, and reporting weight-set status. Hotkey
//! signing lives here; signature verification of Polaris records lives in
//! `cathedral-evidence`.
//!
//! The trait abstraction keeps tests deterministic. The default impl wraps
//! the `bittensor` crate (added in a follow-up patch); for now the `mock`
//! module gives a working implementation for unit and integration tests.

#![forbid(unsafe_code)]

pub mod metagraph;
pub mod weights;
pub mod mock;

use async_trait::async_trait;
use cathedral_types::Hotkey;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Network {
    Finney,
    Test,
    Local,
}

impl Network {
    pub fn endpoint(self) -> &'static str {
        match self {
            Network::Finney => "wss://entrypoint-finney.opentensor.ai:443",
            Network::Test => "wss://test.finney.opentensor.ai:443",
            Network::Local => "ws://127.0.0.1:9944",
        }
    }
}

#[derive(Debug, Clone)]
pub struct ChainConfig {
    pub network: Network,
    pub netuid: u16,
    pub validator_hotkey: Hotkey,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WeightStatus {
    Healthy,
    BlockedByStake,
    BlockedByTransactionError,
    Disabled,
}

#[async_trait]
pub trait Chain: Send + Sync {
    async fn metagraph(&self) -> Result<metagraph::Metagraph, ChainError>;
    async fn set_weights(&self, weights: &[(u16, f32)]) -> Result<WeightStatus, ChainError>;
    async fn current_block(&self) -> Result<u64, ChainError>;
}

#[derive(Debug, thiserror::Error)]
pub enum ChainError {
    #[error("rpc error: {0}")]
    Rpc(String),
    #[error("not registered on netuid {0}")]
    NotRegistered(u16),
    #[error("stake below permit threshold")]
    StakeBelowPermit,
}
