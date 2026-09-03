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

