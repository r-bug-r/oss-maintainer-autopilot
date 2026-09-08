// Autopilot dashboard: a small, dependency-free client over the local JSON API.
// Every value rendered here comes from the server; nothing is invented.

const state = {
  repositories: [],
  repositoryId: null,
  actor: null,
  actors: [],
  provider: "unknown",
  proposals: [],
  filters: { state: "", risk: "", q: "" },
  path: undefined,
  proposal: null,
  audit: [],
  settings: null,
  system: null,
  loading: false,
};

const els = {
  main: document.getElementById("main"),
  status: document.getElementById("status"),
  repoSelect: document.getElementById("repo-select"),
  actorSelect: document.getElementById("actor-select"),
  providerBadge: document.getElementById("provider-badge"),
  dialog: document.getElementById("confirm-dialog"),
  dialogTitle: document.getElementById("dialog-title"),
  dialogSummary: document.getElementById("dialog-summary"),
  dialogFacts: document.getElementById("dialog-facts"),
  dialogConfirm: document.getElementById("dialog-confirm"),
};

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = String(value);
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function setStatus(message, tone = "") {
  els.status.textContent = message || "";
  if (tone) els.status.dataset.tone = tone;
  else delete els.status.dataset.tone;
}

async function api(path, options = {}) {
  const headers = { Accept: "application/json" };
  if (state.actor) headers["X-Autopilot-Actor"] = state.actor;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method: options.method || "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const text = await response.text();
  let payload = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const message = payload?.error?.message || `request failed (${response.status})`;
    const error = new Error(message);
    error.status = response.status;
    error.code = payload?.error?.code;
    throw error;
  }
  return payload;
}

function badge(text, tone, wrap = false) {
  return el("span", { class: `badge badge-${tone}${wrap ? " badge-wrap" : ""}`, text });
}

function statusBadge(status) {
  const tones = {
    pending_approval: "warning",
    approved: "accent",
    executing: "accent",
    executed: "success",
    rejected: "neutral",
    expired: "neutral",
    action_failed: "danger",
  };
  return badge(status.replaceAll("_", " "), tones[status] || "neutral");
}

function riskBadge(risk) {
  if (!risk) return badge("risk unknown", "neutral");
  const tones = { low: "success", medium: "warning", high: "danger" };
  return badge(`risk: ${risk}`, tones[risk] || "neutral");
}

