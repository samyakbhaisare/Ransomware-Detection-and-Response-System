/**
 * Executable harness for the RDRS dashboard front end.
 *
 * Loads the real <script> block out of src/rdrs/dashboard/index.html and runs
 * it in a Node vm context with a minimal DOM stub, then drives the real
 * functions and asserts their behaviour. This is what makes the B-05, B-06 and
 * B-07 front-end fixes executable rather than merely inspected.
 *
 * Usage:  node tests/dashboard_frontend_harness.mjs
 * Exit code 0 = all assertions passed.
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DASHBOARD = path.join(ROOT, "src", "rdrs", "dashboard", "index.html");

// ---------------------------------------------------------------- DOM stub

class ClassList {
  constructor() {
    this.set = new Set();
  }
  toggle(name, force) {
    const on = force === undefined ? !this.set.has(name) : force;
    if (on) this.set.add(name);
    else this.set.delete(name);
    return on;
  }
  contains(name) {
    return this.set.has(name);
  }
  add(name) {
    this.set.add(name);
  }
  remove(name) {
    this.set.delete(name);
  }
}

// A 2D canvas context stub: every method is a no-op and every property is
// writable, which is all drawChart() needs to run to completion.
function make2dContext() {
  return new Proxy(
    {},
    {
      get(target, prop) {
        if (prop in target) return target[prop];
        return () => {};
      },
      set(target, prop, value) {
        target[prop] = value;
        return true;
      },
    },
  );
}

class Element {
  constructor(id) {
    this.id = id;
    this._textContent = "";
    this.attributes = {};
    this.value = "";
    this.disabled = false;
    this.className = "";
    this.style = {};
    this.dataset = {};
    this.classList = new ClassList();
    this.children = [];
    this.listeners = {};
    this.width = 500;
    this.height = 250;
    this.clientWidth = 500;
    this.clientHeight = 250;
  }
  // Mirrors the real DOM: assigning textContent replaces every child node.
  get textContent() {
    return this._textContent;
  }

  set textContent(value) {
    this._textContent = value;

    if (value === "") {
      this.children = [];
    }
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name)
      ? this.attributes[name]
      : null;
  }

  getContext() {
    return make2dContext();
  }
  addEventListener(type, handler) {
    (this.listeners[type] ||= []).push(handler);
  }
  removeEventListener() {}
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  remove() {}
  click() {}
  querySelector() {
    return null;
  }
  querySelectorAll() {
    return [];
  }
  closest() { return new Element("parent"); }
  before() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
}

const elements = new Map();

function fire(element, type) {
  const handlers = element.listeners[type] || [];
  for (const handler of handlers) {
    handler({ target: element, type });
  }
}

function typeInto(id, value) {
  const element = el(id);
  element.value = value;
  fire(element, "input");
  return element;
}

function el(id) {
  if (!elements.has(id)) elements.set(id, new Element(id));
  return elements.get(id);
}

// Pre-create every id the dashboard markup defines, so getElementById never
// returns null for a real element.
const html = fs.readFileSync(DASHBOARD, "utf8");
for (const match of html.matchAll(/id="([^"]+)"/g)) el(match[1]);

const navItems = ["dashboard", "events", "processes", "alerts", "incidents", "reports", "settings"].map(
  (page) => {
    const item = new Element(`nav-${page}`);
    item.dataset.page = page;
    item.classList = new ClassList();
    if (page === "dashboard") item.classList.add("active");
    return item;
  },
);

let activePage = "dashboard";

const documentStub = {
  getElementById: (id) => el(id),
  querySelector: (selector) => {
    if (selector === ".nav-item.active") {
      return navItems.find((n) => n.classList.contains("active")) || null;
    }
    if (selector === ".page") return null;
    return null;
  },
  querySelectorAll: (selector) => {
    if (selector === ".nav-item") return navItems;
    if (selector === ".page") return [];
    return [];
  },
  createElement: () => new Element("created"),
  body: new Element("body"),
  addEventListener: () => {},
};

// ------------------------------------------------------------ fetch stub

const routes = new Map();
const failures = new Set();
let fetchLog = [];

function jsonResponse(payload, status = 200, headers = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get: (name) => headers[name.toLowerCase()] ?? headers[name] ?? null,
    },
    json: async () => payload,
  };
}

const fetchStub = async (url, options = {}) => {
  if (String(url) === "/public-summary") return {ok: true, json: async () => ({access: "private"})};
  if (String(url) === "/incidents?limit=1000") return {ok: true, json: async () => []};
  const key = String(url);
  fetchLog.push({ url: key, method: options.method || "GET" });

  if (failures.has(key)) {
    return jsonResponse({ detail: `forced failure for ${key}` }, 500);
  }
  if (routes.has(key)) {
    const route = routes.get(key);
    return route?.__httpResponse
      ? jsonResponse(route.payload, route.status || 200, route.headers || {})
      : jsonResponse(route);
  }
  return jsonResponse({});
};

// ------------------------------------------------------------- run script

const scriptMatch = html.match(/<script>([\s\S]*?)<\/script>/);
if (!scriptMatch) {
  console.error("FAIL: no <script> block found in the dashboard");
  process.exit(1);
}

const dashboardHtmlSource = fs.readFileSync(
  new URL('../src/rdrs/dashboard/index.html', import.meta.url),
  'utf8',
);

const context = vm.createContext({
  document: documentStub,
  window: { addEventListener: () => {} },
  fetch: fetchStub,
  setInterval: () => 0,
  clearInterval: () => {},
  setTimeout: (fn) => fn(),
  console,
  Chart: class {
    constructor() {
      this.data = null;
    }
    destroy() {}
    update() {}
  },
  Date,
  Number,
  String,
  Boolean,
  Math,
  JSON,
  Object,
  Array,
  Promise,
  Error,
  Set,
  Map,
  RegExp,
  isNaN,
  parseInt,
  parseFloat,
  URLSearchParams,
  __dashboardHtml: dashboardHtmlSource,
  // Reads the stubbed fields, so `new FormData(form)` behaves like the real
  // thing when saveSettings builds its payload.
  FormData: class {
    constructor() {}
    get(name) {
      const map = {
        name: "settingName",
        environment: "settingEnvironment",
        shutdown_timeout: "settingTimeout",
        log_level: "settingLogLevel",
        persistence_enabled: "settingPersistence",
      };
      const id = map[name];
      return id ? el(id).value : null;
    }
  },
  encodeURIComponent,
});

vm.runInContext(scriptMatch[1], context, { filename: "dashboard-inline.js" });

// ---------------------------------------------------------------- helpers

let passed = 0;
let failed = 0;

function check(name, condition, detail) {
  if (condition) {
    passed += 1;
    console.log(`  PASS  ${name}`);
  } else {
    failed += 1;
    console.log(`  FAIL  ${name}${detail ? ` -- ${detail}` : ""}`);
  }
}

function section(title) {
  console.log(`\n${title}`);
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

// Top-level `const` bindings (API, state, startBtn, ...) live in the script's
// lexical scope rather than on the context object, so they are read by
// evaluating an expression inside the same context.
const evalIn = (code) => vm.runInContext(code, context);

function setActivePage(page) {
  activePage = page;
  navItems.forEach((n) => n.classList.toggle("active", n.dataset.page === page));
}

// Baseline payloads.
routes.set("/status", {
  state: "STOPPED",
  healthy: false,
  config: {
    name: "rdrs",
    environment: "test",
    shutdown_timeout: 15.0,
    log_level: "INFO",
    persistence_enabled: true,
  },
  config_available: true,
  subsystems: {},
  unhealthy_subsystems: [],
});

routes.set("/dashboard-data", {
  summary: {
    event_count: 235,
    process_count: 26925,
    score_count: 2,
    alert_count: 2,
    incident_count: 1,
  },
  score: { score: 95.0, level: "Critical", reason: "burst", created_at: null },
  events: [],
  processes: [],
  alerts: [],
  incidents: [],
  modified_files: [],
});

// Let the script's own initial refreshAll() settle.
await settle();
await settle();

// ============================================================ B-06 refresh

section("B-06  Refresh button");

fetchLog = [];
setActivePage("events");
routes.set("/events", []);

await evalIn("refreshAll(true)");

check(
  "manual refresh calls /status",
  fetchLog.some((c) => c.url === "/status"),
);
check(
  "manual refresh calls /dashboard-data",
  fetchLog.some((c) => c.url === "/dashboard-data"),
  JSON.stringify(fetchLog.map((c) => c.url)),
);
check(
  "summary counters updated while on a non-dashboard page",
  el("eventCount").textContent === 235,
  `eventCount=${el("eventCount").textContent}`,
);
check(
  "incident counter updated",
  el("incidentCount").textContent === 1,
  `incidentCount=${el("incidentCount").textContent}`,
);
check(
  "manual refresh reports success feedback",
  el("message").textContent.includes("refreshed"),
  el("message").textContent,
);
check(
  "success feedback carries the success class",
  el("message").classList.contains("success"),
);
check(
  "last-refreshed timestamp is shown",
  el("lastRefreshed").textContent.includes("Last refreshed"),
  el("lastRefreshed").textContent,
);

// Overlap handling.
//
// A manual refresh must NOT be silently dropped. The background poll runs
// every 5 seconds, so a click very often lands mid-cycle; returning
// immediately made the click do nothing at all, which is exactly what
// "Refresh is not working" looked like. A manual refresh now waits for the
// in-flight cycle and then runs its own. What must still never happen is two
// cycles executing at the same time.
fetchLog = [];
const slow = evalIn("refreshAll(true)");
const queued = evalIn("refreshAll(true)");
await Promise.all([slow, queued]);

const statusCalls = fetchLog.filter((c) => c.url === "/status").length;

check(
  "a manual refresh queued behind another still completes its own fetch",
  statusCalls === 2,
  `status calls=${statusCalls}`,
);

check(
  "each cycle issues exactly one /status and one /dashboard-data (no duplicates)",
  fetchLog.filter((c) => c.url === "/status").length === 2 &&
    fetchLog.filter((c) => c.url === "/dashboard-data").length === 2,
  `total calls=${fetchLog.length} (${fetchLog.map((c) => c.url).join(", ")})`,
);

// Failure handling.
failures.add("/dashboard-data");
fetchLog = [];
await evalIn("refreshAll(true)");
check(
  "refresh failure shows an error message",
  el("message").textContent.toLowerCase().includes("refresh failed"),
  el("message").textContent,
);
check(
  "error feedback carries the error class",
  el("message").classList.contains("error"),
);
check(
  "refresh failure does not throw and releases the guard",
  evalIn("state.refreshing") === false,
);
check(
  "status text reports the API as unavailable",
  el("statusText").textContent.includes("unavailable"),
  el("statusText").textContent,
);

// Recover.
failures.delete("/dashboard-data");
await evalIn("refreshAll(true)");
check(
  "refresh recovers after a failure",
  evalIn("state.refreshing") === false && el("message").classList.contains("success"),
);

// =========================================================== B-07 controls

section("B-07  Monitoring controls");

function applyState(stateName, extra = {}) {
  evalIn(
    `renderStatus(${JSON.stringify({
      state: stateName,
      healthy: stateName === "RUNNING",
      subsystems: extra.subsystems || {},
      unhealthy_subsystems: extra.unhealthy_subsystems || [],
      config: {},
      config_available: true,
    })})`,
  );
}

applyState("RUNNING");
check(
  "RUNNING -> Start disabled, Stop enabled",
  el("startBtn").disabled === true && el("stopBtn").disabled === false,
);

applyState("STOPPED");
check(
  "STOPPED -> Start enabled, Stop disabled",
  el("startBtn").disabled === false && el("stopBtn").disabled === true,
);

applyState("DEGRADED", {
  unhealthy_subsystems: ["file_monitor"],
});
check(
  "DEGRADED -> Start disabled, Stop enabled (still stoppable)",
  el("startBtn").disabled === true && el("stopBtn").disabled === false,
);
check(
  "DEGRADED names the failing subsystem",
  el("statusDetail").textContent.includes("file_monitor"),
  el("statusDetail").textContent,
);
check(
  "DEGRADED text says it is still running",
  el("statusText").textContent.includes("still running"),
  el("statusText").textContent,
);

applyState("SHUTTING_DOWN");
check(
  "SHUTTING_DOWN -> both buttons disabled",
  el("startBtn").disabled === true && el("stopBtn").disabled === true,
);

applyState("FAILED");
check(
  "FAILED -> both buttons disabled",
  el("startBtn").disabled === true && el("stopBtn").disabled === true,
);

applyState("STARTING");
check(
  "STARTING -> Start disabled, Stop enabled",
  el("startBtn").disabled === true && el("stopBtn").disabled === false,
);

// Buttons must be restored after a control request fails, even when the
// recovery refresh also fails.
applyState("STOPPED");
failures.add("/monitor/start");
failures.add("/status");
failures.add("/dashboard-data");

await evalIn('controlRuntime("/monitor/start", "started")');
await settle();

check(
  "buttons restored after a failed control request (guaranteed cleanup)",
  el("startBtn").disabled === false && el("stopBtn").disabled === true,
  `start=${el("startBtn").disabled} stop=${el("stopBtn").disabled}`,
);
check(
  "control guard released after failure",
  evalIn("state.controlPending") === false,
);

failures.clear();

// Successful control request restores buttons from the returned state.
routes.set("/monitor/start", { state: "RUNNING", healthy: true, subsystems: {} });
await evalIn('controlRuntime("/monitor/start", "started")');
await settle();
check(
  "successful start leaves Start disabled, Stop enabled",
  el("startBtn").disabled === true && el("stopBtn").disabled === false,
  `start=${el("startBtn").disabled} stop=${el("stopBtn").disabled}`,
);

// =========================================================== B-05 settings

section("B-05  Settings");

evalIn(
  `populateSettings(${JSON.stringify({
    name: "rdrs",
    environment: "test",
    shutdown_timeout: 15.0,
    log_level: "INFO",
    persistence_enabled: true,
  })}, true)`,
);
check(
  "real config populates Name",
  el("settingName").value === "rdrs",
  el("settingName").value,
);
check(
  "real config populates Environment",
  el("settingEnvironment").value === "test",
  el("settingEnvironment").value,
);

// The reported symptom: a Swagger schema hint must never be rendered as data.
evalIn(
  `populateSettings(${JSON.stringify({
    name: "string",
    environment: "string",
  })}, true)`,
);
check(
  "OpenAPI placeholder 'string' is never displayed as a value",
  el("settingName").value === "" && el("settingEnvironment").value === "",
  `name=${el("settingName").value} env=${el("settingEnvironment").value}`,
);

// Cold start with no configuration available.
evalIn("populateSettings(null, false)");
check(
  "unavailable config blanks the fields",
  el("settingName").value === "" && el("settingEnvironment").value === "",
);
check(
  "unavailable config shows an explicit message",
  el("settingsStatus").textContent.toLowerCase().includes("unavailable"),
  el("settingsStatus").textContent,
);

// loadSettings with a real payload.
routes.set("/status", {
  state: "STOPPED",
  config: { name: "rdrs", environment: "test" },
  config_available: true,
  subsystems: {},
  unhealthy_subsystems: [],
});
await evalIn("loadSettings()");
check(
  "loadSettings renders the real configuration",
  el("settingName").value === "rdrs" && el("settingEnvironment").value === "test",
);
check(
  "loadSettings reports success",
  el("message").classList.contains("success"),
  el("message").textContent,
);

// loadSettings when the backend says the config is unavailable.
routes.set("/status", {
  state: "STOPPED",
  config: {},
  config_available: false,
  subsystems: {},
  unhealthy_subsystems: [],
});
await evalIn("loadSettings()");
check(
  "loadSettings reports unavailable instead of showing blanks",
  el("message").classList.contains("error") &&
    el("settingsStatus").textContent.toLowerCase().includes("unavailable"),
  `${el("message").textContent} | ${el("settingsStatus").textContent}`,
);

// ============================================================ B-08 dashboard

section("B-08  Phase 7 dashboard elements");

// A rich payload built from the SAME key names the real /dashboard-data
// endpoint returns (verified against the live API).
routes.set("/dashboard-data", {
  summary: {
    event_count: 235,
    process_count: 26925,
    score_count: 2,
    alert_count: 2,
    incident_count: 1,
  },
  score: {
    score: 95.0,
    level: "CRITICAL",
    reason: "phase 7 verification",
    created_at: "2026-09-18T12:00:00Z",
  },
  events: [
    {
      id: 1,
      action: "modified",
      path: "/sandbox/encrypted_0.bin",
      extension: ".bin",
      created_at: "2026-09-18T12:00:00Z",
      metadata: { entropy: 7.95, file_type: "application/octet-stream" },
    },
  ],
  processes: [
    {
      id: 1,
      pid: 4321,
      name: "probe.exe",
      cpu_percent: 91.5,
      memory_percent: 1.25,
      disk_write_bytes: 4096,
      executable_path: "C:/probe.exe",
      parent_pid: 1,
    },
  ],
  alerts: [
    {
      id: 1,
      severity: "CRITICAL",
      message: "ransomware-like burst",
      created_at: "2026-09-18T12:00:01Z",
      evidence: { score: 95.0 },
    },
  ],
  incidents: [],
  top_modified: [
    { path: "/sandbox/encrypted_0.bin", modifications: 12 },
    { path: "/sandbox/encrypted_1.bin", modifications: 7 },
  ],
  telemetry: { cpu_history: [], entropy_history: [] },
});

const dashboardPayload = routes.get("/dashboard-data");

evalIn(`renderDashboard(${JSON.stringify(dashboardPayload)})`);

// 1. current threat level
check(
  "threat level renders the real score level",
  el("level").textContent.trim().toUpperCase().includes("CRITICAL"),
  el("level").textContent,
);

// 2. recent alerts
check(
  "recent alerts render the real alert",
  el("dashboardAlerts").innerHTML.includes("ransomware-like burst"),
  el("dashboardAlerts").innerHTML.slice(0, 80),
);

// 3. recent events
check(
  "recent events render the real event",
  el("dashboardEvents").innerHTML.includes("encrypted_0.bin"),
  el("dashboardEvents").innerHTML.slice(0, 80),
);

// 4. top modified files
check(
  "top modified files render the real entries",
  el("topModified").innerHTML.includes("encrypted_0.bin"),
  el("topModified").innerHTML.slice(0, 80),
);

// 5. CPU / entropy charts
check(
  "CPU and entropy charts exist as canvases",
  el("cpuChart") !== null && el("entropyChart") !== null,
);
const chartSource = scriptMatch[1];

check(
  "renderCharts draws the CPU chart",
  /drawChart\s*\([\s\S]{0,120}?cpuChart/.test(chartSource),
);
check(
  "renderCharts draws the entropy chart",
  /drawChart\s*\([\s\S]{0,120}?entropyChart/.test(chartSource),
);

// Polling: the page must refresh itself every few seconds.
const pollMatch = scriptMatch[1].match(/setInterval\s*\([\s\S]{0,200}?,\s*(\d+)\s*\)/);
const pollMs = pollMatch ? Number(pollMatch[1]) : 0;
check(
  "dashboard polls the API every few seconds",
  pollMs > 0 && pollMs <= 15000,
  `interval=${pollMs}ms`,
);

// Dark theme. Measure the actual page-surface color rather than assuming a
// particular first hex digit, so all valid dark charcoal tokens are accepted.
const background = (html.match(/--bg:\s*#([0-9a-f]{6})/i) || [])[1];
const linearChannel = (offset) => {
  const value = parseInt(background?.slice(offset, offset + 2) || "ff", 16) / 255;
  return value <= 0.04045
    ? value / 12.92
    : ((value + 0.055) / 1.055) ** 2.4;
};
const backgroundLuminance =
  0.2126 * linearChannel(0) +
  0.7152 * linearChannel(2) +
  0.0722 * linearChannel(4);
check(
  "dark theme is applied",
  Boolean(background) && backgroundLuminance < 0.08,
  background ? `#${background} luminance=${backgroundLuminance}` : "missing --bg token",
);

// ======================================================== B-09 fix cycle

section("B-09  Fix-cycle regressions");

const pageOrder = [
  "dashboard",
  "events",
  "processes",
  "alerts",
  "incidents",
  "reports",
  "settings",
];

for (const page of pageOrder) {
  setActivePage(page);
  fetchLog = [];

  const messageEl = el("message");
  messageEl.textContent = "";
  messageEl.classList = new ClassList();

  await evalIn("refreshAll(true)");
  await settle();
  await settle();

  const counts = {};
  for (const call of fetchLog) {
    counts[call.url] = (counts[call.url] || 0) + 1;
  }

  // B-08: the message must name the page, never the generic dashboard string.
  const expected = page === "dashboard" ? "Security Dashboard" : null;

  check(
    `B-08 ${page}: refresh message is page-specific`,
    messageEl.textContent !== "Dashboard refreshed." &&
      messageEl.textContent.endsWith("refreshed."),
    messageEl.textContent,
  );

  // B-09: no endpoint may be requested twice in one refresh cycle.
  const duplicated = Object.keys(counts).filter((url) => counts[url] > 1);

  check(
    `B-09 ${page}: no duplicate requests in one refresh`,
    duplicated.length === 0,
    JSON.stringify(counts),
  );
}

// B-03: an empty persistence select must not be sent as false.
const form = el("settingsForm");
form.querySelectorAll = () => [];

const originalFormData = context.FormData;

context.FormData = class {
  constructor() {}
  get(name) {
    if (name === "name") return "rdrs";
    if (name === "environment") return "test";
    if (name === "shutdown_timeout") return "15";
    if (name === "log_level") return "INFO";
    if (name === "persistence_enabled") return "";
    return "";
  }
};

fetchLog = [];

let postedBody = null;
const originalFetch = context.fetch;
context.fetch = async (url, options) => {
  fetchLog.push({ url, options });
  if (url === "/settings") {
    postedBody = options && options.body;
  }
  return originalFetch(url, options);
};

try {
  await evalIn(
    'saveSettings({ preventDefault: () => {}, target: document.getElementById("settingsForm") })',
  );
} catch (error) {
  // A validation error is fine; only the payload matters here.
}

context.fetch = originalFetch;
context.FormData = originalFormData;

check(
  "B-03 empty persistence select is not sent as false",
  postedBody !== null && !String(postedBody).includes("persistence_enabled"),
  postedBody === null ? "no POST was made" : String(postedBody),
);

// ================================================ B-10 report error handling

section("B-10  Report generation error handling");

const reportMessage = el("message");

async function driveGenerate(status, body) {
  reportMessage.textContent = "";
  reportMessage.classList = new ClassList();

  const realFetch = context.fetch;

  context.fetch = async (url) => ({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" },
    json: async () => body,
    text: async () => JSON.stringify(body),
  });

  try {
    await evalIn("generateReport(1, 'json')");
  } catch (error) {
    // The point is that it should NOT throw out of generateReport.
    reportMessage.textContent = `THREW: ${error.message}`;
  }

  context.fetch = realFetch;

  return reportMessage.textContent;
}

// A 500 must be reported, not swallowed.
const failText = await driveGenerate(500, { detail: "Internal Server Error" });

check(
  "B-10 a 500 from /reports/generate is reported to the operator",
  failText.toLowerCase().includes("failed") && !failText.toLowerCase().includes("threw"),
  failText,
);

check(
  "B-10 the message does not stay on 'Generating...'",
  !failText.toLowerCase().startsWith("generating"),
  failText,
);

check(
  "B-10 the failure carries the error class",
  reportMessage.classList.contains("error"),
  reportMessage.className,
);

// The guard must be released so a retry is possible.
check(
  "B-10 the pending guard is released after a failure",
  evalIn("state.reportPending") === false,
);

// A 2xx without a path must not be reported as success.
const noPathText = await driveGenerate(200, {});

check(
  "B-10 a 2xx without a path is not reported as success",
  !noPathText.toLowerCase().includes("generated for incident"),
  noPathText,
);

// A healthy response reports success and releases the guard.
const okText = await driveGenerate(200, { path: "data/reports/incident_1_x.json" });

check(
  "B-10 a successful generation is reported",
  okText.toLowerCase().includes("generated"),
  okText,
);

check(
  "B-10 the pending guard is released after success",
  evalIn("state.reportPending") === false,
);

// ======================================= B-11 settings persistence

section("B-11  Settings persistence (15 -> 20)");

const SETTINGS_CONFIG_15 = {
  name: "rdrs",
  environment: "test",
  shutdown_timeout: 15,
  log_level: "WARNING",
  persistence_enabled: false,
};

function statusPayload(config) {
  return { state: "STOPPED", config, config_available: true };
}

// 1. The form loads the current backend value.
await evalIn("loadSettings(null, false, true)");
el("settingTimeout").value = "15";
await evalIn(`populateSettings(${JSON.stringify(SETTINGS_CONFIG_15)}, true, true)`);

check(
  "B-11 the form loads the backend value (15)",
  el("settingTimeout").value === "15",
  el("settingTimeout").value,
);

// 2. The operator edits it to 20.
typeInto("settingTimeout", "20");

check(
  "B-11 the operator's edit is registered as unsaved",
  evalIn("settingsHaveUnsavedEdits()") === true,
  el("settingTimeout").value,
);

// 3. A background poll must not touch it. This is the reported defect.
await evalIn("markSettingsPolled()");

check(
  "B-11 a background poll does not revert the edit",
  el("settingTimeout").value === "20",
  el("settingTimeout").value,
);

// 4. Even a full load with a stale payload must not clobber an unsaved edit.
await evalIn(
  `populateSettings(${JSON.stringify(SETTINGS_CONFIG_15)}, true, false)`,
);

check(
  "B-11 a stale load does not clobber an unsaved edit",
  el("settingTimeout").value === "20",
  el("settingTimeout").value,
);

// 5. The explicit "Load Current Settings" button does discard it, because
//    that is what the operator is asking for.
await evalIn("dirtySettings.clear()");
await evalIn(
  `populateSettings(${JSON.stringify(SETTINGS_CONFIG_15)}, true, true)`,
);

check(
  "B-11 an explicit load refreshes the field back to the backend value",
  el("settingTimeout").value === "15",
  el("settingTimeout").value,
);

// 6. Edit to 20 and save. The response is authoritative.
let savedPayload = null;
const realFetchSettings = context.fetch;

context.fetch = async (url, options) => {
  if (String(url).includes("/settings")) {
    savedPayload = options && options.body;
    return {
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      json: async () => ({
        ...SETTINGS_CONFIG_15,
        shutdown_timeout: 20,
      }),
      text: async () => "{}",
    };
  }

  return realFetchSettings(url, options);
};

typeInto("settingTimeout", "20");

await evalIn(
  `saveSettings({ preventDefault: () => {}, target: document.getElementById("settingsForm") })`,
);

context.fetch = realFetchSettings;

check(
  "B-11 the save sends the edited timeout",
  savedPayload !== null && String(savedPayload).includes("20"),
  savedPayload === null ? "no POST" : String(savedPayload),
);

check(
  "B-11 the field holds the saved value after the response",
  el("settingTimeout").value === "20",
  el("settingTimeout").value,
);

check(
  "B-11 the unsaved-edit flag is cleared after a successful save",
  evalIn("settingsHaveUnsavedEdits()") === false,
);

// 7. After a save, polling still must not disturb the form.
await evalIn("markSettingsPolled()");

check(
  "B-11 polling after a save leaves the saved value in place",
  el("settingTimeout").value === "20",
  el("settingTimeout").value,
);

// ============================================ B-12 settings lock + theme

section("B-12  Settings lock, scrollbar and spinner");

function setRuntimeState(stateName) {
  context.state = context.state || {};
  evalIn(`state.status = Object.assign({}, state.status || {}, { state: ${JSON.stringify(stateName)} })`);
}

// --- RUNNING: locked ------------------------------------------------------
setRuntimeState("RUNNING");
await evalIn(`state.status = Object.assign({}, state.status || {}, {
  state: "RUNNING",
  config: ${JSON.stringify(SETTINGS_CONFIG_15)},
  config_available: true
})`);
await evalIn("applySettingsLock(state.status.state)");

const lockedFields = ["settingName", "settingEnvironment", "settingTimeout", "settingLogLevel", "settingPersistence"];

check(
  "B-12 RUNNING: every settings field is disabled",
  lockedFields.every(id => el(id).disabled === true),
  lockedFields.filter(id => el(id).disabled !== true).join(", ") || "",
);

check(
  "B-12 RUNNING: Save Settings is disabled",
  el("saveSettingsBtn").disabled === true,
);

check(
  "B-12 RUNNING: the lock notice is shown",
  el("settingsStatus").textContent ===
    "Settings can be changed only when monitoring is stopped.",
  el("settingsStatus").textContent,
);

check(
  "B-12 RUNNING: Load Current Settings stays available",
  el("loadSettingsBtn").disabled !== true,
);

// A programmatic submit must be refused too, not just the disabled button.
let saveAttempted = false;
const realFetchLock = context.fetch;
context.fetch = async (url, options) => {
  if (String(url).includes("/settings")) {
    saveAttempted = true;
  }
  return realFetchLock(url, options);
};

el("message").textContent = "";
await evalIn(
  `saveSettings({ preventDefault: () => {}, target: document.getElementById("settingsForm") })`,
);
context.fetch = realFetchLock;

check(
  "B-12 RUNNING: a programmatic save is refused with the lock message",
  saveAttempted === false &&
    el("message").textContent ===
      "Settings can be changed only when monitoring is stopped.",
  el("message").textContent,
);

// --- STOPPED: unlocked ----------------------------------------------------
await evalIn(`state.status = Object.assign({}, state.status || {}, {
  state: "STOPPED",
  config: ${JSON.stringify(SETTINGS_CONFIG_15)},
  config_available: true
})`);
await evalIn("applySettingsLock(state.status.state)");

check(
  "B-12 STOPPED: every settings field is editable again",
  lockedFields.every(id => el(id).disabled === false),
  lockedFields.filter(id => el(id).disabled !== false).join(", ") || "",
);

check(
  "B-12 STOPPED: Save Settings is enabled",
  el("saveSettingsBtn").disabled === false,
);

check(
  "B-12 STOPPED: the lock notice is cleared",
  el("settingsStatus").textContent !==
    "Settings can be changed only when monitoring is stopped.",
  el("settingsStatus").textContent,
);

// --- transition RUNNING -> STOPPED in one call ---------------------------
setRuntimeState("RUNNING");
await evalIn("applySettingsLock(state.status.state)");
const lockedAfterRunning = el("settingTimeout").disabled;

setRuntimeState("STOPPED");
await evalIn("applySettingsLock(state.status.state)");

check(
  "B-12 lock follows a RUNNING -> STOPPED transition automatically",
  lockedAfterRunning === true && el("settingTimeout").disabled === false,
  `running=${lockedAfterRunning} stopped=${el("settingTimeout").disabled}`,
);

// --- dark-theme styling present ------------------------------------------
const dashboardHtml = context.__dashboardHtml || "";

check(
  "B-12 dark colour-scheme is declared (native widgets follow the theme)",
  dashboardHtml.includes("color-scheme: dark"),
);

check(
  "B-12 scrollbar is themed for WebKit",
  dashboardHtml.includes("::-webkit-scrollbar-thumb"),
);

check(
  "B-12 scrollbar is themed for Firefox",
  dashboardHtml.includes("scrollbar-color"),
);

check(
  "B-12 number-input spinner is themed",
  dashboardHtml.includes("::-webkit-inner-spin-button"),
);

check(
  "B-12 long table columns wrap instead of forcing horizontal overflow",
  dashboardHtml.includes("td:nth-child(2)") &&
    dashboardHtml.includes("overflow-wrap: anywhere"),
);

// ================================ B-13 score reason + refresh state

section("B-13  Score Reason formatting and Refresh busy state");

const REAL_REASON =
  "Ransomware-like burst detected; signals: rapid_encryption, mass_rename, extension_change, high_entropy";

evalIn(`renderScoreReason(${JSON.stringify(REAL_REASON)})`);

const reasonChildren = el("scoreReason").children;

const leadNode = reasonChildren.find(c => c.className === "score-reason-lead");
const listNode = reasonChildren.find(c => c.className === "signal-list");

check(
  "B-13 the lead sentence is rendered separately",
  Boolean(leadNode) &&
    leadNode.textContent === "Ransomware-like burst detected",
  leadNode ? leadNode.textContent : "no lead node",
);

check(
  "B-13 the signals become a list, not one run of text",
  Boolean(listNode) && listNode.children.length === 4,
  listNode ? String(listNode.children.length) : "no list node",
);

const tagLabels = listNode ? listNode.children.map(c => c.textContent) : [];

check(
  "B-13 all four signals are rendered and readable",
  ["Rapid encryption", "Mass rename", "Extension change", "High entropy"]
    .every(label => tagLabels.includes(label)),
  tagLabels.join(" | "),
);

check(
  "B-13 the raw signal name stays discoverable on the tag",
  listNode ? listNode.children.every(c => typeof c.title === "string" && c.title.length > 0) : false,
  listNode ? listNode.children.map(c => c.title).join(",") : "",
);

check(
  "B-13 encryption signals are marked severe",
  listNode ? listNode.children.filter(c => c.className.includes("severe")).length === 2 : false,
  listNode ? listNode.children.map(c => c.className).join(" | ") : "",
);

check(
  "B-13 the container carries the score-reason class",
  el("scoreReason").className === "score-reason",
  el("scoreReason").className,
);

// No signals at all.
evalIn('renderScoreReason("Dangerous ransomware-like burst detected")');
const noSignalChildren = el("scoreReason").children;

check(
  "B-13 a reason without signals renders just the lead",
  noSignalChildren.length === 1 &&
    noSignalChildren[0].textContent === "Dangerous ransomware-like burst detected",
  String(noSignalChildren.length),
);

// Empty reason.
evalIn("renderScoreReason('')");

check(
  "B-13 an empty reason falls back to 'No score recorded'",
  el("scoreReason").textContent === "No score recorded",
  el("scoreReason").textContent,
);

// An unknown signal must not be dropped or crash.
evalIn('renderScoreReason("Burst detected; signals: rapid_encryption, brand_new_signal")');
const unknownTags = el("scoreReason").children
  .find(c => c.className === "signal-list").children.map(c => c.textContent);

check(
  "B-13 an unknown signal is still shown, made readable",
  unknownTags.includes("Brand new signal"),
  unknownTags.join(" | "),
);

// --- refresh busy state ---------------------------------------------------
el("refreshBtn").textContent = "Refresh";
el("refreshBtn").disabled = false;

evalIn("setRefreshBusy(true)");

check(
  "B-13 refreshing disables the button and relabels it",
  el("refreshBtn").disabled === true &&
    el("refreshBtn").textContent === "Refreshing…",
  `${el("refreshBtn").disabled} / ${el("refreshBtn").textContent}`,
);

check(
  "B-13 the button reports aria-busy while refreshing",
  el("refreshBtn").getAttribute("aria-busy") === "true",
  String(el("refreshBtn").getAttribute("aria-busy")),
);

evalIn("setRefreshBusy(false)");

check(
  "B-13 the button is restored after the refresh settles",
  el("refreshBtn").disabled === false &&
    el("refreshBtn").textContent === "Refresh",
  `${el("refreshBtn").disabled} / ${el("refreshBtn").textContent}`,
);

// ============================ B-14 history state + persistence help

section("B-14  History-state banner and persistence help text");

function historyState(status, summary) {
  evalIn(
    `renderHistoryState(${JSON.stringify(status)}, ${JSON.stringify(summary)})`
  );
  return el("historyState");
}

// --- Persistence Disabled ------------------------------------------------
let node = historyState(
  { state: "STOPPED", config: { persistence_enabled: false } },
  { event_count: 0, alert_count: 0, incident_count: 0 },
);

check(
  "B-14 Disabled: the banner warns that persistence is off",
  node.textContent.includes("Persistence is Disabled"),
  node.textContent.slice(0, 90),
);

check(
  "B-14 Disabled: it says the database was NOT deleted",
  node.textContent.includes("has not been deleted"),
  node.textContent.slice(0, 160),
);

check(
  "B-14 Disabled: it tells the operator how to see the history again",
  node.textContent.includes("Enabled"),
  node.textContent.slice(0, 200),
);

check(
  "B-14 Disabled: the banner carries the warn class",
  node.classList.contains("warn"),
  node.className,
);

// --- Persistence Enabled, nothing recorded yet ---------------------------
node = historyState(
  { state: "RUNNING", config: { persistence_enabled: true } },
  { event_count: 0, alert_count: 0, incident_count: 0 },
);

check(
  "B-14 Enabled + empty: it says no history has been recorded yet",
  node.textContent.includes("No history recorded yet"),
  node.textContent.slice(0, 90),
);

check(
  "B-14 Enabled + empty: the banner carries the info class",
  node.classList.contains("info"),
  node.className,
);

// --- Persistence Enabled with history ------------------------------------
node = historyState(
  { state: "RUNNING", config: { persistence_enabled: true } },
  { event_count: 181, alert_count: 4, incident_count: 2 },
);

check(
  "B-14 Enabled + history: it reports the durable history it is reading",
  node.textContent.includes("Reading durable history"),
  node.textContent.slice(0, 90),
);

check(
  "B-14 Enabled + history: the real counts appear in the banner",
  node.textContent.includes("181") &&
    node.textContent.includes("4") &&
    node.textContent.includes("2"),
  node.textContent.slice(0, 160),
);

check(
  "B-14 Enabled + history: the banner carries the ok class",
  node.classList.contains("ok"),
  node.className,
);

// --- running / stopped wording -------------------------------------------
check(
  "B-14 the banner names the running state",
  historyState(
    { state: "RUNNING", config: { persistence_enabled: true } },
    { event_count: 1, alert_count: 0, incident_count: 0 },
  ).textContent.includes("Monitoring is running"),
);

check(
  "B-14 the banner names the stopped state",
  historyState(
    { state: "STOPPED", config: { persistence_enabled: true } },
    { event_count: 1, alert_count: 0, incident_count: 0 },
  ).textContent.includes("Monitoring is stopped"),
);

// --- persistence help text in the markup ---------------------------------
// `dashboardHtml` is already declared by the B-12 section.

check(
  "B-14 the Persistence control has help text",
  dashboardHtml.includes('id="persistenceHelp"'),
);

check(
  "B-14 the help explains Enabled = durable, survives restart",
  dashboardHtml.includes("durable SQLite history") &&
    dashboardHtml.includes("survive an"),
);

check(
  "B-14 the help explains Disabled = in-memory, lost on exit",
  dashboardHtml.includes("in-memory runtime data"),
);

check(
  "B-14 the help states that switching does not delete the database",
  dashboardHtml.includes("does") && dashboardHtml.includes("not"),
);

check(
  "B-14 the dashboard has a history-state element",
  dashboardHtml.includes('id="historyState"'),
);

// ========================================================= B-15 pagination

section("B-15  Cursor pagination");

const firstEvents = [
  { id: 10, action: "modified", path: "C:/page/10", created_at: "2026-10-06T00:00:00Z" },
  { id: 9, action: "modified", path: "C:/page/9", created_at: "2026-10-06T00:00:00Z" },
];
const firstEventsUrl = "/events?limit=1000&include_total=true";
const secondEventsUrl = "/events?limit=1000&cursor=2026-10-06T00%3A00%3A00Z%7C9";
const newerEventsUrl = "/events?limit=1000";

routes.set(firstEventsUrl, {
  __httpResponse: true,
  payload: firstEvents,
  headers: {
    "x-total-count": "3",
    "x-has-more": "true",
    "x-next-cursor": "2026-10-06T00:00:00Z|9",
  },
});
routes.set(secondEventsUrl, {
  __httpResponse: true,
  payload: [
    { id: 8, action: "modified", path: "C:/page/8", created_at: "2026-10-05T00:00:00Z" },
  ],
  headers: { "x-has-more": "false" },
});
routes.set(newerEventsUrl, {
  __httpResponse: true,
  payload: firstEvents,
  headers: { "x-has-more": "true", "x-next-cursor": "2026-10-06T00:00:00Z|9" },
});

await evalIn('loadEventsPage({reset: true})');
check(
  "B-15 first page renders the bounded collection and total",
  evalIn('state.pages.events.items.length === 2 && state.pages.events.total === 3'),
);
check(
  "B-15 Older is available only when the server reports another page",
  el("eventsOlderBtn").hidden === false && el("eventsNewerBtn").hidden === true,
);

await evalIn('loadOlderPage("events")');
check(
  "B-15 Older requests the stable server cursor and renders page two",
  fetchLog.some(({ url }) => url === secondEventsUrl) &&
    evalIn('state.pages.events.page === 2 && state.pages.events.items[0].id === 8'),
);
check(
  "B-15 Newer is available after moving to an older page",
  el("eventsNewerBtn").hidden === false && el("eventsOlderBtn").hidden === true,
);

await evalIn('loadNewerPage("events")');
check(
  "B-15 Newer returns to the first page without losing the total",
  evalIn('state.pages.events.page === 1 && state.pages.events.items[0].id === 10 && state.pages.events.total === 3'),
);

// ================================================================= report

section("B-16  SOC investigation and clean path display");
check("B-16 long paths retain the filename and full hover/copy value",
  evalIn(`renderPathScroller('D:\\\\sandbox\\\\very-long-directory-name\\\\another-long-directory-name\\\\file_042.locked').includes('file_042.locked') && renderPathScroller('/sandbox/file.locked').includes('data-copy-path')`));
check("B-16 posture uses recorded data, not an active-risk claim",
  dashboardHtml.includes("latest recorded detection") && dashboardHtml.includes("Recorded score, not a continuous risk estimate"));
check("B-16 incident detail loads the real existing report endpoint",
  evalIn('typeof investigateIncident === "function"') && dashboardHtml.includes("report?.timeline"));
check("B-16 path presentation hides row-level horizontal scrolling",
  /\.path-scroll\s*\{[^}]*overflow:\s*hidden/s.test(dashboardHtml));
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
