//! Shared wire types for Cathedral.
//!
//! Every record that crosses a process boundary lives here. Crates downstream
//! depend on these definitions but never extend them privately.

#![forbid(unsafe_code)]

pub mod claim;
pub mod evidence;
pub mod card;
pub mod hotkey;

pub use claim::{PolarisAgentClaim, ClaimVersion};
pub use evidence::{PolarisManifest, PolarisRunRecord, PolarisArtifactRecord, PolarisUsageRecord, EvidenceBundle};
pub use card::{Card, CardId, Jurisdiction, Source, ScoreParts};
pub use hotkey::Hotkey;