function formatTime(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function relativeAge(value) {
  if (!value) return "—";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "—";
  const minutes = Math.round((Date.now() - then) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

// ------------------------------------------------------------------- chrome
// The app is hash-routed, so a plain `href="#main"` skip link would be read as
// a route and would bounce the user back to the overview. Move focus instead.
function wireSkipLink() {
  const skip = document.querySelector(".skip-link");
  if (!skip) return;
  skip.addEventListener("click", (event) => {
    event.preventDefault();
    els.main.focus();
    els.main.scrollIntoView({ block: "start" });
  });
}

// Chromium lets Tab leave a modal <dialog> for the browser UI, which drops
// visible focus on the way out. Keep the cycle inside the dialog: the decision
// is two buttons and a stray Tab must not land on the page behind it.
function wireDialogFocusTrap() {
  els.dialog.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    const focusable = [...els.dialog.querySelectorAll("button:not([disabled])")];
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    if (!els.dialog.contains(active)) {
      event.preventDefault();
      first.focus();
    } else if (event.shiftKey && active === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  });
}

async function bootstrap() {
  try {
    const health = await api("/api/health");
    state.provider = health.provider || "unknown";
    els.providerBadge.textContent = `provider: ${state.provider}`;
    els.providerBadge.className =
      state.provider === "fake" ? "badge badge-neutral" : "badge badge-accent";

    state.system = await api("/api/system");
    const data = await api("/api/repositories");
    state.repositories = data.repositories || [];
    const saved = localStorage.getItem("autopilot.repository");
    const preferred =
      state.repositories.find((repo) => repo.id === saved)?.id || state.repositories[0]?.id || null;
    state.repositoryId = preferred;
    renderRepositoryOptions();
    await loadActors();
    await route();
  } catch (error) {
    renderFatal(error);
  }
}

function renderRepositoryOptions() {
  els.repoSelect.replaceChildren();
  if (state.repositories.length === 0) {
    els.repoSelect.append(el("option", { value: "", text: "No repositories" }));
    els.repoSelect.disabled = true;
    return;
  }
  els.repoSelect.disabled = false;
  for (const repo of state.repositories) {
    els.repoSelect.append(
      el("option", {
        value: repo.id,
        text: `${repo.full_name} · ${repo.pending_count} pending`,
        selected: repo.id === state.repositoryId,
      }),
    );
  }
}

// The selector carries a pending count, so a decision or an apply must refresh
// it; otherwise the header keeps advertising work the maintainer already did.
async function refreshRepositoryCounts() {
  if (state.repositories.length === 0) return;
  try {
    const data = await api("/api/repositories");
    state.repositories = data.repositories || [];
    renderRepositoryOptions();
  } catch {
    // A stale count is a cosmetic problem; the screen it belongs to still works.
  }
}

async function loadActors() {
  state.actors = [];
  if (!state.repositoryId) {
    els.actorSelect.replaceChildren(el("option", { value: "", text: "—" }));
    return;
  }
  try {
    const data = await api(`/api/repositories/${encodeURIComponent(state.repositoryId)}/actors`);
    state.actors = data.actors || [];
  } catch {
    state.actors = [];
  }
  const saved = localStorage.getItem("autopilot.actor");
  const preferred =
    state.actors.find((actor) => actor.actor_login === saved)?.actor_login ||
    state.actors.find((actor) => actor.role === "owner")?.actor_login ||
    state.actors[0]?.actor_login ||
    null;
  state.actor = preferred;
  els.actorSelect.replaceChildren();
  if (state.actors.length === 0) {
    els.actorSelect.append(el("option", { value: "", text: "No roles granted" }));
    return;
  }
  for (const actor of state.actors) {
    els.actorSelect.append(
      el("option", {
        value: actor.actor_login,
        text: `${actor.actor_login} (${actor.role})`,
        selected: actor.actor_login === state.actor,
      }),
    );
  }
}

els.repoSelect.addEventListener("change", async () => {
  state.repositoryId = els.repoSelect.value;
  localStorage.setItem("autopilot.repository", state.repositoryId);
  await loadActors();
  await route();
});

els.actorSelect.addEventListener("change", () => {
  state.actor = els.actorSelect.value;
  localStorage.setItem("autopilot.actor", state.actor);
  setStatus(`Acting as ${state.actor}.`, "success");
});

window.addEventListener("hashchange", () => {
  route();
});

// ------------------------------------------------------------------ routing
// A hash-only navigation replaces the whole main region without a document
// load, so the browser keeps focus on whatever was removed. Keyboard and
// screen-reader users would land back at the top of the page with no
// announcement. Move focus to the new view's heading instead; filter-only
// changes keep the same path and must not steal focus from the field.
function focusViewHeading() {
  const heading = els.main.querySelector("h1");
  if (!heading) {
    els.main.focus();
    return;
  }
  heading.setAttribute("tabindex", "-1");
  heading.focus();
  window.scrollTo({ top: 0, behavior: "instant" });
}

// Deciding re-renders the whole detail view, which destroys the button the
// keyboard user just pressed and leaves focus on <body>. Send it to whatever
// the decision unlocked (apply), falling back to the first usable control and
// finally the heading, so the next Tab continues from the decision.
function focusNextAction() {
  const target =
    els.main.querySelector("#apply-action:not([disabled])") ||
    els.main.querySelector(".action-bar-buttons button:not([disabled])") ||
    els.main.querySelector(".action-bar-buttons a");
  if (target) target.focus();
  else focusViewHeading();
}

async function route() {
  const hash = window.location.hash || "#/overview";
  const [pathPart, queryPart] = hash.slice(1).split("?");
  const params = new URLSearchParams(queryPart || "");
  const segments = pathPart.split("/").filter(Boolean);
  const view = segments[0] || "overview";
  const previousPath = state.path;
  state.path = pathPart;

  // A modal belongs to the screen that opened it; navigating away must not
  // leave a decision dialog floating over an unrelated page.
  if (els.dialog.open) els.dialog.close("navigate");

  document.querySelectorAll(".tab").forEach((tab) => {
    if (tab.dataset.view === view || (view === "proposal" && tab.dataset.view === "proposals")) {
      tab.setAttribute("aria-current", "page");
    } else {
      tab.removeAttribute("aria-current");
    }
  });

  try {
    if (view === "proposals") {
      state.filters = {
        state: params.get("state") || "",
        risk: params.get("risk") || "",
        q: params.get("q") || "",
      };
      await renderProposals();
    } else if (view === "proposal") {
      await renderProposalDetail(segments[1]);
    } else if (view === "audit") {
      await renderAudit();
    } else if (view === "settings") {
      await renderSettings();
    } else {
      await renderOverview();
    }
    if (previousPath !== undefined && previousPath !== pathPart) focusViewHeading();
  } catch (error) {
    renderFatal(error);
  }
}

function renderFatal(error) {
  els.main.replaceChildren(
    el(
      "div",
      { class: "error-state" },
      el("h1", { text: "The console could not load" }),
      el("p", { class: "small", text: error.message }),
      el("p", {
        class: "small muted",
        text: "Check that the server is running, the webhook secret is configured, and this browser can reach the API.",
      }),
      el(
        "button",
        { class: "button button-accent", type: "button", onclick: () => window.location.reload() },
        "Retry",
      ),
    ),
  );
  setStatus(error.message, "error");
}

function currentRepo() {
  return state.repositories.find((repo) => repo.id === state.repositoryId) || null;
}

function requireRepo() {
  const repo = currentRepo();
  if (!repo) {
    els.main.replaceChildren(
      el(
        "div",
        { class: "empty-state" },
        el("h1", { text: "No repository is connected" }),
        el("p", {
          class: "small",
          text: "Register a repository from the CLI with `autopilot repo add owner/name --owner your-login`. Autopilot cannot read or write a repository until you do.",
        }),
        el("p", {
          class: "small muted",
          text: "The default local mode uses deterministic fixtures and never contacts GitHub.",
        }),
      ),
    );
    return null;
  }
  return repo;
}

// ----------------------------------------------------------------- overview
async function renderOverview() {
  const repo = requireRepo();
  if (!repo) return;
  setStatus("");
  els.main.replaceChildren(
    el(
      "div",
      { class: "stack" },
      el(
        "div",
        { class: "section-title" },
        el("h1", { text: repo.full_name }),
        el("span", { class: "mono muted", text: repo.id }),
      ),
      el("div", { class: "skeleton", style: null }),
      el("div", { class: "skeleton" }),
    ),
  );

  const [proposals, audit, metrics, settings] = await Promise.all([
    api(`/api/proposals?repository_id=${encodeURIComponent(repo.id)}&limit=200`),
    api(`/api/audit?repository_id=${encodeURIComponent(repo.id)}&limit=8`),
    api(`/api/metrics?repository_id=${encodeURIComponent(repo.id)}`),
    api(`/api/repositories/${encodeURIComponent(repo.id)}/settings`),
  ]);
  const list = proposals.proposals || [];
  const pending = list.filter((item) => item.state === "pending_approval");
  const executed = list.filter((item) => item.state === "executed");
  const failed = list.filter((item) => item.state === "action_failed");

  const spendValue =
    metrics.model_calls.requests === 0
      ? "No data"
      : `$${metrics.model_calls.cost_usd.toFixed(4)}`;
  const spendLabel =
    metrics.model_calls.requests === 0
      ? "model spend, this month"
      : `model spend this month · ${metrics.model_calls.requests} calls`;

  els.main.replaceChildren(
    el(
      "div",
      { class: "stack" },
      el(
        "div",
        { class: "section-title" },
        el("h1", { text: repo.full_name }),
        badge(repo.provider, "neutral"),
        badge(state.provider === "fake" ? "offline fixtures" : "live provider", state.provider === "fake" ? "neutral" : "accent"),
        badge(repo.enabled ? "enabled" : "disabled", repo.enabled ? "success" : "danger"),
      ),
      el("p", {
        class: "panel-note",
        text: "Every model output becomes a proposal. Nothing is posted, labeled, or closed until a maintainer approves it.",
      }),

      state.actors.some((actor) => actor.role === "owner" || actor.role === "maintainer")
        ? null
        : el(
            "section",
            { class: "panel permission-state" },
            el("h2", { text: "No maintainer role is granted" }),
            el("p", {
              class: "small",
              text: "Proposals will be generated, but approvals are refused until a role exists. Grant one from the CLI:",
            }),
            el("p", {
              class: "mono small",
              text: `python -m autopilot roles grant ${repo.id} <github-login> maintainer`,
            }),
          ),

      el(
        "section",
        { class: "panel" },
        el(
          "div",
          { class: "kpi-row" },
          kpi(String(pending.length), "awaiting approval"),
          kpi(String(executed.length), "applied to the repository"),
          kpi(String(failed.length), "failed actions"),
          kpi(spendValue, spendLabel),
        ),
        el(
          "div",
          { class: "actions" },
          el(
            "a",
            { class: "button button-accent", href: "#/proposals?state=pending_approval" },
            `Review ${pending.length} proposal${pending.length === 1 ? "" : "s"}`,
          ),
          el("a", { class: "button", href: "#/settings" }, "Workflow and budget settings"),
          el("a", { class: "button button-quiet", href: "#/audit" }, "Open audit trail"),
        ),
      ),

      el(
        "div",
        { class: "grid-two" },
        el(
          "section",
          { class: "panel" },
          el(
            "div",
            { class: "panel-head" },
            el("h2", { text: "Recent activity" }),
            el("span", { class: "panel-note", text: "from the append-only audit chain" }),
          ),
          audit.events.length === 0
            ? el("div", { class: "empty-state" }, el("p", { text: "No audit events yet." }))
            : el(
                "div",
                { class: "table-wrap" },
                el(
                  "table",
                  {},
                  el("caption", { text: "Eight most recent audit events" }),
                  el(
                    "thead",
                    {},
                    el(
                      "tr",
                      {},
                      el("th", { scope: "col", text: "When" }),
                      el("th", { scope: "col", text: "Event" }),
                      el("th", { scope: "col", text: "Actor" }),
                    ),
                  ),
                  el(
                    "tbody",
                    {},
                    ...audit.events.map((event) =>
                      el(
                        "tr",
                        {},
                        el("td", { "data-label": "When", text: relativeAge(event.created_at) }),
                        el("td", { "data-label": "Event", text: event.event_type }),
                        el("td", { "data-label": "Actor", text: event.actor_login || "system" }),
                      ),
                    ),
                  ),
                ),
              ),
        ),
        el(
          "section",
          { class: "panel" },
          el("h2", { text: "Data boundaries" }),
          el(
            "dl",
            { class: "fact-list" },
            el("dt", { text: "Workflows" }),
            el(
              "dd",
              {
                text: Object.entries(settings.settings.workflows)
                  .map(([name, on]) => `${name}: ${on ? "on" : "off"}`)
                  .join(" · "),
              },
            ),
            el("dt", { text: "Daily requests" }),
            el("dd", {
              text: `${metrics.budget?.requests_today ?? 0} of ${metrics.budget?.request_limit ?? settings.settings.daily_request_limit}`,
            }),
            el("dt", { text: "Daily spend cap" }),
            el("dd", {
              text:
                (metrics.budget?.daily_cost_limit_usd ?? settings.settings.daily_cost_limit_usd) > 0
                  ? `$${(metrics.budget?.daily_cost_limit_usd ?? settings.settings.daily_cost_limit_usd).toFixed(2)}`
                  : "none set — only the request limit applies",
            }),
            el("dt", { text: "Proposal TTL" }),
            el("dd", { text: `${settings.settings.proposal_ttl_minutes} minutes` }),
            el("dt", { text: "Retention" }),
            el("dd", {
              text: "Audit events are permanent; proposals, calls, and deliveries are purged by the retention job.",
            }),
          ),
        ),
      ),
    ),
  );
}

function kpi(value, label) {
  return el(
    "div",
    { class: "kpi" },
    el("span", { class: "kpi-value", text: value }),
    el("span", { class: "kpi-label", text: label }),
  );
}

// ----------------------------------------------------------------- proposals
async function renderProposals() {
  const repo = requireRepo();
  if (!repo) return;
  setStatus("");
  const query = new URLSearchParams({ repository_id: repo.id, limit: "200" });
  if (state.filters.state) query.set("state", state.filters.state);

  const data = await api(`/api/proposals?${query.toString()}`);
  const items = data.proposals || [];
  const filtered = () => {
    let visible = items;
    if (state.filters.risk) visible = visible.filter((item) => item.risk === state.filters.risk);
    const needle = state.filters.q.trim().toLowerCase();
    if (needle) {
      visible = visible.filter((item) =>
        `${item.subject_number} ${item.workflow} ${item.summary}`.toLowerCase().includes(needle),
      );
    }
    return visible;
  };

  const stateSelect = el(
    "select",
    { class: "control", id: "filter-state", onchange: (event) => updateFilters({ state: event.target.value }) },
    ...[
      ["", "All states"],
      ["pending_approval", "Pending approval"],
      ["approved", "Approved"],
      ["executed", "Executed"],
      ["action_failed", "Action failed"],
      ["rejected", "Rejected"],
      ["expired", "Expired"],
    ].map(([value, label]) =>
      el("option", { value, text: label, selected: value === state.filters.state }),
    ),
  );
  const riskSelect = el(
    "select",
    { class: "control", id: "filter-risk", onchange: (event) => updateFilters({ risk: event.target.value }) },
    ...[
      ["", "All risk levels"],
      ["low", "Low"],
      ["medium", "Medium"],
      ["high", "High"],
    ].map(([value, label]) =>
      el("option", { value, text: label, selected: value === state.filters.risk }),
    ),
  );
  const search = el("input", {
    class: "input",
    id: "filter-q",
    type: "search",
    value: state.filters.q,
    placeholder: "Filter by issue number or text",
    "aria-label": "Filter proposals by issue number or text",
    oninput: (event) => {
      state.filters.q = event.target.value;
      // Keep the address bar in step without a navigation: `replaceState` fires
      // no hashchange, so the field keeps focus and the caret while typing.
      syncFilterUrl();
      renderTableOnly();
      // Typing changes whether there is anything to clear, but the table-only
      // re-render never rebuilds this button.
      clearButton.disabled = !filtersActive();
    },
  });

  const clearButton = el(
    "button",
    {
      class: "button button-quiet",
      type: "button",
      id: "clear-filters",
      // With every filter at its default the control has no effect, so it is
      // disabled rather than offering a button that does nothing.
      disabled: !filtersActive(),
      onclick: () => updateFilters({ state: "", risk: "", q: "" }),
    },
    "Clear filters",
  );

  els.main.replaceChildren(
    el(
      "div",
      { class: "stack" },
      el(
        "div",
        { class: "section-title" },
        el("h1", { text: "Proposals" }),
        el("span", { class: "panel-note", text: repo.full_name }),
      ),
      el(
        "div",
        { class: "filter-bar" },
        el("label", { class: "field", for: "filter-state" }, el("span", { class: "field-label", text: "Status" }), stateSelect),
        el("label", { class: "field", for: "filter-risk" }, el("span", { class: "field-label", text: "Risk" }), riskSelect),
        el("label", { class: "field", for: "filter-q" }, el("span", { class: "field-label", text: "Search" }), search),
        clearButton,
      ),
      el("div", { id: "proposal-table" }),
    ),
  );

  function renderTableOnly() {
    const target = document.getElementById("proposal-table");
    if (!target) return;
    target.replaceChildren(proposalTable(filtered(), search.value));
  }
  renderTableOnly();
}

// Cut on a word boundary so a queue row never ends mid-word.
function excerpt(text, limit) {
  const value = String(text || "").trim();
  if (value.length <= limit) return value;
  const cut = value.slice(0, limit);
  const boundary = cut.lastIndexOf(" ");
  return `${(boundary > limit * 0.6 ? cut.slice(0, boundary) : cut).replace(/[\s,.;:—-]+$/, "")}…`;
}

function filtersActive() {
  return Boolean(state.filters.state || state.filters.risk || state.filters.q);
}

function filterQuery() {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(state.filters)) if (value) params.set(key, value);
  return params.toString();
}

// Filter state belongs in the address bar so a queue view can be linked and so
// Back from a proposal returns to the same filtered list. `replaceState` is
// deliberate: a history entry per keystroke would make Back walk through
// filters instead of leaving the screen.
function syncFilterUrl() {
  const query = filterQuery();
  const next = `#/proposals${query ? `?${query}` : ""}`;
  if (window.location.hash === next) return;
  window.history.replaceState(null, "", next);
}

async function updateFilters(patch) {
  const focusId = document.activeElement && document.activeElement.id;
  state.filters = { ...state.filters, ...patch };
  syncFilterUrl();
  await renderProposals();
  // The re-render replaces the control that had focus; put the keyboard user
  // back where they were, or on the status filter after a clear. A control that
  // became disabled (Clear filters, once nothing is left to clear) cannot take
  // focus, so it falls through rather than dropping the reader on <body>.
  const restored = focusId && document.getElementById(focusId);
  const fallback = document.getElementById("filter-state");
  (restored && !restored.disabled ? restored : fallback)?.focus();
}

function proposalTable(items, searchValue) {
  if (items.length === 0) {
    const filtered = Boolean(state.filters.state || state.filters.risk || searchValue);
    return el(
      "div",
      { class: "empty-state" },
      el("h2", { text: filtered ? "No proposals match these filters." : "No proposals need review." }),
      el("p", {
        class: "small muted",
        text: filtered
          ? "The filters are preserved in the address bar. Clear them to see the full queue."
          : "Autopilot creates a proposal when a webhook event arrives or when you run a workflow from the CLI.",
      }),
      el(
        "div",
        { class: "empty-actions" },
        filtered
          ? el(
              "button",
              {
                class: "button",
                type: "button",
                id: "empty-clear-filters",
                onclick: () => updateFilters({ state: "", risk: "", q: "" }),
              },
              "Clear filters",
            )
          : null,
        el("a", { class: "button", href: "#/settings" }, "Check workflow settings"),
      ),
    );
  }
  return el(
    "div",
    { class: "table-wrap" },
    el(
      "table",
      {},
      el("caption", { text: `${items.length} proposal${items.length === 1 ? "" : "s"}` }),
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          el("th", { scope: "col", text: "Type" }),
          el("th", { scope: "col", text: "Source" }),
          el("th", { scope: "col", text: "Risk" }),
          el("th", { scope: "col", text: "Status" }),
          el("th", { scope: "col", text: "Created" }),
          el("th", { scope: "col", text: "Next action" }),
        ),
      ),
      el(
        "tbody",
        {},
        ...items.map((item) =>
          el(
            "tr",
            {},
            el("td", { "data-label": "Type", class: "cell-nowrap", text: item.workflow.replaceAll("_", " ") }),
            el(
              "td",
              { "data-label": "Source" },
              el("span", { class: "mono", text: `#${item.subject_number ?? "—"}` }),
              el("div", { class: "small muted", text: excerpt(item.summary, 110) }),
            ),
            el("td", { "data-label": "Risk" }, riskBadge(item.risk)),
            el("td", { "data-label": "Status" }, statusBadge(item.state)),
            el("td", { "data-label": "Created", text: relativeAge(item.created_at) }),
            el(
              "td",
              { "data-label": "Next action", class: "cell-nowrap" },
              el(
                "a",
                { class: "row-button", href: `#/proposal/${encodeURIComponent(item.id)}` },
                item.state === "pending_approval"
                  ? "Inspect"
                  : item.state === "approved"
                    ? "Apply approved action"
                    : "Open record",
              ),
            ),
          ),
        ),
      ),
    ),
  );
}

