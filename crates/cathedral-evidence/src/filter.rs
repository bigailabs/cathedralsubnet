//! Drop usage that should not count toward rewards.
//!
//! Issue #77: exclude creator-owned, platform-owned, refunded, abuse-flagged,
//! test, and self-loop usage.

use cathedral_types::PolarisUsageRecord;

pub fn filter_usage(records: Vec<PolarisUsageRecord>, owner_wallet: &str) -> Vec<PolarisUsageRecord> {
    records
        .into_iter()
        .filter(|u| {
            !u.flagged
                && !u.refunded
                && u.consumer.counts_for_rewards()
                && !is_self_loop(u, owner_wallet)
        })
        .collect()
}

fn is_self_loop(_u: &PolarisUsageRecord, _owner_wallet: &str) -> bool {
    // Placeholder: the production detector needs the consumer wallet on the
    // usage record. Issue #77 explicitly calls out self-loop; the schema we
    // accept from Polaris must include consumer wallet for this check.
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use cathedral_types::evidence::ConsumerClass;
    use chrono::Utc;

    fn rec(consumer: ConsumerClass, flagged: bool, refunded: bool) -> PolarisUsageRecord {
        PolarisUsageRecord {
            usage_id: "u".into(),
            polaris_agent_id: "a".into(),
            consumer,
            used_at: Utc::now(),
            flagged,
            refunded,
            signature: String::new(),
        }
    }

    #[test]
    fn drops_flagged_refunded_and_non_external() {
        let raw = vec![
            rec(ConsumerClass::External, false, false),
            rec(ConsumerClass::External, true, false),
            rec(ConsumerClass::External, false, true),
            rec(ConsumerClass::Creator, false, false),
            rec(ConsumerClass::Platform, false, false),
            rec(ConsumerClass::Test, false, false),
            rec(ConsumerClass::SelfLoop, false, false),
        ];
        let kept = filter_usage(raw, "wallet1");
        assert_eq!(kept.len(), 1);
    }
}
