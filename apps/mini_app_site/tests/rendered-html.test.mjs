import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html", host: "localhost" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("renders branded handoff to the Mini App", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>FMCPT \| Central de comandos<\/title>/i);
  assert.match(html, /Abrindo a central de comandos/);
  assert.match(html, /\/mini\/index\.html/);
  assert.match(html, /\/mini\/og\.png/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton/i);
});

test("build contains the complete static Mini App", async () => {
  const files = [
    "index.html",
    "styles.css",
    "app.js",
    "catalog.json",
    "og.png",
  ];

  await Promise.all(
    files.map((file) => access(new URL(`../public/mini/${file}`, import.meta.url))),
  );

  const html = await readFile(
    new URL("../public/mini/index.html", import.meta.url),
    "utf8",
  );
  assert.match(html, /id="commandList"/);
  assert.match(html, /id="adminView"/);
  assert.doesNotMatch(html, /catalog-data\.js/);
});
