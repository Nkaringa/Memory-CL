// Memory-CL inspector — vanilla JS, calls existing API only.
(function () {
  "use strict";

  const baseURL = location.origin;

  // ---------- helpers ----------
  function fmt(payload) {
    return JSON.stringify(payload, null, 2);
  }

  async function fetchJSON(path, options) {
    const init = Object.assign({headers: {"content-type": "application/json"}}, options || {});
    const resp = await fetch(baseURL + path, init);
    let body;
    try { body = await resp.json(); } catch (_) { body = await resp.text(); }
    if (!resp.ok) {
      throw {status: resp.status, body: body, url: path};
    }
    return body;
  }

  function setOut(elId, payload) {
    document.getElementById(elId).textContent = fmt(payload);
  }

  function setError(elId, err) {
    document.getElementById(elId).textContent = "ERROR\n" + fmt(err);
  }

  // ---------- tab switching ----------
  document.querySelectorAll(".tab").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll(".tab").forEach(function (b) { b.classList.remove("active"); });
      document.querySelectorAll(".tab-panel").forEach(function (p) { p.classList.remove("active"); });
      btn.classList.add("active");
      document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
    });
  });

  // ---------- status pill ----------
  async function refreshStatusPill() {
    const pill = document.getElementById("status-pill");
    try {
      const s = await fetchJSON("/status");
      const env = s.environment;
      const safe = s.safe_mode && s.safe_mode.enabled;
      const ok = s.boot_overall_ok && !safe;
      pill.textContent = "status: " + (ok ? "ok" : safe ? "safe-mode" : "degraded");
      pill.className = "pill " + (ok ? "pill-ok" : safe ? "pill-degraded" : "pill-failed");
      pill.title = "env=" + env + " · tools=" + s.mcp_tool_count + " · schema=" + s.schema_version;
    } catch (err) {
      pill.textContent = "status: unreachable";
      pill.className = "pill pill-failed";
    }
  }

  // ---------- retrieval ----------
  document.getElementById("form-retrieval").addEventListener("submit", async function (e) {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target).entries());
    try {
      const result = await fetchJSON("/retrieve", {
        method: "POST",
        body: JSON.stringify({
          repo_id: data.repo_id,
          text: data.text,
          top_k: parseInt(data.top_k || "5", 10),
        }),
      });
      setOut("out-retrieval", result);
    } catch (err) {
      setError("out-retrieval", err);
    }
  });

  // ---------- graph ----------
  document.getElementById("form-graph").addEventListener("submit", async function (e) {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target).entries());
    try {
      const result = await fetchJSON("/mcp/tools/query_graph", {
        method: "POST",
        body: JSON.stringify({
          repo_id: data.repo_id,
          node: data.node,
          depth: parseInt(data.depth || "1", 10),
        }),
      });
      setOut("out-graph", result);
    } catch (err) {
      setError("out-graph", err);
    }
  });

  // ---------- ingestion (status shows backend health, not a write) ----------
  document.getElementById("btn-ingestion-status").addEventListener("click", async function () {
    try {
      const s = await fetchJSON("/status");
      setOut("out-ingestion", s);
    } catch (err) {
      setError("out-ingestion", err);
    }
  });

  // ---------- snapshots ----------
  let lastSnapshot = null;
  document.getElementById("form-snapshot").addEventListener("submit", async function (e) {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target).entries());
    try {
      const snap = await fetchJSON("/snapshot/build", {
        method: "POST",
        body: JSON.stringify({tenant_id: data.tenant_id, state_version_token: "v0"}),
      });
      const target = lastSnapshot ? "out-snapshot-b" : "out-snapshot-a";
      setOut(target, snap);
      const diff = document.getElementById("snapshot-diff");
      if (lastSnapshot) {
        diff.textContent = lastSnapshot.snapshot_id === snap.snapshot_id
          ? "snapshot_id: IDENTICAL"
          : "snapshot_id: DIFFERS — system state changed between builds";
      } else {
        diff.textContent = "click again to compare";
      }
      lastSnapshot = snap;
    } catch (err) {
      setError("out-snapshot-a", err);
    }
  });

  // ---------- audit ----------
  document.getElementById("btn-audit-tail").addEventListener("click", async function () {
    try {
      const tail = await fetchJSON("/audit/tail?limit=25");
      setOut("out-audit", tail);
    } catch (err) {
      setError("out-audit", err);
    }
  });
  document.getElementById("btn-audit-verify").addEventListener("click", async function () {
    try {
      const v = await fetchJSON("/audit/verify");
      setOut("out-audit", v);
    } catch (err) {
      setError("out-audit", err);
    }
  });

  // initial load
  refreshStatusPill();
  setInterval(refreshStatusPill, 30000);
})();