// ------------------------------------------------------------------- detail
const RAIL_STEPS = ["received", "analyzed", "proposed", "decided", "applied"];

// `done` is how many steps are behind us; `current`/`failed` mark the step the
// proposal sits on. A finished proposal has no current step: marking "applied"
// as current for an executed proposal (or "decided" for a rejection) claims
// work is still in flight when the rail is actually closed.
const RAIL_PROGRESS = {
  pending_approval: { done: 3, current: 3 },
  approved: { done: 4, current: 4 },
  executing: { done: 4, current: 4 },
  executed: { done: 5 },
  action_failed: { done: 4, failed: 4 },
  rejected: { done: 4 },
  expired: { done: 4 },
};

function railFor(proposal) {
  const progress = RAIL_PROGRESS[proposal.state] || { done: 0 };
  return el(
    "ol",
    { class: "rail", "aria-label": "Decision rail" },
    ...RAIL_STEPS.map((step, position) => {
      let status = "";
      if (position < progress.done) status = "done";
      else if (position === progress.current) status = "current";
      else if (position === progress.failed) status = "failed";
      return el(
        "li",
        { class: "rail-step", dataset: { state: status } },
        el("span", { class: "rail-index", text: String(position + 1) }),
        el("span", { text: step }),
        el("span", { class: "visually-hidden", text: status ? `: ${status}` : ": pending" }),
      );
    }),
  );
}

