//! Regulatory card types.
//!
//! Issue #78 acceptance criteria: card registry defines jurisdiction, topic,
//! worker owner, required source classes, and refresh cadence. Cards must
//! cite official sources, and validator scoring rewards source quality,
//! freshness, specificity, usefulness, clarity, and maintenance.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct CardId(pub String);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Jurisdiction {
    Eu,
    Us,
    Uk,
    Ca,
    Au,
    In,
    Br,
    Other,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceClass {
    Government,
    Regulator,
    Court,
    Parliament,
    LawText,
    OfficialJournal,
    SecondaryAnalysis,
    Other,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Source {
    pub url: String,
    pub class: SourceClass,
    pub fetched_at: chrono::DateTime<chrono::Utc>,
    pub status: u16,
    pub content_hash: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Card {
    pub id: CardId,
    pub jurisdiction: Jurisdiction,
    pub topic: String,
    pub worker_owner_hotkey: String,
    pub polaris_agent_id: String,
    pub title: String,
    pub summary: String,
    pub what_changed: String,
    pub why_it_matters: String,
    pub action_notes: String,
    pub risks: String,
    pub citations: Vec<Source>,
    pub confidence: f32,
    pub no_legal_advice: bool,
    pub last_refreshed_at: chrono::DateTime<chrono::Utc>,
    pub refresh_cadence_hours: u32,
}

/// Score parts a card receives. Each component is in [0.0, 1.0]; the validator
/// produces a final weighted score downstream.
#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
pub struct ScoreParts {
    pub source_quality: f32,
    pub freshness: f32,
    pub specificity: f32,
    pub usefulness: f32,
    pub clarity: f32,
    pub maintenance: f32,
}

impl ScoreParts {
    /// Default weighting from issue #78: source quality and maintenance lead.
    pub fn weighted(self) -> f32 {
        0.30 * self.source_quality
            + 0.20 * self.maintenance
            + 0.15 * self.freshness
            + 0.15 * self.specificity
            + 0.10 * self.usefulness
            + 0.10 * self.clarity
    }
}
