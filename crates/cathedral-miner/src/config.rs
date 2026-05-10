use serde::Deserialize;
use std::path::Path;

#[derive(Debug, Clone, Deserialize)]
pub struct MinerConfig {
    pub miner_hotkey: String,
    pub owner_wallet: String,
    pub validator_url: String,
    pub validator_bearer_env: String,
}

impl MinerConfig {
    pub fn load(path: impl AsRef<Path>) -> anyhow::Result<Self> {
        use figment::providers::{Env, Format, Toml};
        figment::Figment::new()
            .merge(Toml::file(path.as_ref()))
            .merge(Env::prefixed("CATHEDRAL_MINER_").split("__"))
            .extract()
            .map_err(Into::into)
    }
}
