//! Bearer-token middleware for mutating endpoints (issue #79).

use axum::{
    body::Body,
    extract::Request,
    http::{header, StatusCode},
    middleware::Next,
    response::Response,
};

#[derive(Clone)]
pub struct BearerToken(pub String);

pub async fn require_bearer(req: Request<Body>, next: Next) -> Result<Response, StatusCode> {
    let token = req
        .extensions()
        .get::<BearerToken>()
        .cloned()
        .ok_or(StatusCode::INTERNAL_SERVER_ERROR)?;
    let header = req
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok());
    match header {
        Some(h) if h.strip_prefix("Bearer ").map(str::trim) == Some(token.0.as_str()) => {
            Ok(next.run(req).await)
        }
        _ => Err(StatusCode::UNAUTHORIZED),
    }
}
