import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import {
  build,
  inspect,
  sourceIdentity,
  startArguments,
} from "../scripts/soc-frontend.mjs";

test("SOC frontend binds both serving modes to the operator-selected host", () => {
  for (const mode of ["dev", "prebuilt"]) {
    const defaults = startArguments(mode, {});
    assert.equal(defaults.includes("--hostname"), false);
    assert.deepEqual(
      startArguments(mode, { DEERFLOW_FRONTEND_HOST: "127.0.0.1" }),
      [...defaults, "--hostname", "127.0.0.1"],
    );
    assert.equal(defaults[0], mode === "dev" ? "dev" : "start");
  }
});

test("SOC prebuild keeps the current build until a complete replacement exists", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "soc-frontend-"));
  try {
    await mkdir(path.join(root, "src"));
    await mkdir(path.join(root, "node_modules"));
    await writeFile(path.join(root, "package.json"), '{"type":"module"}');
    await writeFile(path.join(root, "src", "page.tsx"), "first");
    const calls = [];
    const compile = async (directory) => {
      calls.push(directory);
      await mkdir(path.join(directory, ".next"));
      await writeFile(path.join(directory, ".next", "BUILD_ID"), "fixture");
    };
    const first = await build(root, { compile, environment: {} });
    assert.equal((await inspect(root, {})).identity, first.identity);
    await build(root, { compile, environment: {} });
    assert.equal(calls.length, 1);
    await writeFile(path.join(root, "src", "page.tsx"), "second");
    await assert.rejects(
      build(root, {
        environment: {},
        compile: async () => {
          throw new Error("failed");
        },
      }),
    );
    assert.equal(
      JSON.parse(
        await readFile(path.join(root, ".soc-frontend/current.json"), "utf8"),
      ).identity,
      first.identity,
    );
    await assert.rejects(inspect(root, {}), /changed/);
    const second = await build(root, { compile, environment: {} });
    assert.notEqual(first.identity, second.identity);
    assert.equal(
      await readFile(path.join(first.directory, "src/page.tsx"), "utf8"),
      "first",
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("SOC build identity includes public configuration but not logs or credentials", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "soc-frontend-"));
  try {
    await writeFile(path.join(root, "package.json"), "{}");
    const before = await sourceIdentity(root, {});
    await mkdir(path.join(root, ".next"));
    await writeFile(path.join(root, ".next", "trace"), "runtime");
    assert.equal(
      await sourceIdentity(root, { PRIVATE_API_KEY: "secret" }),
      before,
    );
    assert.notEqual(
      await sourceIdentity(root, { NEXT_PUBLIC_BACKEND_BASE_URL: "/changed" }),
      before,
    );
    await writeFile(
      path.join(root, ".env"),
      "NEXT_PUBLIC_STATIC_WEBSITE_ONLY=true\n",
    );
    assert.notEqual(await sourceIdentity(root, {}), before);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
