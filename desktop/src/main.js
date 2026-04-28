// Settings window — Tauri 2 frontend.
// Talks to the Rust backend exclusively via `invoke`.

const { invoke } = window.__TAURI__.core;

const $ = (id) => document.getElementById(id);

const state = {
  current: null,
};

async function load() {
  try {
    const cfg = await invoke("cmd_get_settings");
    state.current = cfg;
    $("api-base").value       = cfg.api_base       ?? "";
    $("hotkey").value         = cfg.hotkey         ?? "ctrl+i";
    $("use-guidelines").checked = !!cfg.use_guidelines;
    $("paste-mode").value     = cfg.paste_mode     ?? "goto_impression";
    $("jump-keys").value      = cfg.jump_keys      ?? "tab";
    $("bearer-token").value   = cfg.bearer_token   ?? "";
    updateJumpKeysVisibility();
  } catch (e) {
    setSaveStatus(`Load failed: ${e}`, "status-error");
  }
}

function readForm() {
  return {
    api_base:        $("api-base").value.trim() || "https://dictation.markbekhit.com",
    hotkey:          $("hotkey").value.trim()   || "ctrl+i",
    use_guidelines:  $("use-guidelines").checked,
    paste_mode:      $("paste-mode").value,
    jump_keys:       $("jump-keys").value.trim(),
    bearer_token:    $("bearer-token").value,
  };
}

function setSaveStatus(msg, cls = "muted small") {
  const el = $("save-status");
  el.textContent = msg;
  el.className = cls + (cls.includes("small") ? "" : " small");
}
function setTestStatus(msg, cls = "muted") {
  const el = $("test-status");
  el.textContent = msg;
  el.className = cls;
}

function updateJumpKeysVisibility() {
  const isGoto = $("paste-mode").value === "goto_impression";
  $("jump-keys-wrap").style.display = isGoto ? "" : "none";
}

async function save() {
  const settings = readForm();
  setSaveStatus("Saving…");
  try {
    await invoke("cmd_save_settings", { body: { settings } });
    state.current = settings;
    setSaveStatus("Saved.", "status-ok small");
    setTimeout(() => setSaveStatus(""), 2500);
  } catch (e) {
    setSaveStatus(`Save failed: ${e}`, "status-error small");
  }
}

async function testConnection() {
  const apiBase = $("api-base").value.trim() || "https://dictation.markbekhit.com";
  setTestStatus("Testing…");
  try {
    await invoke("cmd_test_api", { apiBase });
    setTestStatus("✓ Connected", "status-ok");
  } catch (e) {
    setTestStatus(`✗ ${e}`, "status-error");
  }
}

async function triggerNow() {
  await invoke("cmd_trigger_now");
}

async function hide() {
  await invoke("cmd_hide_settings");
}

window.addEventListener("DOMContentLoaded", () => {
  $("btn-save").addEventListener("click", save);
  $("btn-cancel").addEventListener("click", hide);
  $("btn-test").addEventListener("click", testConnection);
  $("btn-trigger").addEventListener("click", triggerNow);
  $("paste-mode").addEventListener("change", updateJumpKeysVisibility);
  load();
});
