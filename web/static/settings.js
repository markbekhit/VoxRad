"use strict";

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// Badge helpers
// ---------------------------------------------------------------------------
function makeBadge(configured) {
  const span = document.createElement("span");
  span.className = `key-badge ${configured ? "configured" : "missing"}`;
  span.textContent = configured ? "✓ Configured" : "✗ Not set";
  return span;
}

function setBadge(elementId, configured) {
  const el = $(elementId);
  if (!el) return;
  el.innerHTML = "";
  el.appendChild(makeBadge(configured));
}

// ---------------------------------------------------------------------------
// Provider selection UI
// ---------------------------------------------------------------------------
let _currentProvider = "";
let _keys = { transcription: false, text: false, deepgram: false, assemblyai: false };

function _updateProviderUI() {
  const val = _currentProvider;
  ["none", "deepgram", "assemblyai"].forEach((id) => {
    const opt = $(`opt-${id}`);
    if (opt) opt.classList.toggle("selected", (id === "none" ? "" : id) === val);
  });

  // Show Groq fields only when no streaming provider selected
  const groqFields = $("groq-fields");
  if (groqFields) groqFields.style.display = val === "" ? "" : "none";

  // Show warning if selected provider has no key
  const warn = $("warn-no-key");
  if (warn) {
    const needsKey = val === "deepgram" ? !_keys.deepgram
                   : val === "assemblyai" ? !_keys.assemblyai
                   : false;
    warn.style.display = needsKey ? "" : "none";
  }
}

document.querySelectorAll('input[name="streaming_stt_provider"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    _currentProvider = radio.value;
    _updateProviderUI();
  });
});

// ---------------------------------------------------------------------------
// Load settings from API
// ---------------------------------------------------------------------------
async function loadSettings() {
  try {
    const resp = await fetch("/api/settings");
    if (!resp.ok) throw new Error(resp.statusText);
    const data = await resp.json();

    // Live-update callback URL previews when redirect base URL changes
    const rbuEl = $("oauth_redirect_base_url");
    if (rbuEl && !rbuEl._listenerAdded) {
      rbuEl.addEventListener("input", () => _updateCallbackUrls(rbuEl.value.trim()));
      rbuEl._listenerAdded = true;
    }

    _keys = data.keys;
    _currentProvider = data.streaming_stt_provider || "";

    // Set radio
    const radio = document.querySelector(
      `input[name="streaming_stt_provider"][value="${_currentProvider}"]`
    );
    if (radio) radio.checked = true;

    // Set text fields
    if ($("transcription_base_url")) $("transcription_base_url").value = data.transcription_base_url || "";
    if ($("transcription_model"))    $("transcription_model").value    = data.transcription_model    || "";
    if ($("text_base_url"))          $("text_base_url").value          = data.text_base_url          || "";
    if ($("text_model"))             $("text_model").value             = data.text_model             || "";
    if ($("fhir_export_enabled"))    $("fhir_export_enabled").checked  = !!data.fhir_export_enabled;

    // OAuth settings
    const oauth = data.oauth || {};
    if ($("oauth_redirect_base_url"))  $("oauth_redirect_base_url").value  = oauth.redirect_base_url  || "";
    if ($("google_client_id"))         $("google_client_id").value         = oauth.google_client_id   || "";
    if ($("google_client_secret"))     $("google_client_secret").value     = oauth.google_client_secret ? "••••••••" : "";
    if ($("microsoft_client_id"))      $("microsoft_client_id").value      = oauth.microsoft_client_id  || "";
    if ($("microsoft_client_secret"))  $("microsoft_client_secret").value  = oauth.microsoft_client_secret ? "••••••••" : "";

    _updateCallbackUrls(oauth.redirect_base_url || "");

    // Reporting style
    const style = data.style || {};
    const styleFields = [
      ["style_spelling",             style.spelling],
      ["style_numerals",             style.numerals],
      ["style_measurement_unit",     style.measurement_unit],
      ["style_measurement_separator", style.measurement_separator],
      ["style_decimal_precision",    style.decimal_precision],
      ["style_laterality",           style.laterality],
      ["style_impression_style",     style.impression_style],
      ["style_negation_phrasing",    style.negation_phrasing],
      ["style_date_format",          style.date_format],
    ];
    for (const [id, val] of styleFields) {
      const el = $(id);
      if (el && val != null) el.value = String(val);
    }

    // Render badges
    setBadge("badge-groq",       data.keys.transcription);
    setBadge("badge-deepgram",   data.keys.deepgram);
    setBadge("badge-assemblyai", data.keys.assemblyai);
    setBadge("badge-text",       data.keys.text);

    // Key status panel
    setBadge("key-transcription", data.keys.transcription);
    setBadge("key-text",          data.keys.text);
    setBadge("key-deepgram",      data.keys.deepgram);
    setBadge("key-assemblyai",    data.keys.assemblyai);

    _updateProviderUI();
  } catch (err) {
    showStatus(`Failed to load settings: ${err.message}`, "error");
  }
}

