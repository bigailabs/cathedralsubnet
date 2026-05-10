//! Preflight checks that fail fast before scoring.
//!
//! Issue #78: broken sources, uncited claims, and legal-advice framing fail.

use cathedral_types::Card;

#[derive(Debug, thiserror::Error)]
pub enum PreflightFailure {
    #[error("card has no citations")]
    NoCitations,
    #[error("card has a broken source: {url} status {status}")]
    BrokenSource { url: String, status: u16 },
    #[error("card uses legal-advice framing: {reason}")]
    LegalAdviceFraming { reason: &'static str },
    #[error("card missing no_legal_advice marker")]
    MissingNoLegalAdviceMarker,
    #[error("card missing required field: {0}")]
    MissingField(&'static str),
}

const LEGAL_ADVICE_PHRASES: &[&str] = &[
    "you should",
    "we recommend that you",
    "our advice is",
    "as your lawyer",
    "this constitutes legal advice",
];

pub fn preflight(card: &Card) -> Result<(), PreflightFailure> {
    if card.citations.is_empty() {
        return Err(PreflightFailure::NoCitations);
    }
    if !card.no_legal_advice {
        return Err(PreflightFailure::MissingNoLegalAdviceMarker);
    }
    if card.summary.is_empty() {
        return Err(PreflightFailure::MissingField("summary"));
    }
    if card.what_changed.is_empty() {
        return Err(PreflightFailure::MissingField("what_changed"));
    }
    if card.why_it_matters.is_empty() {
        return Err(PreflightFailure::MissingField("why_it_matters"));
    }

    for src in &card.citations {
        if !(200..400).contains(&src.status) {
            return Err(PreflightFailure::BrokenSource {
                url: src.url.clone(),
                status: src.status,
            });
        }
    }

    let lc = format!("{} {} {}", card.summary, card.action_notes, card.why_it_matters).to_lowercase();
    for phrase in LEGAL_ADVICE_PHRASES {
        if lc.contains(phrase) {
            return Err(PreflightFailure::LegalAdviceFraming { reason: phrase });
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use cathedral_types::card::*;
    use chrono::Utc;

    fn card_with(citations: Vec<Source>, no_legal_advice: bool, summary: &str) -> Card {
        Card {
            id: CardId("c".into()),
            jurisdiction: Jurisdiction::Eu,
            topic: "t".into(),
            worker_owner_hotkey: "h".into(),
            polaris_agent_id: "a".into(),
            title: "T".into(),
            summary: summary.into(),
            what_changed: "x".into(),
            why_it_matters: "y".into(),
            action_notes: "n".into(),
            risks: "r".into(),
            citations,
            confidence: 0.8,
            no_legal_advice,
            last_refreshed_at: Utc::now(),
            refresh_cadence_hours: 24,
        }
    }

    fn good_source() -> Source {
        Source {
            url: "https://eur-lex.europa.eu/example".into(),
            class: SourceClass::OfficialJournal,
            fetched_at: Utc::now(),
            status: 200,
            content_hash: "deadbeef".into(),
        }
    }

    #[test]
    fn no_citations_fails() {
        let c = card_with(vec![], true, "ok");
        assert!(matches!(preflight(&c), Err(PreflightFailure::NoCitations)));
    }

    #[test]
    fn missing_no_legal_advice_marker_fails() {
        let c = card_with(vec![good_source()], false, "ok");
        assert!(matches!(preflight(&c), Err(PreflightFailure::MissingNoLegalAdviceMarker)));
    }

    #[test]
    fn legal_advice_framing_fails() {
        let c = card_with(vec![good_source()], true, "We recommend that you do this.");
        assert!(matches!(preflight(&c), Err(PreflightFailure::LegalAdviceFraming { .. })));
    }

    #[test]
    fn good_card_passes() {
        let c = card_with(vec![good_source()], true, "summary");
        preflight(&c).unwrap();
    }
}
