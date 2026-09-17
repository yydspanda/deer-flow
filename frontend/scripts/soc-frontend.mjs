// SOC acceptance serving is independent of the backend's DEV/STG scope.
import { createHash, randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import {
  cp,
  mkdir,
  readFile,
  readdir,
  rename,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const INPUT_DIRS = new Set(["src", "public", "content", "scripts"]);
const IGNORED_FILES = new Set([
  "next-env.d.ts",
  "tsconfig.tsbuildinfo",
  "AGENTS.md",
  "CLAUDE.md",
  "README.md",
]);

async function inputs(root, relative = "") {
  const files = [];
  for (const item of await readdir(path.join(root, relative), {
    withFileTypes: true,
  })) {
    const name = path.join(relative, item.name);
    if (item.isDirectory() && (relative || INPUT_DIRS.has(item.name))) {
      files.push(...(await inputs(root, name)));
    } else if (
      item.isFile() &&
      !IGNORED_FILES.has(item.name) &&
      (relative || !item.name.startsWith(".") || item.name.startsWith(".env"))
    ) {
      files.push(name);
    }
  }
  return files.sort();
}

export async function sourceIdentity(root, environment = process.env) {
  const hash = createHash("sha256").update(
    `soc-frontend-v1:${process.version}:${process.platform}:${process.arch}`,
  );
  for (const file of await inputs(root)) {
    hash
      .update(file)
      .update("\0")
      .update(await readFile(path.join(root, file)))
      .update("\0");
  }
  const keys = Object.keys(environment)
    .filter(
      (key) =>
        key.startsWith("NEXT_PUBLIC_") ||
        key === "DEER_FLOW_INTERNAL_GATEWAY_BASE_URL" ||
        key === "DEER_FLOW_AUTH_DISABLED",
    )
    .sort();
  hash.update(JSON.stringify(keys.map((key) => [key, environment[key]])));
  return hash.digest("hex");
}

const cacheRoot = (root) => path.join(root, ".soc-frontend");

export async function inspect(root = ROOT, environment = process.env) {
  const manifest = JSON.parse(
    await readFile(path.join(cacheRoot(root), "current.json"), "utf8"),
  );
  if (
    !/^[a-f0-9]{64}$/.test(manifest.identity) ||
    manifest.identity !== (await sourceIdentity(root, environment))
  ) {
    throw new Error(
      "Frontend source/config changed; run the SOC frontend build before starting.",
    );
  }
  if (path.dirname(manifest.directory) !== cacheRoot(root))
    throw new Error("Invalid SOC build directory");
  await readFile(path.join(manifest.directory, ".next", "BUILD_ID"), "utf8");
  return manifest;
}

function runNext(root, directory, args, environment) {
  return new Promise((resolve, reject) => {
    const child = spawn(
      process.execPath,
      [path.join(root, "node_modules/next/dist/bin/next"), ...args],
      {
        cwd: directory,
        env: {
          ...environment,
          NODE_ENV: args[0] === "dev" ? "development" : "production",
          NEXT_TELEMETRY_DISABLED: "1",
        },
        stdio: "inherit",
      },
    );
    const interrupt = () => child.kill("SIGINT");
    const terminate = () => child.kill("SIGTERM");
    process.on("SIGINT", interrupt);
    process.on("SIGTERM", terminate);
    child.on("error", reject);
    child.on("exit", (code, signal) => {
      process.off("SIGINT", interrupt);
      process.off("SIGTERM", terminate);
      if (code === 0) resolve();
      else reject(new Error(`Next ${args[0]} exited: ${signal ?? code}`));
    });
  });
}

export async function build(
  root = ROOT,
  { environment = process.env, compile } = {},
) {
  try {
    const existing = await inspect(root, environment);
    console.log(
      `SOC frontend build unchanged: ${existing.identity.slice(0, 12)}`,
    );
    return existing;
  } catch {
    /* Publish only after the next build succeeds. */
  }
  const identity = await sourceIdentity(root, environment);
  const directory = path.join(
    cacheRoot(root),
    `${identity.slice(0, 16)}-${randomUUID()}`,
  );
  await mkdir(directory, { recursive: true, mode: 0o700 });
  try {
    for (const file of await inputs(root)) {
      const target = path.join(directory, file);
      await mkdir(path.dirname(target), { recursive: true });
      await cp(path.join(root, file), target);
    }
    await symlink(
      path.join(root, "node_modules"),
      path.join(directory, "node_modules"),
      "dir",
    );
    await (compile
      ? compile(directory)
      : runNext(root, directory, ["build", "--webpack"], environment));
    await readFile(path.join(directory, ".next", "BUILD_ID"), "utf8");
    if (identity !== (await sourceIdentity(root, environment)))
      throw new Error(
        "Frontend changed during build; retry without editing source.",
      );
    const manifest = {
      schema_version: "soc.frontend_build.v1",
      identity,
      directory,
      created_at: new Date().toISOString(),
    };
    const temporary = path.join(
      cacheRoot(root),
      `current-${randomUUID()}.json`,
    );
    await writeFile(temporary, JSON.stringify(manifest, null, 2), {
      mode: 0o600,
    });
    await rename(temporary, path.join(cacheRoot(root), "current.json"));
    console.log(`SOC frontend build ready: ${identity.slice(0, 12)}`);
    return manifest;
  } catch (error) {
    await rm(directory, { recursive: true, force: true });
    throw error;
  }
}

async function main() {
  const action = process.argv[2] ?? "inspect";
  if (action === "build") await build();
  else if (action === "inspect")
    console.log(JSON.stringify(await inspect(), null, 2));
  else if (action === "start") {
    const mode = process.env.SOC_FRONTEND_MODE ?? "prebuilt";
    if (mode === "dev")
      await runNext(
        ROOT,
        ROOT,
        ["dev", "--webpack", "--port", "3000"],
        process.env,
      );
    else if (mode === "prebuilt") {
      const manifest = await inspect();
      console.log(
        `SOC frontend prebuilt: ${manifest.identity.slice(0, 12)} (no on-demand compilation)`,
      );
      await runNext(
        ROOT,
        manifest.directory,
        ["start", "--port", "3000"],
        process.env,
      );
    } else throw new Error("SOC_FRONTEND_MODE must be prebuilt or dev");
  } else
    throw new Error(
      "Usage: node frontend/scripts/soc-frontend.mjs build|inspect|start",
    );
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
