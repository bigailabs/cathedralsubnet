//! In-memory chain impl for tests and local development.

use async_trait::async_trait;
use std::sync::{Arc, Mutex};

use crate::{metagraph::Metagraph, Chain, ChainError, WeightStatus};

#[derive(Debug, Clone, Default)]
pub struct MockChain {
    inner: Arc<Mutex<MockState>>,
}

#[derive(Debug, Default)]
struct MockState {
    metagraph: Metagraph,
    block: u64,
    last_weights: Vec<(u16, f32)>,
    weight_status: Option<WeightStatus>,
}

impl Default for Metagraph {
    fn default() -> Self {
        Metagraph { block: 0, miners: Vec::new() }
    }
}

impl MockChain {
    pub fn new(metagraph: Metagraph) -> Self {
        Self {
            inner: Arc::new(Mutex::new(MockState {
                metagraph,
                block: 0,
                last_weights: Vec::new(),
                weight_status: Some(WeightStatus::Healthy),
            })),
        }
    }

    pub fn last_weights(&self) -> Vec<(u16, f32)> {
        self.inner.lock().unwrap().last_weights.clone()
    }

    pub fn set_weight_status(&self, status: WeightStatus) {
        self.inner.lock().unwrap().weight_status = Some(status);
    }
}

#[async_trait]
impl Chain for MockChain {
    async fn metagraph(&self) -> Result<Metagraph, ChainError> {
        Ok(self.inner.lock().unwrap().metagraph.clone())
    }

    async fn set_weights(&self, weights: &[(u16, f32)]) -> Result<WeightStatus, ChainError> {
        let mut s = self.inner.lock().unwrap();
        s.last_weights = weights.to_vec();
        Ok(s.weight_status.unwrap_or(WeightStatus::Healthy))
    }

    async fn current_block(&self) -> Result<u64, ChainError> {
        Ok(self.inner.lock().unwrap().block)
    }
}
