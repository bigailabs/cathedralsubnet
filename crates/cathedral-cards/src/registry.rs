//! Card registry — what Cathedral expects to see, by topic.

use cathedral_types::{Jurisdiction, card::SourceClass};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RegistryEntry {
    pub card_id: String,
    pub jurisdiction: Jurisdiction,
    pub topic: String,
    pub required_source_classes: Vec<SourceClass>,
    pub refresh_cadence_hours: u32,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CardRegistry {
    pub entries: Vec<RegistryEntry>,
}

impl CardRegistry {
    pub fn baseline() -> Self {
        use Jurisdiction::*;
        use SourceClass::*;
        Self {
            entries: vec![
                RegistryEntry {
                    card_id: "eu-ai-act".into(),
                    jurisdiction: Eu,
                    topic: "EU AI Act enforcement and guidance".into(),
                    required_source_classes: vec![OfficialJournal, Regulator, LawText],
                    refresh_cadence_hours: 24,
                },
                RegistryEntry {
                    card_id: "us-ai-executive-order".into(),
                    jurisdiction: Us,
                    topic: "US executive orders and federal AI guidance".into(),
                    required_source_classes: vec![Government, Regulator],
                    refresh_cadence_hours: 24,
                },
                RegistryEntry {
                    card_id: "uk-aisi".into(),
                    jurisdiction: Uk,
                    topic: "UK AI Safety Institute publications".into(),
                    required_source_classes: vec![Government, Regulator],
                    refresh_cadence_hours: 48,
                },
                RegistryEntry {
                    card_id: "eu-gdpr-enforcement".into(),
                    jurisdiction: Eu,
                    topic: "GDPR enforcement decisions and fines".into(),
                    required_source_classes: vec![Regulator, Court],
                    refresh_cadence_hours: 24,
                },
                RegistryEntry {
                    card_id: "us-ccpa".into(),
                    jurisdiction: Us,
                    topic: "California Consumer Privacy Act enforcement".into(),
                    required_source_classes: vec![Regulator, LawText, Court],
                    refresh_cadence_hours: 48,
                },
            ],
        }
    }

    pub fn lookup(&self, card_id: &str) -> Option<&RegistryEntry> {
        self.entries.iter().find(|e| e.card_id == card_id)
    }
}
