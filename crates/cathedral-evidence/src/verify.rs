//! Ed25519 verification of Polaris records and artifact-bytes hash check.
//!
//! Each record carries a base64-encoded `signature` field. The signed payload
//! is the canonical JSON of the record with `signature` removed. We use serde
//! to drop the field deterministically.

use base64::Engine;
use cathedral_types::{PolarisArtifactRecord, PolarisManifest, PolarisRunRecord, PolarisUsageRecord};
use ed25519_dalek::{Signature, VerifyingKey};

use crate::EvidenceError;

fn check_signature<T: serde::Serialize>(
    record: &T,
    signature_b64: &str,
    pubkey: &VerifyingKey,
    label: &'static str,
) -> Result<(), EvidenceError> {
    let mut value = serde_json::to_value(record).map_err(|_| EvidenceError::BadSignature(label))?;
    if let serde_json::Value::Object(ref mut map) = value {
        map.remove("signature");
    }
    let payload = serde_json::to_vec(&value).map_err(|_| EvidenceError::BadSignature(label))?;

    let sig_bytes = base64::engine::general_purpose::STANDARD
        .decode(signature_b64)
        .map_err(|_| EvidenceError::BadSignature(label))?;
    let signature = Signature::from_slice(&sig_bytes).map_err(|_| EvidenceError::BadSignature(label))?;

    pubkey
        .verify_strict(&payload, &signature)
        .map_err(|_| EvidenceError::BadSignature(label))
}

pub fn manifest(m: &PolarisManifest, pk: &VerifyingKey) -> Result<(), EvidenceError> {
    check_signature(m, &m.signature, pk, "manifest")
}

pub fn run(r: &PolarisRunRecord, pk: &VerifyingKey) -> Result<(), EvidenceError> {
    check_signature(r, &r.signature, pk, "run")
}

pub fn artifact_record(a: &PolarisArtifactRecord, pk: &VerifyingKey) -> Result<(), EvidenceError> {
    check_signature(a, &a.signature, pk, "artifact")
}

pub fn usage(u: &PolarisUsageRecord, pk: &VerifyingKey) -> Result<(), EvidenceError> {
    check_signature(u, &u.signature, pk, "usage")
}

/// Hash the served artifact bytes with BLAKE3 and compare to the record.
pub fn artifact_bytes(a: &PolarisArtifactRecord, bytes: &[u8]) -> Result<(), EvidenceError> {
    let computed = blake3::hash(bytes);
    let expected = hex::decode(&a.content_hash).map_err(|_| EvidenceError::HashMismatch(a.artifact_id.clone()))?;
    if computed.as_bytes() == expected.as_slice() {
        Ok(())
    } else {
        Err(EvidenceError::HashMismatch(a.artifact_id.clone()))
    }
}
