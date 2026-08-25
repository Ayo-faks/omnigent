"use strict";

/**
 * Arca host connection (Databricks-internal).
 *
 * Arca is Databricks' internal sandbox CLI: each user has one EC2 dev
 * instance, `arca ssh <args...>` passes the args through to ssh against it
 * (starting the instance first when needed). Connecting that instance as an
 * Omnigent host means running, over `arca ssh`:
 *
 *   isaac omni host --server <url> --background --non-interactive
 *
 * (`isaac` is the Databricks-internal launcher that provides the `omni` CLI
 * on Arca instances.)
 *
 * The remote daemon then opens the ordinary outbound host tunnel using the
 * Arca box's own Databricks credentials (synced by arca), so no secret ever
 * leaves this machine. `--background` exits 0 only once the daemon survived
 * startup, and `--non-interactive` fails loud instead of dangling on a browser
 * login — both are what make the exit code a trustworthy signal here.
 *
 * This module is main-process-free: the binary probe and process spawn are
 * injected so everything is unit-testable without Electron or a real arca.
 */

const { execFile, execFileSync, spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

/**
 * Connecting may cold-start the EC2 instance, which takes minutes — give the
 * whole ssh + remote daemon startup a generous ceiling.
 */
const CONNECT_TIMEOUT_MS = 5 * 60 * 1000;

/**
 * Restart backoff for the background keepalive session, and how long a
 * session must survive for the backoff to reset. The ladder tops out at five
 * minutes so a suspended/unreachable instance isn't hammered, while a
 * transient ssh drop reconnects quickly.
 */
const KEEPALIVE_RESTART_DELAYS_MS = [5_000, 15_000, 60_000, 300_000];
const KEEPALIVE_STABLE_MS = 5 * 60 * 1000;

/**
 * Well-known install locations for the arca binary. Probed because a
 * GUI-launched Electron app inherits a minimal PATH (mirrors the omnigent CLI
 * resolution in omnigent_cli.js).
 *
 * @returns {string[]}
 */
function candidatePaths() {
  const home = os.homedir();
  return [
    "/usr/local/bin/arca",
    "/opt/homebrew/bin/arca",
    path.join(home, ".local", "bin", "arca"),
  ];
}

/**
 * True when `p` exists, is a regular file, and is executable by this process.
 *
 * @param {string} p
 * @returns {boolean}
 */
function isExecutableFile(p) {
  try {
    if (!fs.statSync(p).isFile()) return false;
    fs.accessSync(p, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

/**
 * Resolve `arca` on PATH via the shell (so login-shell PATHs resolve), else
 * null. Arca is macOS-only, so no Windows branch.
 *
 * @returns {string | null}
 */
function whichArca() {
  try {
    const out = execFileSync("/bin/sh", ["-c", "command -v arca"], { encoding: "utf8" });
    return out.trim() || null;
  } catch {
    return null;
  }
}

/**
 * Locate the arca binary: PATH first, then well-known locations. Null when
 * arca isn't installed on this machine.
 *
 * @param {{
 *   isExecutableFile?: (p: string) => boolean,
 *   whichArca?: () => string | null,
 *   candidatePaths?: () => string[],
 * }} [deps]
 * @returns {string | null}
 */
function resolveArcaPath(deps = {}) {
  const isExec = deps.isExecutableFile || isExecutableFile;
  const onPath = (deps.whichArca || whichArca)();
  if (onPath && isExec(onPath)) return onPath;
  for (const candidate of (deps.candidatePaths || candidatePaths)()) {
    if (isExec(candidate)) return candidate;
  }
  return null;
}

/**
 * Build the arca argv that connects the instance to `serverUrl`. Everything
 * after "ssh" is passed through to ssh and runs as the remote command.
 *
 * @param {string} serverUrl
 * @returns {string[]}
 */
function buildConnectArgs(serverUrl) {
  const url = new URL(serverUrl);
  if (url.protocol !== "https:" && url.protocol !== "http:") {
    throw new Error(`unsupported server URL scheme: ${url.protocol}`);
  }
  return [
    "ssh",
    "isaac",
    "omni",
    "host",
    "--server",
    url.toString(),
    "--background",
    "--non-interactive",
  ];
}

/**
 * Map a failed connect run to an actionable user-facing result. Matched
 * against known arca / omnigent CLI failure shapes; anything unrecognized
 * falls through to the captured output.
 *
 * @param {{ code: number | null, stdout: string, stderr: string, timedOut?: boolean }} run
 * @returns {{ ok: false, error: string, authError?: boolean }}
 */
function describeConnectFailure(run) {
  const output = `${run.stderr}\n${run.stdout}`;
  if (run.timedOut) {
    return {
      ok: false,
      error:
        "Connecting to Arca timed out. The instance may still be starting — " +
        "check `arca status` and try again.",
    };
  }
  // `omni host --non-interactive` fails loud with a sign-in hint when the
  // Arca box's Databricks credentials can't mint a server token.
  if (/not signed in/i.test(output)) {
    return {
      ok: false,
      authError: true,
      error:
        "The Arca instance isn't signed in to this server. Run " +
        "`arca ssh` and sign in with `isaac omni login <server-url>`, then try again.",
    };
  }
  // The remote shell couldn't find isaac (or isaac couldn't find omni) on the
  // Arca instance.
  if (run.code === 127 || /(isaac|omni(gent)?):? .*(command )?not found/i.test(output)) {
    return {
      ok: false,
      error:
        "`isaac omni` isn't available on the Arca instance. " +
        "Check the isaac setup there (`arca ssh`, then `isaac omni --help`) and try again.",
    };
  }
  if (/error connecting to arca/i.test(output)) {
    return {
      ok: false,
      error:
        "Couldn't reach the Arca instance. Try `arca stop && arca start` in a terminal, " +
        "then connect again.",
    };
  }
  const detail = run.stderr.trim() || run.stdout.trim();
  return {
    ok: false,
    error: detail
      ? `Connecting to Arca failed: ${lastLine(detail)}`
      : `Connecting to Arca failed (exit code ${run.code ?? "unknown"}).`,
  };
}

/**
 * The last non-empty line of captured output — arca and ssh are chatty, and
 * the final line is where both put the actual error.
 *
 * @param {string} text
 * @returns {string}
 */
function lastLine(text) {
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  return lines[lines.length - 1] ?? text;
}

/**
 * Connect the user's Arca instance to `serverUrl` as an Omnigent host. Never
 * rejects — every failure resolves as `{ ok: false, error }` so the IPC layer
 * forwards it verbatim.
 *
 * @param {string} serverUrl The window's connected server URL.
 * @param {{
 *   timeoutMs?: number,
 *   resolveArcaPath?: () => string | null,
 *   execFile?: typeof execFile,
 * }} [deps]
 * @returns {Promise<{ ok: boolean, error?: string, authError?: boolean }>}
 */
function connectArcaHost(serverUrl, deps = {}) {
  const timeoutMs = deps.timeoutMs ?? CONNECT_TIMEOUT_MS;
  const arcaPath = (deps.resolveArcaPath || resolveArcaPath)();
  if (!arcaPath) {
    return Promise.resolve({
      ok: false,
      error: "The arca CLI was not found on this machine.",
    });
  }
  let args;
  try {
    args = buildConnectArgs(serverUrl);
  } catch (error) {
    return Promise.resolve({ ok: false, error: `Invalid server URL: ${error.message}` });
  }
  const run = deps.execFile || execFile;
  return new Promise((resolve) => {
    run(
      arcaPath,
      args,
      { timeout: timeoutMs, encoding: "utf8", maxBuffer: 4 * 1024 * 1024 },
      (error, stdout = "", stderr = "") => {
        if (!error) {
          resolve({ ok: true });
          return;
        }
        resolve(
          describeConnectFailure({
            code: typeof error.code === "number" ? error.code : null,
            stdout: String(stdout),
            stderr: String(stderr),
            timedOut: error.killed === true || error.signal === "SIGTERM",
          }),
        );
      },
    );
  });
}

/**
 * The argv for the long-lived keepalive session. `sleep infinity` holds the
 * ssh channel open with zero remote cost; the ServerAlive options make ssh
 * itself notice a dead peer within ~90s so the manager can reconnect.
 *
 * @returns {string[]}
 */
function buildKeepaliveArgs() {
  return [
    "ssh",
    "-o",
    "ServerAliveInterval=30",
    "-o",
    "ServerAliveCountMax=3",
    "sleep",
    "infinity",
  ];
}

/**
 * A background `arca ssh` session the app maintains while it runs, so the
 * Arca instance is never idle (Arca suspends idle instances, which would take
 * the connected host down with it). Started after a successful host connect;
 * restarted with backoff on unexpected exit; stopped on app quit.
 *
 * Everything impure (spawn, timers, clock, binary resolution, logging) is
 * injectable so the restart ladder is unit-testable without a real arca.
 *
 * @param {{
 *   spawn?: typeof spawn,
 *   resolveArcaPath?: () => string | null,
 *   setTimeout?: typeof setTimeout,
 *   clearTimeout?: typeof clearTimeout,
 *   now?: () => number,
 *   log?: (message: string) => void,
 * }} [deps]
 * @returns {{ start: () => void, stop: () => void, isRunning: () => boolean }}
 */
function createArcaKeepalive(deps = {}) {
  const spawnFn = deps.spawn || spawn;
  const resolvePath = deps.resolveArcaPath || resolveArcaPath;
  const setTimeoutFn = deps.setTimeout || setTimeout;
  const clearTimeoutFn = deps.clearTimeout || clearTimeout;
  const now = deps.now || Date.now;
  const log = deps.log || (() => {});

  /** @type {import("node:child_process").ChildProcess | null} */
  let child = null;
  let restartTimer = null;
  let stopped = true;
  let attempt = 0;
  let startedAt = 0;

  function scheduleRestart() {
    const delay =
      KEEPALIVE_RESTART_DELAYS_MS[Math.min(attempt, KEEPALIVE_RESTART_DELAYS_MS.length - 1)];
    attempt += 1;
    log(`arca keepalive: session ended; reconnecting in ${Math.round(delay / 1000)}s`);
    restartTimer = setTimeoutFn(() => {
      restartTimer = null;
      launch();
    }, delay);
    // The pending reconnect must never hold the app open at quit.
    if (restartTimer && typeof restartTimer.unref === "function") restartTimer.unref();
  }

  function onEnded() {
    child = null;
    if (stopped) return;
    // A session that survived a while was healthy — treat the next drop as
    // fresh rather than climbing the ladder forever.
    if (now() - startedAt >= KEEPALIVE_STABLE_MS) attempt = 0;
    scheduleRestart();
  }

  function launch() {
    if (stopped || child) return;
    const arcaPath = resolvePath();
    if (!arcaPath) {
      // No point retrying: the binary won't appear mid-session. A later
      // start() (next host connect) probes again.
      log("arca keepalive: arca CLI not found; keepalive disabled");
      stopped = true;
      return;
    }
    startedAt = now();
    const session = spawnFn(arcaPath, buildKeepaliveArgs(), { stdio: "ignore" });
    child = session;
    log(`arca keepalive: session started (pid ${session.pid ?? "?"})`);
    let ended = false;
    const endOnce = () => {
      if (ended) return;
      ended = true;
      if (child === session) onEnded();
    };
    session.on("exit", endOnce);
    session.on("error", endOnce);
  }

  return {
    /** Begin (or resume) maintaining the session. Idempotent. */
    start() {
      stopped = false;
      if (child || restartTimer) return;
      attempt = 0;
      launch();
    },
    /** Kill the session and cancel any pending reconnect. Idempotent. */
    stop() {
      stopped = true;
      if (restartTimer) {
        clearTimeoutFn(restartTimer);
        restartTimer = null;
      }
      if (child) {
        try {
          child.kill();
        } catch {
          // Already gone — nothing to clean up.
        }
        child = null;
      }
    },
    /** True while a session process is alive (not during a backoff wait). */
    isRunning() {
      return child !== null;
    },
  };
}

module.exports = {
  CONNECT_TIMEOUT_MS,
  KEEPALIVE_RESTART_DELAYS_MS,
  KEEPALIVE_STABLE_MS,
  buildConnectArgs,
  buildKeepaliveArgs,
  connectArcaHost,
  createArcaKeepalive,
  describeConnectFailure,
  resolveArcaPath,
};
