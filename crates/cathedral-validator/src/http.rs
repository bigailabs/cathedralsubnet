//! HTTP surface: health (public), claim intake (bearer-protected).

use axum::{
    extract::State,
    http::StatusCode,
    response::Json,
    routing::{get, post},
    Router,
};
use cathedral_types::PolarisAgentClaim;

use crate::{auth, health::HealthSnapshot, state::ValidatorState};

pub fn router(state: ValidatorState) -> Router {
    let bearer = auth::BearerToken((*state.bearer).clone());
    Router::new()
        .route("/health", get(get_health))
        .route(
            "/v1/claim",
            post(post_claim).layer(axum::middleware::from_fn(auth::require_bearer)),
        )
        .layer(axum::Extension(bearer))
        .with_state(state)
}

async fn get_health(State(state): State<ValidatorState>) -> Json<HealthSnapshot> {
    Json(state.health.snapshot())
}

async fn post_claim(
    State(state): State<ValidatorState>,
    Json(claim): Json<PolarisAgentClaim>,
) -> Result<StatusCode, (StatusCode, String)> {
    claim
        .validate_shape()
        .map_err(|e| (StatusCode::BAD_REQUEST, e.to_string()))?;
    state.health.update(|h| h.claims_pending += 1);
    // Persisting and verifying happens on the worker loop. Returning 202
    // lets the miner know the claim was accepted for verification.
    Ok(StatusCode::ACCEPTED)
}
