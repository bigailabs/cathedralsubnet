use std::sync::Arc;

use crate::health::Health;

#[derive(Clone)]
pub struct ValidatorState {
    pub health: Health,
    pub bearer: Arc<String>,
}

impl ValidatorState {
    pub fn new(bearer: String) -> Self {
        Self {
            health: Health::new(),
            bearer: Arc::new(bearer),
        }
    }
}
