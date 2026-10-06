// End-to-end check of the audio course package: the app's real traversal
// (src/player/traversal.ts) driven against the real backend.
//
//   1. Copies backend/db.sqlite3 to a temp file, migrates the copy and runs a
//      Django server on it -- your real database is never touched.
//   2. Creates three test students enrolled in the topic's course.
//   3. Compiles src/player/traversal.ts on its own (it imports no React, no
//      audio and no network, which is the point of it being a separate file).
//   4. Walks each student through the whole topic exactly as the phone does --
//      open the package, play every step, answer, follow each command,
//      continue past listen-only steps -- under three answer policies:
//      always right, always wrong, and wrong on the first try.
//   5. Asserts every audio clip downloads, nobody is ever stranded, every
//      walk completes, and the app's position always matches the server's.
//
// Usage: node scripts/check-traversal.mjs [topicId] [--republish]   (default topic 122)
//   --republish  mark the topic published in the temporary copy first (for a
//                topic a migration unpublished pending teacher review)

import { execFileSync, spawn } from "node:child_process";
import { copyFileSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const app = resolve(here, "..");
const backend = resolve(app, "..", "backend");
// The shared venv lives with the Milestone1-Jean checkout; mavia reuses it.
const PYTHON = process.env.MAVIA_PYTHON ?? resolve(app, "..", "..", "Milestone1-Jean", "mavia", "Scripts", "python.exe");
const TOPIC = Number(process.argv.slice(2).find((arg) => /^\d+$/.test(arg)) ?? 122);
const REPUBLISH = process.argv.includes("--republish");
const PORT = 8765;
const API = `http://127.0.0.1:${PORT}/api`;
const PASSWORD = "check-traversal-pw";
const POLICIES = ["always_right", "always_wrong", "wrong_first_try"];

const work = mkdtempSync(join(tmpdir(), "mavia-check-"));
const db = join(work, "db.sqlite3").replace(/\\/g, "/");
const env = { ...process.env, DATABASE_URL: `sqlite:///${db}`, PYTHONIOENCODING: "utf-8" };
let server = null;
let failures = 0;

function python(args, opts = {}) {
  return execFileSync(PYTHON, args, { cwd: backend, env, encoding: "utf-8", ...opts });
}

function check(ok, message) {
  if (!ok) {
    failures += 1;
    console.log(`   FAIL  ${message}`);
  }
}

async function api(path, { token, method = "GET", body } = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Token ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = res.status === 204 ? null : await res.json();
  if (!res.ok) throw new Error(`${method} ${path} -> ${res.status} ${JSON.stringify(data)}`);
  return data;
}

async function waitForServer() {
  for (let i = 0; i < 120; i += 1) {
    try {
      await fetch(`${API}/auth/me/`);
      return;
    } catch {
      await new Promise((r) => setTimeout(r, 500));
    }
  }
  throw new Error("Django server did not start");
}

// One student's walk through the topic, the way the phone does it.
async function walk(T, token, policy, answers) {
  const pkg = await api(`/mobile/topics/${TOPIC}/`, { token });
  let state = T.applyPackage(pkg);
  const tried = new Set();
  // A missed True/False must never be asked again: the other answer is certain.
  const missedTrueFalse = new Set();
  const log = [];

  for (let turn = 0; turn < 400; turn += 1) {
    if (state.phase === "done") return { completed: true, log, turns: turn };

    if (state.phase === "audio") {
      // Every clip must be playable by the device.
      for (const track of T.tracksFor(state)) {
        const url = `http://127.0.0.1:${PORT}${track.audio_url}`;
        const res = await fetch(url, { method: "HEAD" });
        check(res.ok, `audio clip ${track.audio_url} -> ${res.status}`);
      }
      state = T.afterAudio(state);
      continue;
    }

    if (state.phase === "continue") {
      const { next } = await api(`/mobile/topics/${TOPIC}/continue/`, { token, method: "POST" });
      log.push(`step ${state.position}: listened -> ${next.action} ${next.next_step_position ?? ""}`);
      state = T.applyCommand(state, next);
      continue;
    }

    // phase === "questions"
    const question = T.questionsFor(state)[state.questionIndex];
    check(Boolean(question), `step ${state.position} is in "questions" with no question to ask`);
    if (!question) return { completed: false, log, turns: turn };

    check(!missedTrueFalse.has(question.id), `Q${question.id}: a missed True/False was asked again`);
    const key = answers[String(question.id)];
    const firstTry = !tried.has(question.id);
    tried.add(question.id);
    const right =
      policy === "always_right" || (policy === "wrong_first_try" && !firstTry);
    const res = await api("/mobile/answers/", {
      token,
      method: "POST",
      body: { topic_id: TOPIC, question_id: question.id, selected_answer: right ? key.right : key.wrong },
    });
    check(res.is_correct === right, `Q${question.id}: expected is_correct=${right}, got ${res.is_correct}`);
    if (!right && key.format === "TF") missedTrueFalse.add(question.id);
    log.push(
      `step ${state.position} Q${question.id} ${right ? "right" : "wrong"} -> ${res.next.action}` +
        (res.next.next_step_position ? ` step ${res.next.next_step_position}` : "") +
        (res.next.action === "escalate_variant" ? ` (${res.next.next_variant})` : "")
    );
    state = T.applyCommand(state, res.next);

    // The app and the server must agree on where the student is.
    const server = (await api(`/mobile/topics/${TOPIC}/`, { token })).progress;
    if (state.phase !== "done") {
      check(server.current_step_position === state.position,
        `app at step ${state.position}, server at ${server.current_step_position}`);
      check(server.current_variant === state.variant,
        `app on ${state.variant}, server on ${server.current_variant}`);
    } else {
      check(server.completed, "app finished but the server did not mark the topic completed");
    }
  }
  return { completed: false, log, turns: 400 };
}

try {
  console.log(`1/4  copying the database and starting a server on it (topic ${TOPIC})...`);
  copyFileSync(join(backend, "db.sqlite3"), db);
  python(["manage.py", "migrate", "--noinput"], { stdio: "ignore" });

  // Three fresh, verified, enrolled students on the copy.
  const setup = `
from user.models import User
from adaptive.models import Enrollment
from lessons.models import OutlineNode
course_id = OutlineNode.objects.get(pk=${TOPIC}).course_id
for name in ${JSON.stringify(POLICIES)}:
    u, _ = User.objects.get_or_create(username="check_" + name, defaults={"role": "STUDENT", "email": name + "@check.local"})
    u.role, u.is_verified, u.is_active = "STUDENT", True, True
    u.set_password("${PASSWORD}"); u.save()
    Enrollment.objects.get_or_create(student=u, course_id=course_id)
`;
  python(["manage.py", "shell", "-c", setup]);
  if (REPUBLISH) {
    // The copy only -- the real database is never touched.
    python(["manage.py", "shell", "-c",
      `from django.utils import timezone\nfrom lessons.models import OutlineNode\n` +
      `OutlineNode.objects.filter(pk=${TOPIC}).update(published=True, published_at=timezone.now())`]);
  }

  server = spawn(PYTHON, ["manage.py", "runserver", `127.0.0.1:${PORT}`, "--noreload"], {
    cwd: backend, env, stdio: "ignore",
  });
  await waitForServer();

  console.log("2/4  compiling the traversal module...");
  const built = join(work, "build");
  execFileSync(process.execPath, [resolve(app, "node_modules", "typescript", "bin", "tsc"),
    "-p", resolve(app, "tsconfig.traversal.json"), "--outDir", built], { stdio: "inherit" });
  writeFileSync(join(built, "package.json"), '{"type": "module"}');
  const T = await import(pathToFileURL(join(built, "player", "traversal.js")).href);

  console.log("3/4  logging in and building the package...");
  const tokens = {};
  for (const policy of POLICIES) {
    tokens[policy] = (await api("/auth/login/", {
      method: "POST", body: { username: `check_${policy}`, password: PASSWORD },
    })).token;
  }
  await api(`/mobile/topics/${TOPIC}/`, { token: tokens.always_right });   // builds TopicPackage

  // The answer key never reaches the phone; the harness reads it from the
  // server side so it can answer right or wrong on purpose.
  const answers = JSON.parse(python(["manage.py", "shell", "-c", `
import json
from adaptive.services import normalize
from mobile_course_package.models import TopicPackage
key = TopicPackage.objects.get(topic_id=${TOPIC}).answer_key
out = {}
for qid, q in key.items():
    right = normalize(q, q["correct_answer"])
    options = ["true", "false"] if q["format"] == "TF" else ["a", "b", "c", "d"]
    out[qid] = {"right": right, "wrong": next(o for o in options if o != right), "format": q["format"]}
print(json.dumps(out))
`]).trim().split("\n").pop());

  console.log("4/4  walking the topic under each answer policy...\n");
  for (const policy of POLICIES) {
    const before = failures;
    const result = await walk(T, tokens[policy], policy, answers);
    check(result.completed, `${policy}: the walk never completed (stranded after ${result.turns} turns)`);
    const actions = {};
    for (const line of result.log) {
      const action = line.split("-> ")[1]?.split(" ")[0];
      actions[action] = (actions[action] ?? 0) + 1;
    }
    console.log(`   ${failures === before ? "PASS" : "FAIL"}  ${policy}: ${result.log.length} interactions, ` +
      `completed=${result.completed}  ${JSON.stringify(actions)}`);
    if (process.env.VERBOSE) result.log.forEach((line) => console.log(`         ${line}`));
  }
} finally {
  if (server) server.kill();
  try {
    rmSync(work, { recursive: true, force: true });
  } catch {
    // the server may still hold the db file for a moment on Windows
  }
}

console.log(failures ? `\n${failures} check(s) failed.` : "\nAll checks passed.");
process.exit(failures ? 1 : 0);
