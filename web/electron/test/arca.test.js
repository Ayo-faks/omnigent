"use strict";

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const {
  KEEPALIVE_RESTART_DELAYS_MS,
  KEEPALIVE_STABLE_MS,
  buildConnectArgs,
  buildKeepaliveArgs,
  connectArcaHost,
  createArcaKeepalive,
  describeConnectFailure,
  resolveArcaPath,
} = require("../src/arca");

/** A fake keepalive world: manual clock, captured timers, fake children. */
function keepaliveHarness() {
  const world = {
    time: 0,
    children: [],
    timers: [],
    spawnCalls: [],
  };
  const keepalive = createArcaKeepalive({
    resolveArcaPath: () => "/usr/local/bin/arca",
    now: () => world.time,
    spawn: (file, args) => {
      world.spawnCalls.push([file, args]);
      const child = new EventEmitter();
      child.pid = 100 + world.children.length;
      child.killed = false;
      child.kill = () => {
        child.killed = true;
      };
      world.children.push(child);
      return child;
    },
    setTimeout: (fn, delay) => {
      const timer = { fn, delay, cleared: false };
      world.timers.push(timer);
      return timer;
    },
    clearTimeout: (timer) => {
      timer.cleared = true;
    },
  });
  return { world, keepalive };
}

describe("arca binary resolution", () => {
  it("prefers PATH, then falls back to well-known locations", () => {
    assert.equal(
      resolveArcaPath({
        whichArca: () => "/from/path/arca",
        isExecutableFile: (p) => p === "/from/path/arca",
        candidatePaths: () => ["/usr/local/bin/arca"],
      }),
      "/from/path/arca",
    );
    assert.equal(
      resolveArcaPath({
        whichArca: () => null,
        isExecutableFile: (p) => p === "/usr/local/bin/arca",
        candidatePaths: () => ["/opt/homebrew/bin/arca", "/usr/local/bin/arca"],
      }),
      "/usr/local/bin/arca",
    );
    assert.equal(
      resolveArcaPath({
        whichArca: () => null,
        isExecutableFile: () => false,
        candidatePaths: () => ["/usr/local/bin/arca"],
      }),
      null,
    );
  });
});

describe("arca connect command", () => {
  it("passes the remote isaac omni host command through ssh", () => {
    assert.deepEqual(buildConnectArgs("https://workspace.example.com/ml/omnigents"), [
      "ssh",
      "isaac",
      "omni",
      "host",
      "--server",
      "https://workspace.example.com/ml/omnigents",
      "--background",
      "--non-interactive",
    ]);
  });

  it("rejects non-http(s) server URLs", () => {
    assert.throws(() => buildConnectArgs("file:///etc/passwd"));
    assert.throws(() => buildConnectArgs("not a url"));
  });
});

describe("arca connect failures", () => {
  it("maps a timeout, sign-in, missing-CLI, and unreachable instance", () => {
    assert.match(
      describeConnectFailure({ code: null, stdout: "", stderr: "", timedOut: true }).error,
      /timed out/i,
    );

    const auth = describeConnectFailure({
      code: 1,
      stdout: "",
      stderr: "Not signed in to https://srv (\u2026). Run `omnigent login https://srv` and retry.",
    });
    assert.equal(auth.authError, true);
    assert.match(auth.error, /isaac omni login/);

    assert.match(
      describeConnectFailure({ code: 127, stdout: "", stderr: "bash: isaac: command not found" })
        .error,
      /isn't available on the Arca instance/,
    );
    assert.match(
      describeConnectFailure({ code: 1, stdout: "", stderr: "isaac: omni: command not found" })
        .error,
      /isn't available on the Arca instance/,
    );

    assert.match(
      describeConnectFailure({
        code: 1,
        stdout: "",
        stderr: "Error connecting to arca. The instance may be stopped or unreachable.",
      }).error,
      /arca stop && arca start/,
    );
  });

  it("falls back to the last output line for unrecognized failures", () => {
    const result = describeConnectFailure({
      code: 1,
      stdout: "",
      stderr: "noise line\nssh: connect to host 1.2.3.4 port 22: Connection refused",
    });
    assert.match(result.error, /Connection refused/);
    assert.doesNotMatch(result.error, /noise line/);
  });
});

