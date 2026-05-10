//! Miner service. Submits Polaris agent claims to a configured validator.

#![forbid(unsafe_code)]

pub mod config;
pub mod submit;

pub use config::MinerConfig;
pub use submit::submit_claim;
