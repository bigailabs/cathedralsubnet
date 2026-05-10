//! Helpers for converting card scores into a weight vector.

/// Normalize a list of `(uid, raw_score)` into a weight vector summing to 1.0.
/// Negative or NaN scores are clamped to 0.0; if the total is 0.0 the result
/// is an empty vec, signalling "do not call set_weights".
pub fn normalize(scores: &[(u16, f32)]) -> Vec<(u16, f32)> {
    let cleaned: Vec<(u16, f32)> = scores
        .iter()
        .copied()
        .map(|(u, s)| (u, if s.is_finite() && s > 0.0 { s } else { 0.0 }))
        .collect();
    let total: f32 = cleaned.iter().map(|(_, s)| *s).sum();
    if total <= 0.0 {
        return Vec::new();
    }
    cleaned.into_iter().map(|(u, s)| (u, s / total)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_basic() {
        let out = normalize(&[(0, 1.0), (1, 1.0), (2, 2.0)]);
        let total: f32 = out.iter().map(|(_, w)| w).sum();
        assert!((total - 1.0).abs() < 1e-6);
    }

    #[test]
    fn normalize_zero_returns_empty() {
        assert!(normalize(&[(0, 0.0), (1, 0.0)]).is_empty());
    }

    #[test]
    fn normalize_drops_negative_and_nan() {
        let out = normalize(&[(0, -1.0), (1, f32::NAN), (2, 1.0)]);
        assert_eq!(out.len(), 3);
        assert_eq!(out[2].1, 1.0);
        assert_eq!(out[0].1, 0.0);
        assert_eq!(out[1].1, 0.0);
    }
}
