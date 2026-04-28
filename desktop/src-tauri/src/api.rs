//! HTTP client for the RadSpeed cloud Impressions endpoint.

use reqwest::header::{HeaderMap, HeaderValue, AUTHORIZATION, CONTENT_TYPE};
use serde::Serialize;
use std::time::Duration;

const ENDPOINT_PATH: &str = "/api/impressions/text";

#[derive(Debug, Serialize)]
struct ImpressionsBody<'a> {
    findings: &'a str,
    modality: Option<&'a str>,
    style: Option<serde_json::Value>,
    with_guidelines: bool,
}

pub async fn fetch_impression(
    api_base: &str,
    findings: &str,
    use_guidelines: bool,
    bearer_token: &str,
) -> Result<String, String> {
    if findings.trim().is_empty() {
        return Err("nothing selected".to_string());
    }
    let url = format!("{}{}", api_base.trim_end_matches('/'), ENDPOINT_PATH);

    let mut headers = HeaderMap::new();
    headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
    if !bearer_token.is_empty() {
        if let Ok(value) = HeaderValue::from_str(&format!("Bearer {bearer_token}")) {
            headers.insert(AUTHORIZATION, value);
        }
    }

    let body = ImpressionsBody {
        findings,
        modality: None,
        style: None,
        with_guidelines: use_guidelines,
    };

    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(45))
        .build()
        .map_err(|e| format!("client init: {e}"))?;

    let resp = client
        .post(&url)
        .headers(headers)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("request failed: {e}"))?;

    let status = resp.status();
    let text = resp
        .text()
        .await
        .map_err(|e| format!("read body: {e}"))?;

    if !status.is_success() {
        return Err(format!("HTTP {status}: {}", text.trim()));
    }
    Ok(text)
}

/// Cheap reachability check used by the settings window.
pub async fn ping(api_base: &str) -> Result<(), String> {
    let url = format!("{}/health", api_base.trim_end_matches('/'));
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(8))
        .build()
        .map_err(|e| format!("client init: {e}"))?;
    let resp = client.get(&url).send().await.map_err(|e| format!("{e}"))?;
    if resp.status().is_success() {
        Ok(())
    } else {
        Err(format!("HTTP {}", resp.status()))
    }
}
