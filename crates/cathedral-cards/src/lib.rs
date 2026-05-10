//! Regulatory card registry, preflight, and scoring.
//!
//! Implements issue #78 acceptance criteria:
//! - Card registry by jurisdiction/topic/owner/required source classes/cadence
//! - Source baseline preferring official sources
//! - Live source freshness and diff checks (downstream consumers do the fetch;
//!   this crate scores from already-fetched evidence)
//! - Score parts: source quality, freshness, specificity, usefulness, clarity,
//!   maintenance
//! - Preflight failure on broken sources, uncited claims, legal-advice framing
//!
//! First baseline target (per #78): a small set of jurisdictions/topics where
//! official source quality is high. Default registry seeds five cards: EU AI
//! Act, US AI Executive Order, UK AI Safety Institute, EU GDPR enforcement,
//! California Consumer Privacy Act. Operators can override with TOML.

#![forbid(unsafe_code)]

pub mod registry;
pub mod preflight;
pub mod score;

pub use registry::{CardRegistry, RegistryEntry};
pub use preflight::{preflight, PreflightFailure};
pub use score::score_card;