async function renderProposalDetail(proposalId) {
  if (!proposalId) {
    window.location.hash = "#/proposals";
    return;
  }
  const data = await api(`/api/proposals/${encodeURIComponent(proposalId)}`);
  const proposal = data.proposal;
  state.proposal = proposal;
  const context = proposal.input_context || {};
  const payload = proposal.payload || {};
  const comment = proposal.action_payload?.comment;
  const labels = proposal.action_payload?.labels || [];
  const canDecide = proposal.state === "pending_approval";
  const canExecute = proposal.state === "approved" || proposal.state === "action_failed";

  els.main.replaceChildren(
    el(
      "div",
      { class: "stack" },
      el(
        "div",
        { class: "stack" },
        el(
          "div",
          { class: "section-title" },
          el("h1", { text: `${proposal.workflow.replaceAll("_", " ")} · #${proposal.subject_number ?? "—"}` }),
          statusBadge(proposal.state),
          riskBadge(proposal.risk),
          badge(`confidence ${(proposal.confidence ?? 0).toFixed(2)}`, "neutral"),
        ),
        railFor(proposal),
      ),

      el(
        "div",
        { class: "grid-two" },
        el(
          "div",
          { class: "stack" },
          el(
            "section",
            { class: "panel" },
            el("div", { class: "panel-head" }, el("h2", { text: "What the model proposed" })),
            el("p", { text: payload.summary || "No summary was stored." }),
            payload.labels?.length
              ? el(
                  "p",
                  { class: "small muted" },
                  `Suggested labels: ${payload.labels.join(", ")}`,
                )
              : null,
            payload.review_notes?.length
              ? el(
                  "div",
                  { class: "stack" },
                  el("h3", { text: "Review checks" }),
                  el("ul", {}, ...payload.review_notes.map((note) => el("li", { text: note }))),
                )
              : null,
            payload.missing_reproduction_questions?.length
              ? el(
                  "div",
                  { class: "stack" },
                  el("h3", { text: "Still needed from the reporter" }),
                  el(
                    "ul",
                    {},
                    ...payload.missing_reproduction_questions.map((question) =>
                      el("li", { text: question }),
                    ),
                  ),
                )
              : null,
            payload.sections?.length
              ? el(
                  "div",
                  { class: "stack" },
                  ...payload.sections.map((section) =>
                    el(
                      "div",
                      { class: "stack" },
                      el("h3", { text: section.heading }),
                      el("ul", {}, ...section.items.map((item) => el("li", { text: item }))),
                    ),
                  ),
                )
              : null,
          ),

          el(
            "section",
            { class: "panel" },
            el("div", { class: "panel-head" }, el("h2", { text: "Source evidence" })),
            el(
              "div",
              { class: "stack" },
              el(
                "div",
                { class: "citation" },
                el("span", { class: "small muted", text: "Input snapshot" }),
                el("span", { class: "mono", text: `sha256 ${proposal.input_sha256}` }),
              ),
              context.title
                ? el(
                    "div",
                    { class: "citation" },
                    el("span", { class: "small muted", text: `#${context.number} · ${context.author || "unknown author"}` }),
                    el("strong", { text: context.title }),
                    el("pre", { class: "excerpt", text: context.body || "(no body)" }),
                  )
                : null,
              proposal.citations?.length
                ? el(
                    "div",
                    { class: "citation-refs" },
                    el("span", { class: "small muted", text: "Cited records" }),
                    el(
                      "ul",
                      { class: "citation-list" },
                      ...proposal.citations.map((citation) =>
                        el(
                          "li",
                          {},
                          el("span", { class: "mono", text: citation.label }),
                          el("span", {
                            class: "small muted",
                            text: citation.kind.replaceAll("_", " "),
                          }),
                        ),
                      ),
                    ),
                  )
                : null,
            ),
          ),

          el(
            "section",
            { class: "panel" },
            el("div", { class: "panel-head" }, el("h2", { text: "Will change" })),
            proposal.action_type === "comment"
              ? el(
                  "div",
                  { class: "change-block" },
                  el("span", { class: "change-label", text: `public comment on #${proposal.subject_number}` }),
                  el("pre", { class: "excerpt", text: comment || "(empty)" }),
                )
              : proposal.action_type === "label"
                ? el(
                    "div",
                    { class: "change-block" },
                    el("span", { class: "change-label", text: "labels applied to the issue" }),
                    el("p", { text: labels.join(", ") || "(no labels)" }),
                  )
                : el(
                    "div",
                    { class: "change-block" },
                    el("span", { class: "change-label", text: "no repository change" }),
                    el("p", {
                      class: "small",
                      text: "This proposal is informational. Approving it records the decision but does not contact the forge.",
                    }),
                  ),
            proposal.last_error
              ? el("p", { class: "small", style: null, text: `Last error: ${proposal.last_error}` })
              : null,
          ),

        ),

        el(
          "div",
          { class: "stack" },
          el(
            "section",
            { class: "panel" },
            el("h2", { text: "Decision record" }),
            el(
              "dl",
              { class: "fact-list" },
              el("dt", { text: "Repository" }),
              el("dd", { class: "mono", text: proposal.repository_id }),
              el("dt", { text: "Created" }),
              el("dd", { text: formatTime(proposal.created_at) }),
              el("dt", { text: "Expires" }),
              el("dd", { text: formatTime(proposal.expires_at) }),
              el("dt", { text: "Approved by" }),
              el("dd", { text: proposal.approved_by || "—" }),
              el("dt", { text: "Rejected by" }),
              el("dd", { text: proposal.rejected_by || "—" }),
              el("dt", { text: "Executed" }),
              el("dd", { text: proposal.executed_at ? formatTime(proposal.executed_at) : "—" }),
              el("dt", { text: "Attempts" }),
              el("dd", { text: String(proposal.attempt_count) }),
              el("dt", { text: "Cost" }),
              el("dd", { text: `$${Number(proposal.cost_usd || 0).toFixed(6)}` }),
            ),
          ),
          el(
            "section",
            { class: "panel" },
            el(
              "details",
              { class: "stack" },
              el("summary", { text: "Model metadata and hashes" }),
              el(
                "dl",
                { class: "fact-list" },
                el("dt", { text: "Provider" }),
                el("dd", { text: proposal.provider || "—" }),
                el("dt", { text: "Model" }),
                el("dd", { text: proposal.model || "—" }),
                el("dt", { text: "Request id" }),
                el("dd", { class: "mono", text: proposal.model_request_id || "—" }),
                el("dt", { text: "Prompt version" }),
                el("dd", { class: "mono", text: proposal.prompt_version || "—" }),
                el("dt", { text: "Proposal id" }),
                el("dd", { class: "mono", text: proposal.id }),
              ),
            ),
          ),
        ),
      ),

      el(
        "section",
        { class: "panel action-bar", "aria-label": "Proposal decisions" },
        el(
          "div",
          { class: "action-bar-context" },
          el("span", { class: "mono", text: `#${proposal.subject_number ?? "—"}` }),
          statusBadge(proposal.state),
          el("span", {
            class: "small muted",
            text: canDecide
              ? `expires ${formatTime(proposal.expires_at)}`
              : "this proposal is closed to new decisions",
          }),
        ),
        el(
          "div",
          { class: "action-bar-buttons" },
          el(
            "button",
            {
              class: "button button-accent",
              type: "button",
              disabled: !canDecide,
              onclick: () => confirmDecision("approve"),
            },
            "Approve",
          ),
          el(
            "button",
            {
              class: "button",
              type: "button",
              disabled: !canDecide,
              onclick: () => confirmDecision("reject"),
            },
            "Reject",
          ),
          el(
            "button",
            {
              id: "apply-action",
              class: "button",
              type: "button",
              disabled: !canExecute,
              onclick: () => executeProposal(),
            },
            "Apply approved action",
          ),
          el("a", { class: "button button-quiet", href: "#/proposals" }, "Return to queue"),
        ),
      ),
    ),
  );
}

