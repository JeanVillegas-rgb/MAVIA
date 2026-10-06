// One-command dev environment: kills stale Django zombies, starts a fresh
// Django backend, keeps `adb reverse` alive for a USB-connected phone (Metro
// on 8081, the API on 8000 -- re-armed on an interval since the adb daemon
// dropping the tunnel, e.g. after a device reconnect or `expo run:android`
// reinstall, is what actually causes "Unable to load script"), and starts
// Expo. Ctrl+C stops all of it. Run with `npm run dev` from mobile-app/.
//
// `npm run dev:wifi` (--wifi) is the no-cable version: the phone reaches this
// PC over the same Wi-Fi -- or the laptop joins the phone's own hotspot, which
// also gets around school/venue Wi-Fi that blocks devices from seeing each
// other. It finds this PC's LAN address, lets Django answer on it, points the
// app's API at it and starts Expo in LAN mode. No adb, no .env edits: both
// Django (python-dotenv) and Expo leave variables already set in the
// environment alone, so the address is simply passed in.

import { spawn, execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { networkInterfaces } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import readline from "node:readline";

const SCRIPTS_DIR = dirname(fileURLToPath(import.meta.url));
const MOBILE_APP_DIR = join(SCRIPTS_DIR, "..");
const BACKEND_DIR = join(MOBILE_APP_DIR, "..", "backend");
const REVERSE_PORTS = [8081, 8000];
const REVERSE_INTERVAL_MS = 5000;
const WIN = process.platform === "win32";
const WIFI = process.argv.includes("--wifi");

// This PC's address on the local network: the Wi-Fi adapter first, skipping
// virtual adapters (WSL / Hyper-V / VPN) the phone can never reach.
function lanAddress() {
  const candidates = [];
  for (const [name, addresses] of Object.entries(networkInterfaces())) {
    if (/vEthernet|WSL|Hyper-V|VirtualBox|VMware|Loopback|Tailscale|ZeroTier/i.test(name)) continue;
    for (const address of addresses ?? []) {
      if (address.family !== "IPv4" || address.internal) continue;
      if (!/^(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)/.test(address.address)) continue;
      candidates.push({ name, ip: address.address, wifi: /wi-?fi|wireless|wlan/i.test(name) });
    }
  }
  candidates.sort((a, b) => Number(b.wifi) - Number(a.wifi));
  return candidates[0] ?? null;
}

function resolveAdb() {
  const sdk = process.env.ANDROID_HOME || process.env.ANDROID_SDK_ROOT;
  if (sdk) {
    const exe = join(sdk, "platform-tools", WIN ? "adb.exe" : "adb");
    if (existsSync(exe)) return exe;
  }
  return "adb";
}

function resolveBackendPython() {
  if (process.env.MAVIA_BACKEND_PYTHON) return process.env.MAVIA_BACKEND_PYTHON;
  const candidates = [
    // This machine's shared venv (see backend/README or project memory) --
    // override with MAVIA_BACKEND_PYTHON if yours lives somewhere else.
    "k:/STUDIO/Milestone1-Jean/mavia/Scripts/python.exe",
    join(BACKEND_DIR, ".venv", WIN ? "Scripts/python.exe" : "bin/python"),
    join(BACKEND_DIR, "venv", WIN ? "Scripts/python.exe" : "bin/python"),
  ];
  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate;
  }
  return "python";
}

function killStaleBackends() {
  if (!WIN) return;
  try {
    execFileSync(
      "powershell",
      [
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | " +
          "Where-Object { $_.CommandLine -like '*manage.py runserver*' } | " +
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
      ],
      { stdio: "ignore" }
    );
  } catch {
    // Best-effort -- a fresh runserver will still bind fine even if this fails.
  }
}

function setupReverse(adb, { announce = false } = {}) {
  try {
    const devices = execFileSync(adb, ["devices"], { encoding: "utf8" })
      .split("\n")
      .slice(1)
      .filter((line) => line.trim().endsWith("device"));
    if (devices.length === 0) {
      if (announce) log("reverse", "no USB device attached yet -- will keep checking.");
      return;
    }
    for (const port of REVERSE_PORTS) {
      execFileSync(adb, ["reverse", `tcp:${port}`, `tcp:${port}`], { stdio: "ignore" });
    }
    if (announce) log("reverse", `adb reverse set for ports ${REVERSE_PORTS.join(", ")}.`);
  } catch {
    // adb not installed / daemon not up yet -- next interval tick retries.
  }
}

const COLORS = { backend: "\x1b[36m", expo: "\x1b[35m", reverse: "\x1b[33m", dev: "\x1b[32m" };
function log(label, line) {
  const color = COLORS[label] ?? "";
  console.log(`${color}[${label}]\x1b[0m ${line}`);
}

function pipePrefixed(stream, label) {
  if (!stream) return;
  readline.createInterface({ input: stream }).on("line", (line) => log(label, line));
}

