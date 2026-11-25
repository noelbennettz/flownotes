// Bundle Tiptap and all of its dependencies into one ES module.
// This virtual entry is also the public API list: static/editor.js may only import
// what is exported here, so keep both sides in sync or the browser fails at runtime.
import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

const here = dirname(fileURLToPath(import.meta.url));
const outfile = resolve(here, "../static/vendor/editor.bundle.js");

const entry = `
import { Editor } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import TaskList from "@tiptap/extension-task-list";
import TaskItem from "@tiptap/extension-task-item";
import Image from "@tiptap/extension-image";
import Placeholder from "@tiptap/extension-placeholder";
import { Markdown } from "tiptap-markdown";

export { Editor, StarterKit, TaskList, TaskItem, Image, Placeholder, Markdown };
`;

await mkdir(dirname(outfile), { recursive: true });

const result = await build({
  stdin: {
    contents: entry,
    resolveDir: here,
    sourcefile: "vendor-entry.js",
    loader: "js",
  },
  bundle: true,
  format: "esm",
  platform: "browser",
  target: ["es2020"],
  minify: true,
  legalComments: "none",
  // Use Tiptap's production branch so dev warnings are not bundled.
  define: { "process.env.NODE_ENV": '"production"' },
  outfile,
  logLevel: "info",
});

if (result.errors.length > 0) {
  process.exitCode = 1;
} else {
  console.log("Wrote", outfile);
}
