const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {
  INSTALL_SCHEMA_VERSION,
  REQUIREMENTS_PROBE,
  buildEnvironmentStamp,
  getPythonCandidates,
  getVenvPythonPath,
  isVersionAtLeast,
  lockIsStale,
  parsePythonVersion,
  stableStringify,
  stampsMatch,
  withInstallLock,
} = require("./python-envs");
const {
  computeNodeManifestFingerprint,
  isCompatibleBackendHealth,
  NODE_DEPENDENCY_CHECK_ARGS,
  NODE_DEPENDENCY_INSTALL_ARGS,
} = require("./start");

test("parses and compares Python versions", () => {
  assert.deepEqual(parsePythonVersion("3.11.9"), [3, 11, 9]);
  assert.deepEqual(parsePythonVersion("3.12"), [3, 12, 0]);
  assert.equal(parsePythonVersion("Python 3.12"), null);
  assert.equal(isVersionAtLeast([3, 11, 0]), true);
  assert.equal(isVersionAtLeast([3, 12, 0]), true);
  assert.equal(isVersionAtLeast([3, 10, 14]), false);
});

test("builds cross-platform virtual environment interpreter paths", () => {
  assert.equal(
    getVenvPythonPath("C:\\repo\\backend\\.venv", "win32"),
    "C:\\repo\\backend\\.venv\\Scripts\\python.exe",
  );
  assert.equal(
    getVenvPythonPath("/repo/backend/.venv", "linux"),
    "/repo/backend/.venv/bin/python",
  );
});

test("orders and deduplicates Python discovery candidates", () => {
  const candidates = getPythonCandidates({
    env: { LABAI_BOOTSTRAP_PYTHON: "custom-python", PYTHON: "custom-python" },
    platform: "win32",
    preferred: ["venv-python", "venv-python"],
  });
  assert.deepEqual(candidates, [
    { args: [], command: "venv-python" },
    { args: [], command: "custom-python" },
    { args: ["-3"], command: "py" },
    { args: [], command: "python" },
  ]);
});

test("stable JSON and environment stamps are deterministic and sensitive to inputs", () => {
  assert.equal(stableStringify({ b: 2, a: 1 }), stableStringify({ a: 1, b: 2 }));
  const base = {
    environmentName: "sandbox",
    imports: ["numpy"],
    python: { cacheTag: "cpython-312", implementation: "CPython", version: "3.12.4" },
    requirements: [{ content: "numpy>=2\n", name: "sandbox-requirements.txt" }],
  };
  const first = buildEnvironmentStamp(base);
  const second = buildEnvironmentStamp({ ...base });
  const changedRequirements = buildEnvironmentStamp({
    ...base,
    requirements: [{ content: "numpy>=2\npandas>=2\n", name: "sandbox-requirements.txt" }],
  });
  const changedPython = buildEnvironmentStamp({
    ...base,
    python: { ...base.python, version: "3.13.0" },
  });
  const changedSchema = buildEnvironmentStamp({
    ...base,
    imports: ["numpy", "pandas"],
  });

  assert.equal(stampsMatch(first, second), true);
  assert.notEqual(first.fingerprint, changedRequirements.fingerprint);
  assert.notEqual(first.fingerprint, changedPython.fingerprint);
  assert.notEqual(first.schemaHash, changedSchema.schemaHash);
  assert.notEqual(first.fingerprint, changedSchema.fingerprint);
  assert.equal(stampsMatch(null, first), false);
});

test("requires the exact sandbox health capability before reusing a backend", () => {
  assert.equal(
    isCompatibleBackendHealth({
      capabilities: { python_sandbox: true },
      sandbox: { ready: true },
      status: "ok",
    }),
    true,
  );
  assert.equal(isCompatibleBackendHealth({ status: "ok" }), false);
  assert.equal(
    isCompatibleBackendHealth({
      capabilities: { python_sandbox: true },
      sandbox: { ready: false },
      status: "ok",
    }),
    false,
  );
});

test("Node dependency fingerprint normalizes manifest newlines", () => {
  const first = computeNodeManifestFingerprint({
    arch: "x64",
    manifestContents: [{ content: "{\r\n}\r\n", name: "package.json" }],
    nodeModulesVersion: "127",
    platform: "win32",
  });
  const second = computeNodeManifestFingerprint({
    arch: "x64",
    manifestContents: [{ content: "{\n}\n", name: "package.json" }],
    nodeModulesVersion: "127",
    platform: "win32",
  });
  const changedAbi = computeNodeManifestFingerprint({
    arch: "x64",
    manifestContents: [{ content: "{\n}\n", name: "package.json" }],
    nodeModulesVersion: "131",
    platform: "win32",
  });
  assert.equal(first, second);
  assert.notEqual(first, changedAbi);
});

test("dependency commands explicitly include development packages", () => {
  assert.deepEqual([...NODE_DEPENDENCY_CHECK_ARGS], ["ls", "--include=dev", "--depth=0", "--json"]);
  assert.deepEqual(
    [...NODE_DEPENDENCY_INSTALL_ARGS],
    ["install", "--include=dev", "--no-audit", "--no-fund"],
  );
});

test("environment schema validates requested distribution versions", () => {
  assert.equal(INSTALL_SCHEMA_VERSION, 2);
  assert.match(REQUIREMENTS_PROBE, /metadata\.version/);
  assert.match(REQUIREMENTS_PROBE, /requirement\.specifier\.contains/);
});

test("malformed locks recover after the short grace period and record child owners", async (context) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "labai-lock-test-"));
  const lockPath = path.join(directory, "dependency.lock");
  context.after(() => fs.rmSync(directory, { force: true, recursive: true }));
  fs.writeFileSync(lockPath, "{", "utf8");

  let lockContents = null;
  await withInstallLock(
    lockPath,
    async (lock) => {
      const child = new EventEmitter();
      child.pid = process.pid;
      lock.trackChild(child);
      lockContents = JSON.parse(fs.readFileSync(lockPath, "utf8"));
      child.emit("close");
    },
    { invalidLockGraceMs: 0, timeoutMs: 1000 },
  );

  assert.deepEqual(lockContents.childPids, [process.pid]);
  assert.equal(fs.existsSync(lockPath), false);
});

test("a live installer child keeps a dead-owner lock active", (context) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "labai-lock-owner-test-"));
  const lockPath = path.join(directory, "dependency.lock");
  context.after(() => fs.rmSync(directory, { force: true, recursive: true }));
  fs.writeFileSync(
    lockPath,
    JSON.stringify({ childPids: [process.pid], hostname: os.hostname(), pid: 99999999 }),
    "utf8",
  );

  assert.equal(lockIsStale(lockPath, 1), false);
});
