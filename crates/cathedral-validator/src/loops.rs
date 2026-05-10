//! Background loops: metagraph refresh, evidence verification, weight setting.
//!
//! Stall detection (issue #79): each loop writes a heartbeat to `Health`. If
//! the heartbeat is older than `stall_after_secs`, the health endpoint reports
//! `stalled: true` and the operator sees it in the runbook output.

use std::time::Duration;

use crate::health::{Health, SerializableWeightStatus};

pub async fn run_stall_watchdog(health: Health, stall_after_secs: i64) {
    let mut tick = tokio::time::interval(Duration::from_secs(30));
    loop {
        tick.tick().await;
        let s = health.snapshot();
        let now = chrono::Utc::now();
        let stalled = match s.last_metagraph_at {
            None => true,
            Some(t) => (now - t).num_seconds() > stall_after_secs,
        };
        health.update(|h| h.stalled = stalled);
    }
}

pub async fn run_weight_loop<C: cathedral_chain::Chain>(
    chain: C,
    health: Health,
    interval_secs: u64,
    disabled: bool,
) {
    if disabled {
        health.update(|h| h.weight_status = Some(SerializableWeightStatus::Disabled));
        return;
    }
    let mut tick = tokio::time::interval(Duration::from_secs(interval_secs));
    loop {
        tick.tick().await;
        // Real impl: pull latest scored claims, build (uid, weight) vec.
        // For the skeleton we set an empty vector and report the chain's
        // returned status without sending traffic.
        let weights: Vec<(u16, f32)> = Vec::new();
        match chain.set_weights(&weights).await {
            Ok(status) => {
                health.update(|h| {
                    h.weight_status = Some(status.into());
                    h.last_weight_set_at = Some(chrono::Utc::now());
                });
            }
            Err(e) => {
                tracing::warn!(error = %e, "set_weights failed");
                health.update(|h| {
                    h.weight_status = Some(SerializableWeightStatus::BlockedByTransactionError);
                });
            }
        }
    }
}
