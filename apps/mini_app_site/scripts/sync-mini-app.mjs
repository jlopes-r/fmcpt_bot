import { copyFile, mkdir, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const sourceDirectory = resolve(scriptDirectory, "..", "..", "mini_app");
const targetDirectory = resolve(scriptDirectory, "..", "public", "mini");
const files = [
  "index.html",
  "styles.css",
  "app.js",
  "catalog.json",
  "catalog-data.js",
  "og.png",
];

await rm(targetDirectory, { recursive: true, force: true });
await mkdir(targetDirectory, { recursive: true });

await Promise.all(
  files.map((file) =>
    copyFile(resolve(sourceDirectory, file), resolve(targetDirectory, file)),
  ),
);