// child.kill() only signals the immediate process. npx is a .cmd on
// Windows, and spawning a .cmd without shell: true throws EINVAL outright --
// so it has to go through cmd.exe, which means the process we hold a handle
// to is cmd.exe, not the real expo process it launches. Killing cmd.exe
// alone orphans that real process, which keeps running. That's almost
// certainly what's behind the whole session's recurring zombie
// `manage.py runserver` processes, not some mystery external actor: kill
// the whole tree, not just the one PID we hold a handle to.
function killTree(child) {
  if (!child || child.exitCode !== null) return;
  if (WIN) {
    try {
      execFileSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
    } catch {
      // Already gone -- fine.
    }
  } else {
    child.kill();
  }
}

// This dev loop tearing everything down the instant one child dies would
// only amplify things further if something ever does kill a child out from
// under it: every child is supervised and auto-restarts on an unexpected
// exit instead of taking the rest of the stack down with it. Only Ctrl+C
// (or a child exiting because *this* script killed it during shutdown)
// stops anything for real.
let shuttingDown = false;
const current = {};
function spawnSupervised(label, command, args, options, { restartDelayMs = 2000 } = {}) {
  function launch() {
    let child;
    try {
      child = spawn(command, args, { stdio: ["ignore", "pipe", "pipe"], ...options });
    } catch (err) {
      log("dev", `${label} failed to start (${err.message}) -- retrying in ${restartDelayMs}ms...`);
      setTimeout(launch, restartDelayMs);
      return;
    }
    current[label] = child;
    pipePrefixed(child.stdout, label);
    pipePrefixed(child.stderr, label);
    child.on("error", (err) => log("dev", `${label} error: ${err.message}`));
    child.on("exit", (code, signal) => {
      if (shuttingDown) return;
      log("dev", `${label} exited unexpectedly (code=${code} signal=${signal}) -- restarting in ${restartDelayMs}ms...`);
      setTimeout(launch, restartDelayMs);
    });
  }
  launch();
}

let reverseTimer = null;
function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  if (reverseTimer) clearInterval(reverseTimer);
  for (const child of Object.values(current)) {
    killTree(child);
  }
  setTimeout(() => process.exit(0), 300);
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

log("dev", "clearing any leftover Django dev servers...");
killStaleBackends();

const backendPython = resolveBackendPython();

if (WIFI) {
  const lan = lanAddress();
  if (!lan) {
    log("dev", "No Wi-Fi / LAN address found. Connect this PC to Wi-Fi (or the phone's hotspot) and try again.");
    process.exit(1);
  }
  const api = `http://${lan.ip}:8000/api`;
  log("dev", `Wi-Fi mode: this PC is ${lan.ip} on "${lan.name}".`);
  log("dev", `the app will call the API at ${api}`);
  log("dev", "the phone must be on the SAME Wi-Fi (or this PC on the phone's hotspot).");
  log("dev", "if Windows asks whether Python / Node may use the network, allow PRIVATE networks.");

  log("dev", `starting Django backend on 0.0.0.0:8000 (${backendPython})...`);
  spawnSupervised("backend", backendPython, ["manage.py", "runserver", "0.0.0.0:8000"], {
    cwd: BACKEND_DIR,
    env: {
      ...process.env,
      DJANGO_ALLOWED_HOSTS: `localhost,127.0.0.1,10.0.2.2,${lan.ip}`,
      CORS_ALLOWED_ORIGINS: `http://localhost:5173,http://127.0.0.1:5173,http://${lan.ip}:5173`,
    },
  });

  // --clear: EXPO_PUBLIC_* values are baked into the bundle, so a bundle
  // cached from a USB session would still call localhost.
  log("dev", "starting Expo in LAN mode -- scan the QR code / open the app on the phone...");
  spawnSupervised("expo", "npx", ["expo", "start", "--lan", "--clear"], {
    cwd: MOBILE_APP_DIR,
    shell: WIN,
    env: {
      ...process.env,
      EXPO_PUBLIC_API_BASE_URL: api,
      EXPO_PUBLIC_WEB_APP_URL: `http://${lan.ip}:5173`,
      REACT_NATIVE_PACKAGER_HOSTNAME: lan.ip,
    },
  });
} else {
  log("dev", `starting Django backend (${backendPython})...`);
  spawnSupervised("backend", backendPython, ["manage.py", "runserver", "0.0.0.0:8000"], { cwd: BACKEND_DIR });

  const adb = resolveAdb();
  log("dev", `keeping adb reverse alive for ports ${REVERSE_PORTS.join(", ")}...`);
  setupReverse(adb, { announce: true });
  reverseTimer = setInterval(() => setupReverse(adb), REVERSE_INTERVAL_MS);

  log("dev", "starting Expo...");
  spawnSupervised("expo", "npx", ["expo", "start"], { cwd: MOBILE_APP_DIR, shell: WIN });
}
