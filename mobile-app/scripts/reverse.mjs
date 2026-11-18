// Runs `adb reverse` for the Metro (8081) and Django API (8000) ports so a
// USB-connected phone can reach both at localhost. Wired into `npm start`.
// No-op (with a friendly note) when adb or a device isn't available, so it
// never blocks `expo start`.

import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";

const PORTS = [8081, 8000];

function resolveAdb() {
  const sdk = process.env.ANDROID_HOME || process.env.ANDROID_SDK_ROOT;
  if (sdk) {
    const exe = join(sdk, "platform-tools", process.platform === "win32" ? "adb.exe" : "adb");
    if (existsSync(exe)) return exe;
  }
  return "adb"; // fall back to PATH
}

const adb = resolveAdb();

try {
  const devices = execFileSync(adb, ["devices"], { encoding: "utf8" })
    .split("\n")
    .slice(1)
    .filter((line) => line.trim().endsWith("device"));

  if (devices.length === 0) {
    console.log("[reverse] no USB device attached — skipping adb reverse.");
    process.exit(0);
  }

  for (const port of PORTS) {
    execFileSync(adb, ["reverse", `tcp:${port}`, `tcp:${port}`], { stdio: "ignore" });
  }
  console.log(`[reverse] adb reverse set for ports ${PORTS.join(", ")}.`);
} catch (err) {
  console.log(`[reverse] skipped (${err.code || err.message}). Set tunnels manually if on USB.`);
}
