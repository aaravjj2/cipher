const byId = (id) => document.getElementById(id);
const text = (value) => String(value ?? "--").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));

function badge(status) {
  const label = String(status || "unknown").replaceAll("_", " ");
  return `<span class="state ${status === "blocked" ? "blocked" : status === "ready_for_manual_research_run" ? "ready" : "muted"}">${text(label)}</span>`;
}

async function load() {
  const response = await fetch("/api/research-brief", { cache: "no-store" });
  if (!response.ok) throw new Error("Research brief data is unavailable");
  const data = await response.json();
  const components = data.registry.components || [];
  const holdout = data.holdout || {};
  byId("asOf").textContent = `Updated ${new Date(data.generated_at).toLocaleString()}`;
  byId("promotion").textContent = data.registry.promotion_eligible ? "Eligible" : "Blocked";
  byId("holdout").textContent = `${holdout.maximum_common_tickers ?? 0} symbols / ${holdout.maximum_strict_independent_origins ?? 0} origins`;
  byId("models").textContent = `${components.filter((item) => item.research_status === "rejected_current_formulation").length} rejected`;
  byId("calibration").textContent = "Unavailable";
  byId("briefSummary").textContent = data.registry.promotion_eligible
    ? "A strategy has cleared its recorded research gate. Human review remains required."
    : "No strategy is eligible for promotion. The dashboard shows evidence and blockers, not trading instructions.";
  byId("scheduler").innerHTML = (data.scheduler || []).map((item) => `
    <div class="row"><div><strong>${text(item.purpose)}</strong><small>Layer ${text(item.layer)} · checked ${new Date(item.checked_at).toLocaleString()}</small></div><div>${badge(item.status)}${item.blocker ? `<small class="blocker">${text(item.blocker.replaceAll("_", " "))}</small>` : ""}</div></div>
  `).join("") || '<div class="empty">No scheduler record exists yet.</div>';
  byId("agent").innerHTML = `<strong>${text(data.agent_panel.status.replaceAll("_", " "))}</strong><p>${text(data.agent_panel.reason)}</p>`;
  byId("calibrationDetail").innerHTML = `<strong>${text(data.agent_panel.calibration_status.replaceAll("_", " "))}</strong><p>Confidence cannot be scored until timestamped decisions and realized outcomes exist.</p>`;
  byId("registry").innerHTML = components.map((item) => `<tr><td>${text(item.name)}</td><td>${badge(item.research_status)}</td><td>${text(item.reason)}</td><td>${text(item.conditions_before_reconsideration)}</td></tr>`).join("");
}

load().catch((error) => {
  byId("briefSummary").textContent = error.message;
  byId("scheduler").innerHTML = '<div class="empty">Local governance artifacts could not be read.</div>';
});