function confirmDecision(kind) {
  const proposal = state.proposal;
  if (!proposal) return;
  const isApprove = kind === "approve";
  els.dialogTitle.textContent = isApprove ? "Approve this proposal?" : "Reject this proposal?";
  els.dialogSummary.textContent = isApprove
    ? "Approval records your identity and unlocks the apply step. The repository is not touched until you apply the action."
    : "Rejection is recorded in the audit trail and the proposal cannot be applied.";
  els.dialogFacts.replaceChildren(
    fact("Target", `#${proposal.subject_number ?? "—"} in ${proposal.repository_id}`),
    fact("Action", proposal.action_type),
    fact("Actor", state.actor || "not selected"),
    fact("Expires", formatTime(proposal.expires_at)),
  );
  els.dialogConfirm.textContent = isApprove ? "Approve" : "Reject";
  els.dialogConfirm.className = isApprove ? "button button-accent" : "button button-danger";
  els.dialog.returnValue = "";
  els.dialog.showModal();

  els.dialog.onclose = async () => {
    if (els.dialog.returnValue !== "confirm") return;
    els.dialogConfirm.disabled = true;
    try {
      const data = await api(
        `/api/proposals/${encodeURIComponent(proposal.id)}/${isApprove ? "approve" : "reject"}`,
        { method: "POST", body: isApprove ? {} : { reason: "rejected from dashboard" } },
      );
      setStatus(
        isApprove
          ? "Approved. Apply the action when you are ready."
          : "Rejected and recorded in the audit trail.",
        "success",
      );
      await Promise.all([renderProposalDetail(data.proposal.id), refreshRepositoryCounts()]);
      focusNextAction();
    } catch (error) {
      setStatus(error.message, "error");
      els.dialogConfirm.disabled = false;
    } finally {
      els.dialogConfirm.disabled = false;
    }
  };
}

