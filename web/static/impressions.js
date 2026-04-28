// RadSpeed Impressions — public free wedge tool.
// Streams an impression from /api/impressions/stream over Server-Sent Events,
// persists style preferences in localStorage, and offers one-click clipboard.

(() => {
  const $ = (id) => document.getElementById(id);

  const STYLE_KEYS = [
    "style_spelling",
    "style_impression_style",
    "style_laterality",
    "style_numerals",
    "style_measurement_unit",
    "style_negation_phrasing",
  ];
  const STORAGE_PREFIX = "radspeed_impressions:";

  function loadStyle() {
    for (const k of STYLE_KEYS) {
      const v = localStorage.getItem(STORAGE_PREFIX + k);
      if (v !== null && $(k)) $(k).value = v;
    }
  }

  function persistStyle() {
    for (const k of STYLE_KEYS) {
      if ($(k)) localStorage.setItem(STORAGE_PREFIX + k, $(k).value);
    }
  }

  function collectStyle() {
    const s = {};
    for (const k of STYLE_KEYS) {
      if ($(k)) s[k] = $(k).value;
    }
    return s;
  }

  function setStatus(msg, kind) {
    const el = $("status");
    el.textContent = msg || "";
    el.classList.remove("error", "success");
    if (kind) el.classList.add(kind);
  }

  function setOutput(text, isPlaceholder) {
    const out = $("impression-output");
    out.textContent = text;
    out.classList.toggle("empty", !!isPlaceholder);
  }

  async function generate() {
    const findings = $("findings").value.trim();
    if (!findings) {
      setStatus("Paste some findings first.", "error");
      return;
    }
    persistStyle();

    const btn = $("btn-generate");
    btn.disabled = true;
    btn.textContent = "Generating...";
    $("btn-copy").disabled = true;
    setOutput("", false);
    setStatus("Streaming impression...");

    let buffer = "";
    let aborted = false;

    try {
      const resp = await fetch("/api/impressions/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          findings,
          modality: $("modality").value.trim() || null,
          with_guidelines: $("with-guidelines").checked,
          style: collectStyle(),
        }),
      });

      if (!resp.ok) {
        let detail = `${resp.status} ${resp.statusText}`;
        try {
          const j = await resp.json();
          if (j.detail) detail = j.detail;
        } catch (_) {}
        throw new Error(detail);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let pending = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        pending += decoder.decode(value, { stream: true });

        // SSE frames are separated by "\n\n"
        let idx;
        while ((idx = pending.indexOf("\n\n")) !== -1) {
          const frame = pending.slice(0, idx);
          pending = pending.slice(idx + 2);
          for (const line of frame.split("\n")) {
            if (!line.startsWith("data: ")) continue;
            const data = line.slice(6);
            try {
              const obj = JSON.parse(data);
              if (obj.token) {
                buffer += obj.token;
                setOutput(buffer, false);
              } else if (obj.error) {
                aborted = true;
                throw new Error(obj.error);
              } else if (obj.done) {
                // graceful end
              }
            } catch (e) {
              if (aborted) throw e;
              // ignore unparseable frame
            }
          }
        }
      }

      if (!buffer.trim()) {
        setStatus("No impression returned. Try again with more detail.", "error");
        setOutput("The generated impression will appear here.", true);
      } else {
        setStatus("Done. Copied to clipboard.", "success");
        $("btn-copy").disabled = false;
        copyToClipboard(buffer.trim(), /* silent */ true);
      }
    } catch (err) {
      setStatus(`Error: ${err.message || err}`, "error");
      if (!buffer) setOutput("The generated impression will appear here.", true);
    } finally {
      btn.disabled = false;
      btn.textContent = "Generate Impression";
    }
  }

  async function copyToClipboard(text, silent) {
    if (!text) {
      text = ($("impression-output").textContent || "").trim();
    }
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      if (!silent) setStatus("Copied to clipboard.", "success");
    } catch (_) {
      // Fallback: select range then execCommand. Older browsers / insecure contexts.
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); } catch (_) {}
      document.body.removeChild(ta);
      if (!silent) setStatus("Copied to clipboard.", "success");
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    loadStyle();

    $("btn-generate").addEventListener("click", generate);
    $("btn-copy").addEventListener("click", () => copyToClipboard(null, false));

    // Cmd/Ctrl + Enter from the findings textarea triggers generate.
    $("findings").addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        generate();
      }
    });
    $("findings").addEventListener("input", () => {
      $("findings-count").textContent = `${$("findings").value.length} chars`;
    });

    for (const k of STYLE_KEYS) {
      if ($(k)) $(k).addEventListener("change", persistStyle);
    }
  });
})();
