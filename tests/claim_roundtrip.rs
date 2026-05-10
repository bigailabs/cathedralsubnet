//! Integration test: claim shape validation + JSON round-trip.

use cathedral_types::{ClaimVersion, Hotkey, PolarisAgentClaim};
use chrono::Utc;

fn sample_claim() -> PolarisAgentClaim {
    PolarisAgentClaim {
        version: ClaimVersion::V1,
        miner_hotkey: Hotkey("5F".into()),
        owner_wallet: "5G".into(),
        work_unit: "card:eu-ai-act".into(),
        polaris_agent_id: "agt_01H".into(),
        polaris_deployment_id: None,
        polaris_run_ids: vec!["run_1".into()],
        polaris_artifact_ids: vec!["art_1".into()],
        submitted_at: Utc::now(),
    }
}

#[test]
fn shape_validates() {
    let c = sample_claim();
    c.validate_shape().unwrap();
}

#[test]
fn missing_agent_id_fails() {
    let mut c = sample_claim();
    c.polaris_agent_id = String::new();
    assert!(c.validate_shape().is_err());
}

#[test]
fn json_roundtrip() {
    let c = sample_claim();
    let json = serde_json::to_string(&c).unwrap();
    let back: PolarisAgentClaim = serde_json::from_str(&json).unwrap();
    assert_eq!(back.polaris_agent_id, c.polaris_agent_id);
    assert_eq!(back.work_unit, c.work_unit);
}
