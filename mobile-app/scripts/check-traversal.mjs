// End-to-end check of the adaptive ruling *and* the app's traversal of it.
//
//   1. Runs backend/adaptive/test_mobile_traversal.py, which drives the real
//      student API under several answer policies and writes one JSON
//      transcript per scenario -- exactly the payloads a phone receives.
//   2. Compiles src/player/traversal.ts on its own (it imports no React, no
//      audio and no network, which is the point of it being a separate file).
//   3. Replays every transcript through it and asserts the student is never
//      stranded. See traversal-check.mjs for the invariants.
//
// Usage: node scripts/check-traversal.mjs

import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const app = resolve(here, "..");
const backend = resolve(app, "..", "backend");

// The shared venv lives with the Milestone1-Jean checkout; mavia reuses it.
const PYTHON = process.env.MAVIA_PYTHON ?? resolve(app, "..", "..", "Milestone1-Jean", "mavia", "Scripts", "python.exe");

const work = mkdtempSync(join(tmpdir(), "mavia-traversal-"));
const transcripts = join(work, "transcripts");
const built = join(work, "build");

function run(label, cmd, args, opts = {}) {
  process.stdout.write(`${label}\n`);
  try {
    execFileSync(cmd, args, { stdio: "inherit", ...opts });
  } catch (err) {
    console.error(`\n${label} failed.`);
    rmSync(work, { recursive: true, force: true });
    process.exit(err.status ?? 1);
  }
}

try {
  if (!existsSync(PYTHON)) {
    console.error(`Python not found at ${PYTHON}. Set MAVIA_PYTHON to the venv's python.exe.`);
    process.exit(1);
  }

  run(
    "1/3  recording student sessions against the real API...",
    PYTHON,
    ["manage.py", "test", "adaptive.test_mobile_traversal", "-v", "1"],
    { cwd: backend, env: { ...process.env, MAVIA_TRANSCRIPT_DIR: transcripts } }
  );

  // Emitted on its own rather than as part of the app build: the module has no
  // React/Expo imports, so plain tsc can turn it into something node runs.
  // Invoked through node directly rather than npx -- on Windows, spawning a
  // .cmd shim without a shell is rejected outright.
  run(
    "\n2/3  compiling the traversal module...",
    process.execPath,
    [
      join(app, "node_modules", "typescript", "bin", "tsc"),
      "-p", "tsconfig.traversal.json",
      "--outDir", built,
      // This step only needs to emit. Types are enforced for real by
      // `npx tsc --noEmit` over the whole app; a project this minimal cannot
      // see the ambient declarations that transitively pulls in.
      "--noCheck",
    ],
    { cwd: app }
  );

  run(
    "\n3/3  replaying them through the player's traversal...",
    process.execPath,
    [join(here, "traversal-check.mjs"), transcripts, join(built, "player", "traversal.js")],
    { cwd: app }
  );
} finally {
  rmSync(work, { recursive: true, force: true });
}