// ---------------------------------------------------------------------------
// Save settings
// ---------------------------------------------------------------------------
async function saveSettings() {
  const body = {
    streaming_stt_provider: _currentProvider || null,
    transcription_base_url: ($("transcription_base_url") || {}).value || null,
    transcription_model:    ($("transcription_model")    || {}).value || null,
    text_base_url:          ($("text_base_url")          || {}).value || null,
    text_model:             ($("text_model")             || {}).value || null,
    fhir_export_enabled:    !!($("fhir_export_enabled")  || {}).checked,
    style_spelling:              ($("style_spelling")              || {}).value || null,
    style_numerals:              ($("style_numerals")              || {}).value || null,
    style_measurement_unit:      ($("style_measurement_unit")      || {}).value || null,
    style_measurement_separator: ($("style_measurement_separator") || {}).value || null,
    style_decimal_precision:     (() => {
      const v = ($("style_decimal_precision") || {}).value;
      return v === "" || v == null ? null : parseInt(v, 10);
    })(),
    style_laterality:            ($("style_laterality")            || {}).value || null,
    style_impression_style:      ($("style_impression_style")      || {}).value || null,
    style_negation_phrasing:     ($("style_negation_phrasing")     || {}).value || null,
    style_date_format:           ($("style_date_format")           || {}).value || null,
    oauth_redirect_base_url:     ($("oauth_redirect_base_url")     || {}).value || null,
    google_client_id:            ($("google_client_id")            || {}).value || null,
    google_client_secret:        (() => {
      const v = ($("google_client_secret") || {}).value || "";
      return v && !v.startsWith("•") ? v : null;
    })(),
    microsoft_client_id:         ($("microsoft_client_id")         || {}).value || null,
    microsoft_client_secret:     (() => {
      const v = ($("microsoft_client_secret") || {}).value || "";
      return v && !v.startsWith("•") ? v : null;
    })(),
  };

  const btn = $("btn-save");
  btn.disabled = true;
  const msg = $("save-msg");
  msg.textContent = "Saving…";
  msg.style.color = "var(--muted)";

  try {
    const resp = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    msg.textContent = "✓ Saved";
    msg.style.color = "var(--green)";
    setTimeout(() => { msg.textContent = ""; }, 3000);
  } catch (err) {
    msg.textContent = `Error: ${err.message}`;
    msg.style.color = "var(--red)";
  } finally {
    btn.disabled = false;
  }
}

function _updateCallbackUrls(base) {
  const b = base.replace(/\/+$/, "") || window.location.origin;
  const g = $("cb-google");    if (g) g.textContent = `${b}/auth/google/callback`;
  const m = $("cb-microsoft"); if (m) m.textContent = `${b}/auth/microsoft/callback`;
}

function showStatus(text, type) {
  const el = $("settings-status");
  if (!el) return;
  el.textContent = text;
  el.className = type || "";
  el.style.cssText = `padding:8px 12px;border-radius:6px;font-size:13px;margin-bottom:10px;
    border:1px solid ${type === "error" ? "var(--red)" : "var(--border)"};
    color:${type === "error" ? "var(--red)" : "var(--text)"};`;
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  loadSettings();
  $("btn-save").addEventListener("click", saveSettings);
});
