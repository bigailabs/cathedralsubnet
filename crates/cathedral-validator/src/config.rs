use serde::Deserialize;
use std::path::Path;

#[derive(Debug, Clone, Deserialize)]
pub struct ValidatorConfig {
    pub network: NetworkConfig,
    pub polaris: PolarisConfig,
    pub http: HttpConfig,
    pub weights: WeightsConfig,
    pub storage: StorageConfig,
}

#[derive(Debug, Clone, Deserialize)]
pub struct NetworkConfig {
    pub name: String,
    pub netuid: u16,
    pub validator_hotkey: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct PolarisConfig {
    pub base_url: String,
    pub public_key_hex: String,
    pub fetch_timeout_secs: u64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct HttpConfig {
    pub listen: String,
    pub bearer_token_env: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct WeightsConfig {
    pub interval_secs: u64,
    pub disabled: bool,
}

#[derive(Debug, Clone, Deserialize)]
pub struct StorageConfig {
    pub database_url: String,
}

impl ValidatorConfig {
    pub fn load(path: impl AsRef<Path>) -> anyhow::Result<Self> {
        use figment::providers::{Env, Format, Toml};
        figment::Figment::new()
            .merge(Toml::file(path.as_ref()))
            .merge(Env::prefixed("CATHEDRAL_").split("__"))
            .extract()
            .map_err(Into::into)
    }
}