async function executeProposal() {
  const proposal = state.proposal;
  if (!proposal) return;
  setStatus("Applying the approved action…");
  try {
    const data = await api(`/api/proposals/${encodeURIComponent(proposal.id)}/execute`, {
      method: "POST",
      body: {},
    });
    if (data.proposal.state === "executed") {
      setStatus("Applied. The audit trail records the actor and attempt.", "success");
    } else {
      setStatus(`Apply failed: ${data.proposal.last_error || "unknown error"}`, "error");
    }
    await Promise.all([renderProposalDetail(data.proposal.id), refreshRepositoryCounts()]);
    focusNextAction();
  } catch (error) {
    setStatus(error.message, "error");
  }
}

function fact(label, value) {
  return el("div", { class: "stack" }, el("dt", { text: label }), el("dd", { text: value }));
}

// -------------------------------------------------------------------- audit
async function renderAudit() {
  const repo = requireRepo();
  if (!repo) return;
  const data = await api(`/api/audit?repository_id=${encodeURIComponent(repo.id)}&limit=300`);
  const verify = await api("/api/audit/verify");
  const events = data.events || [];

  els.main.replaceChildren(
    el(
      "div",
      { class: "stack" },
      el(
        "div",
        { class: "section-title" },
        el("h1", { text: "Audit trail" }),
        verify.ok
          ? badge(`chain verified · ${verify.checked} events`, "success")
          : badge(`chain broken at ${verify.first_broken_id}`, "danger"),
      ),
      el("p", {
        class: "panel-note",
        text:
          "Events are append-only: the database rejects UPDATE and DELETE. Each row hashes the previous row, so removal or reordering is detectable. Hashes are shortened here; GET /api/audit returns them in full.",
      }),
      events.length === 0
        ? el("div", { class: "empty-state" }, el("p", { text: "No audit events for this repository yet." }))
        : el(
            "div",
            { class: "table-wrap" },
            el(
              "table",
              {},
              el("caption", { text: `${events.length} events, newest first` }),
              el(
                "thead",
                {},
                el(
                  "tr",
                  {},
                  el("th", { scope: "col", text: "Seq" }),
                  el("th", { scope: "col", text: "When" }),
                  el("th", { scope: "col", text: "Event" }),
                  el("th", { scope: "col", text: "Actor" }),
                  el("th", { scope: "col", text: "Subject" }),
                  el("th", { scope: "col", text: "Hash" }),
                ),
              ),
              el(
                "tbody",
                {},
                ...events.map((event) =>
                  el(
                    "tr",
                    {},
                    el("td", { "data-label": "Seq", class: "mono", text: String(event.seq) }),
                    el("td", { "data-label": "When", text: formatTime(event.created_at) }),
                    el("td", { "data-label": "Event", text: event.event_type }),
                    el("td", { "data-label": "Actor", text: event.actor_login || "system" }),
                    el("td", { "data-label": "Subject", class: "mono", text: event.subject_id || "—" }),
                    el("td", {
                      "data-label": "Hash",
                      class: "mono",
                      // Truncated for the grid; the full digest stays available to
                      // pointer users and to anyone reading the JSON API.
                      title: event.hash,
                      text: `${event.hash.slice(0, 16)}…`,
                    }),
                  ),
                ),
              ),
            ),
          ),
    ),
  );
}

