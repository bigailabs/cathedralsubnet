//! Six-dimension card scorer.
//!
//! Each dimension returns a value in [0.0, 1.0]. Final weighting is in
//! `cathedral_types::card::ScoreParts::weighted`.
//!
//! These rules are deliberately simple and inspectable; the validator team
//! tunes coefficients in `config/*.toml` rather than rewriting the scorer.

use cathedral_types::{Card, ScoreParts};
use cathedral_types::card::SourceClass;

use crate::registry::RegistryEntry;

pub fn score_card(card: &Card, entry: Option<&RegistryEntry>) -> ScoreParts {
    ScoreParts {
        source_quality: score_source_quality(card, entry),
        freshness: score_freshness(card, entry),
        specificity: score_specificity(card),
        usefulness: score_usefulness(card),
        clarity: score_clarity(card),
        maintenance: score_maintenance(card, entry),
    }
}

fn score_source_quality(card: &Card, entry: Option<&RegistryEntry>) -> f32 {
    if card.citations.is_empty() {
        return 0.0;
    }
    let official_classes = [
        SourceClass::Government,
        SourceClass::Regulator,
        SourceClass::Court,
        SourceClass::Parliament,
        SourceClass::LawText,
        SourceClass::OfficialJournal,
    ];
    let official_count = card
        .citations
        .iter()
        .filter(|s| official_classes.contains(&s.class))
        .count();
    let base = (official_count as f32) / (card.citations.len() as f32);

    let coverage_bonus = if let Some(req) = entry {
        let required = &req.required_source_classes;
        if required.is_empty() {
            0.0
        } else {
            let covered = required
                .iter()
                .filter(|c| card.citations.iter().any(|s| &s.class == *c))
                .count() as f32;
            0.2 * (covered / required.len() as f32)
        }
    } else {
        0.0
    };

    (base + coverage_bonus).clamp(0.0, 1.0)
}

fn score_freshness(card: &Card, entry: Option<&RegistryEntry>) -> f32 {
    let now = chrono::Utc::now();
    let age_hours = (now - card.last_refreshed_at).num_hours().max(0) as f32;
    let cadence = entry
        .map(|e| e.refresh_cadence_hours)
        .unwrap_or(card.refresh_cadence_hours)
        .max(1) as f32;
    let ratio = age_hours / cadence;
    if ratio <= 1.0 {
        1.0
    } else if ratio >= 4.0 {
        0.0
    } else {
        1.0 - (ratio - 1.0) / 3.0
    }
}

fn score_specificity(card: &Card) -> f32 {
    let length = card.what_changed.len() + card.why_it_matters.len();
    if length < 100 {
        0.2
    } else if length < 400 {
        0.6
    } else if length < 1500 {
        1.0
    } else {
        0.7
    }
}

fn score_usefulness(card: &Card) -> f32 {
    let mut s = 0.0_f32;
    if !card.action_notes.trim().is_empty() {
        s += 0.5;
    }
    if !card.risks.trim().is_empty() {
        s += 0.3;
    }
    if card.confidence > 0.5 {
        s += 0.2;
    }
    s.clamp(0.0, 1.0)
}

fn score_clarity(card: &Card) -> f32 {
    let summary = card.summary.trim();
    if summary.len() < 40 || summary.len() > 800 {
        return 0.4;
    }
    let sentences = summary.split('.').filter(|s| !s.trim().is_empty()).count();
    if (1..=6).contains(&sentences) {
        1.0
    } else {
        0.6
    }
}

fn score_maintenance(card: &Card, entry: Option<&RegistryEntry>) -> f32 {
    let cadence = entry
        .map(|e| e.refresh_cadence_hours)
        .unwrap_or(card.refresh_cadence_hours)
        .max(1) as i64;
    let age = (chrono::Utc::now() - card.last_refreshed_at).num_hours();
    if age <= cadence {
        1.0
    } else if age <= cadence * 2 {
        0.6
    } else if age <= cadence * 4 {
        0.2
    } else {
        0.0
    }
}
