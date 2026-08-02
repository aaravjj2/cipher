import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { existsSync } from "node:fs";

const appRoot = dirname(fileURLToPath(import.meta.url));
const projectRoot = dirname(appRoot);
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function resolvePython() {
  if (process.env.PYTHON) return process.env.PYTHON;
  return "python3";
}

const corePort = process.env.CIPHER_CORE_PORT || "8282";
const webPort = process.env.PORT || "8283";
const coreHealth = `http://127.0.0.1:${corePort}/health`;

async function coreAvailable() {
  try {
    return (await fetch(coreHealth)).ok;
  } catch {
    return false;
  }
}

const python = resolvePython();
const coreScript = join(projectRoot, "core", "app.py");
if (!existsSync(coreScript)) {
  console.error("Missing core service at", coreScript);
  process.exit(1);
}

let core;
let web;

if (!(await coreAvailable())) {
  core = spawn(python, ["-u", coreScript], {
    stdio: "inherit",
    windowsHide: true,
    env: { ...process.env, CIPHER_CORE_PORT: corePort },
  });
  for (let i = 0; i < 40; i++) {
    await wait(250);
    if (await coreAvailable()) break;
  }
}

web = spawn(process.execPath, [join(appRoot, "server.mjs")], {
  stdio: "inherit",
  windowsHide: true,
  env: {
    ...process.env,
    PORT: webPort,
    CIPHER_CORE_URL: process.env.CIPHER_CORE_URL || `http://127.0.0.1:${corePort}`,
  },
});

const shutdown = () => {
  core?.kill();
  web?.kill();
  process.exit();
};
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, shutdown);
