//! Validator service.
//!
//! Issue #79 acceptance criteria:
//! - Mutating REST endpoints require bearer-token auth
//! - Silent stalls detected and surfaced
//! - Weight-setting status explicit (healthy / blocked-by-stake /
//!   blocked-by-tx-error / disabled)
//! - Runbook explains start, stop, restart, logs, health, registration
//! - Public docs do not claim live state the validator cannot verify
//! - The validator can be handed to another operator without private context
//!
//! The service has three loops: claim intake (HTTP), evidence verification
//! (worker), and weight-setting (timer). Each reports into a shared `Health`
//! that the runbook surfaces over `GET /health`.

#![forbid(unsafe_code)]

pub mod auth;
pub mod config;
pub mod health;
pub mod http;
pub mod loops;
pub mod state;

pub use config::ValidatorConfig;
pub use state::ValidatorState;