describe("arca keepalive", () => {
  it("spawns the ssh keepalive session once; start is idempotent", () => {
    const { world, keepalive } = keepaliveHarness();
    keepalive.start();
    keepalive.start();
    assert.equal(world.spawnCalls.length, 1);
    assert.deepEqual(world.spawnCalls[0], ["/usr/local/bin/arca", buildKeepaliveArgs()]);
    assert.equal(keepalive.isRunning(), true);
  });

  it("restarts with a climbing backoff and resets after a stable run", () => {
    const { world, keepalive } = keepaliveHarness();
    keepalive.start();

    // Two quick deaths climb the ladder.
    world.children[0].emit("exit", 1);
    assert.equal(world.timers[0].delay, KEEPALIVE_RESTART_DELAYS_MS[0]);
    world.timers[0].fn();
    world.children[1].emit("exit", 1);
    assert.equal(world.timers[1].delay, KEEPALIVE_RESTART_DELAYS_MS[1]);
    world.timers[1].fn();

    // A session that survives the stable window resets the ladder.
    world.time += KEEPALIVE_STABLE_MS;
    world.children[2].emit("exit", 0);
    assert.equal(world.timers[2].delay, KEEPALIVE_RESTART_DELAYS_MS[0]);
  });

  it("stop kills the session and cancels a pending reconnect", () => {
    const { world, keepalive } = keepaliveHarness();
    keepalive.start();
    world.children[0].emit("exit", 1); // now waiting on a reconnect timer
    keepalive.stop();
    assert.equal(world.timers[0].cleared, true);

    keepalive.start();
    assert.equal(world.spawnCalls.length, 2);
    keepalive.stop();
    assert.equal(world.children[1].killed, true);
    assert.equal(keepalive.isRunning(), false);
    // The kill-triggered exit must not schedule a reconnect after stop.
    world.children[1].emit("exit", 0);
    assert.equal(world.timers.length, 1);
  });

  it("disables itself when arca is not installed", () => {
    let spawned = 0;
    const keepalive = createArcaKeepalive({
      resolveArcaPath: () => null,
      spawn: () => {
        spawned += 1;
        return new EventEmitter();
      },
    });
    keepalive.start();
    assert.equal(spawned, 0);
    assert.equal(keepalive.isRunning(), false);
  });
});

describe("connectArcaHost", () => {
  it("resolves ok on a clean exit and never rejects on failure", async () => {
    const calls = [];
    const okRun = await connectArcaHost("https://srv.example.com", {
      resolveArcaPath: () => "/usr/local/bin/arca",
      execFile: (file, args, _opts, cb) => {
        calls.push([file, args]);
        cb(null, "started", "");
      },
    });
    assert.deepEqual(okRun, { ok: true });
    assert.equal(calls[0][0], "/usr/local/bin/arca");
    assert.equal(calls[0][1][0], "ssh");

    const failRun = await connectArcaHost("https://srv.example.com", {
      resolveArcaPath: () => "/usr/local/bin/arca",
      execFile: (_file, _args, _opts, cb) => {
        const error = new Error("exit 1");
        error.code = 1;
        cb(error, "", "bash: isaac: command not found");
      },
    });
    assert.equal(failRun.ok, false);
    assert.match(failRun.error, /Arca instance/);
  });

  it("fails cleanly when arca is not installed", async () => {
    const result = await connectArcaHost("https://srv.example.com", {
      resolveArcaPath: () => null,
      execFile: () => {
        throw new Error("must not spawn");
      },
    });
    assert.deepEqual(result, {
      ok: false,
      error: "The arca CLI was not found on this machine.",
    });
  });
});