// ----------------------------------------------------------------- settings
async function renderSettings() {
  const repo = requireRepo();
  if (!repo) return;
  const [data, system] = await Promise.all([
    api(`/api/repositories/${encodeURIComponent(repo.id)}/settings`),
    api("/api/system"),
  ]);
  const settings = data.settings;
  state.settings = settings;
  state.system = system;

  const workflowRows = Object.keys(settings.workflows).map((name) =>
    el(
      "label",
      { class: "checkbox-row" },
      el("input", {
        type: "checkbox",
        name: "workflow",
        value: name,
        checked: Boolean(settings.workflows[name]),
      }),
      el("span", { text: name.replaceAll("_", " ") }),
    ),
  );

  const requestLimit = el("input", {
    class: "input",
    id: "daily-request-limit",
    type: "number",
    min: "1",
    max: "10000",
    value: String(settings.daily_request_limit),
  });
  const costLimit = el("input", {
    class: "input",
    id: "daily-cost-limit",
    type: "number",
    min: "0",
    step: "0.01",
    value: String(settings.daily_cost_limit_usd),
  });
  const ttl = el("input", {
    class: "input",
    id: "proposal-ttl",
    type: "number",
    min: "1",
    max: "10080",
    value: String(settings.proposal_ttl_minutes),
  });
  const taxonomy = el("input", {
    class: "input",
    id: "label-taxonomy",
    type: "text",
    value: settings.label_taxonomy.join(", "),
    "aria-describedby": "label-taxonomy-help",
  });

  const deleteConfirm = el("input", {
    class: "input",
    id: "delete-confirm",
    type: "text",
    autocomplete: "off",
    "aria-describedby": "delete-help",
  });
  deleteConfirm.addEventListener("input", () => {
    const button = els.main.querySelector(".danger-zone .button-danger");
    if (button) button.disabled = deleteConfirm.value.trim() !== repo.full_name;
  });

  els.main.replaceChildren(
    el(
      "div",
      { class: "stack" },
      el("div", { class: "section-title" }, el("h1", { text: "Settings" }), badge(repo.id, "neutral", true)),
      el("p", {
        class: "panel-note",
        text: "These controls bound what the automation may do. Changes are audited with your actor name.",
      }),
      el(
        "section",
        { class: "panel stack" },
        el("h2", { text: "Workflows" }),
        ...workflowRows,
        el("p", {
          class: "panel-note",
          text: "Disabling a workflow stops new proposals for it. Existing proposals remain reviewable.",
        }),
      ),
      el(
        "section",
        { class: "panel stack" },
        el("h2", { text: "Budgets and lifetime" }),
        el(
          "div",
          { class: "settings-grid" },
          el(
            "label",
            { class: "field", for: "daily-request-limit" },
            el("span", { class: "field-label", text: "Daily request limit" }),
            requestLimit,
          ),
          el(
            "label",
            { class: "field", for: "daily-cost-limit" },
            el("span", { class: "field-label", text: "Daily spend cap (USD, 0 = none)" }),
            costLimit,
          ),
          el(
            "label",
            { class: "field", for: "proposal-ttl" },
            el("span", { class: "field-label", text: "Proposal TTL (minutes)" }),
            ttl,
          ),
        ),
      ),
      el(
        "section",
        { class: "panel stack" },
        el("h2", { text: "Label taxonomy" }),
        el(
          "label",
          { class: "field", for: "label-taxonomy" },
          el("span", { class: "field-label", text: "Allowed labels" }),
          taxonomy,
        ),
        el("p", {
          class: "panel-note",
          id: "label-taxonomy-help",
          text: "Comma separated. The model may only suggest labels from this list; anything else fails validation.",
        }),
      ),
      el(
        "section",
        { class: "panel stack" },
        el("h2", { text: "Provider mode" }),
        el(
          "dl",
          { class: "fact-list" },
          el("dt", { text: "Active provider" }),
          el("dd", { text: system.provider }),
          el("dt", { text: "Live model calls" }),
          el(
            "dd",
            {
              text: system.live_provider_enabled
                ? `enabled · model ${system.model} · every call is metered`
                : "off · the deterministic fake provider answers locally and makes no network call",
            },
          ),
          el("dt", { text: "How to change it" }),
          el("dd", {
            text: "Set AUTOPILOT_PROVIDER and AUTOPILOT_ALLOW_LIVE_PROVIDER, then restart. A live provider also refuses to start without a base URL, a key, and both cost caps.",
          }),
        ),
        el("p", {
          class: "panel-note",
          text: "The provider switch is a process setting, not a stored toggle, so it cannot be flipped from this page by mistake.",
        }),
      ),
      el(
        "section",
        { class: "panel stack" },
        el("h2", { text: "Retention and deletion" }),
        el(
          "dl",
          { class: "fact-list" },
          el("dt", { text: "Content retention" }),
          el("dd", {
            text: `${system.retention_days} days for proposals, model calls, and deliveries; run \`autopilot purge\` to apply it.`,
          }),
          el("dt", { text: "Audit events" }),
          el("dd", { text: "Kept permanently, with content hashes only after a purge or delete." }),
          el("dt", { text: "Webhook intake" }),
          el("dd", {
            text: system.webhook_secret_configured
              ? `signed, bodies up to ${system.max_body_bytes.toLocaleString()} bytes`
              : "not configured; the server refuses to start without a secret",
          }),
          el("dt", { text: "Remote access" }),
          el("dd", {
            text: system.remote_access
              ? "enabled with an admin token and an explicit actor header"
              : "disabled; only loopback requests are served",
          }),
        ),
      ),
      el(
        "section",
        { class: "panel stack" },
        el("h2", { text: "Permissions" }),
        el(
          "dl",
          { class: "fact-list" },
          el("dt", { text: "Owners" }),
          el(
            "dd",
            {
              text:
                state.actors
                  .filter((actor) => actor.role === "owner")
                  .map((actor) => actor.actor_login)
                  .join(", ") || "none",
            },
          ),
          el("dt", { text: "Maintainers" }),
          el(
            "dd",
            {
              text:
                state.actors
                  .filter((actor) => actor.role === "maintainer")
                  .map((actor) => actor.actor_login)
                  .join(", ") || "none",
            },
          ),
        ),
      ),
      el(
        "div",
        { class: "actions-inline" },
        el(
          "button",
          { class: "button button-accent", type: "button", onclick: () => saveSettings() },
          "Save settings",
        ),
        el("a", { class: "button button-quiet", href: "#/overview" }, "Back to overview"),
      ),
      el(
        "section",
        { class: "panel stack danger-zone" },
        el("h2", { text: "Delete stored content" }),
        el("p", {
          class: "panel-note",
          id: "delete-help",
          text: "Removes this repository's issues, pull requests, proposals, deliveries, and model-call rows. The append-only audit events stay, with hashes and no repository text. This cannot be undone.",
        }),
        el(
          "label",
          { class: "field", for: "delete-confirm" },
          el("span", {
            class: "field-label",
            text: `Type ${repo.full_name} to enable deletion`,
          }),
          deleteConfirm,
        ),
        el(
          "button",
          {
            class: "button button-danger",
            type: "button",
            disabled: true,
            onclick: () => deleteRepository(),
          },
          "Delete stored content",
        ),
      ),
    ),
  );

  async function deleteRepository() {
    const button = els.main.querySelector(".danger-zone .button-danger");
    button.disabled = true;
    button.textContent = "Deleting…";
    try {
      const result = await api(`/api/repositories/${encodeURIComponent(repo.id)}`, {
        method: "DELETE",
        body: { confirm: true },
      });
      const counts = Object.entries(result.deleted)
        .map(([name, count]) => `${count} ${name}`)
        .join(", ");
      setStatus(`Deleted ${counts}. The audit trail is intact.`, "success");
      localStorage.removeItem("autopilot.repository");
      await bootstrap();
    } catch (error) {
      setStatus(error.message, "error");
      button.disabled = false;
      button.textContent = "Delete stored content";
    }
  }

  async function saveSettings() {
    const workflows = {};
    document.querySelectorAll('input[name="workflow"]').forEach((input) => {
      workflows[input.value] = input.checked;
    });
    try {
      await api(`/api/repositories/${encodeURIComponent(repo.id)}/settings`, {
        method: "PUT",
        body: {
          workflows,
          label_taxonomy: taxonomy.value
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean),
          daily_request_limit: Number(requestLimit.value),
          daily_cost_limit_usd: Number(costLimit.value),
          proposal_ttl_minutes: Number(ttl.value),
        },
      });
      setStatus("Settings saved and audited.", "success");
      await renderSettings();
    } catch (error) {
      setStatus(error.message, "error");
    }
  }
}

wireSkipLink();
wireDialogFocusTrap();
bootstrap();
