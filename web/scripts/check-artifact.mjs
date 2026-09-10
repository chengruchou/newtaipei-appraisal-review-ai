/**
 * #25 requires that the build artifact carry no token, no private endpoint and no sensitive
 * data in a source map. Those are properties of what actually gets served, so they are
 * checked against dist/ rather than asserted in a component test.
 *
 * This is a coarse net on purpose. It cannot prove the absence of a secret; it catches the
 * ways one usually arrives, which is a committed .env value or a source map shipped by an
 * accidental config change.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { extname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const dist = join(root, "dist");

const SECRET_PATTERNS = [
  { name: "AWS access key id", pattern: /\bAKIA[0-9A-Z]{16}\b/ },
  { name: "bearer token literal", pattern: /\bBearer\s+[A-Za-z0-9._-]{20,}/ },
  { name: "private key block", pattern: /-----BEGIN (?:RSA |EC )?PRIVATE KEY-----/ },
  { name: "AWS session URL", pattern: /X-Amz-Signature=/ },
  { name: "amazonaws.com host", pattern: /[a-z0-9.-]+\.amazonaws\.com/ },
  { name: "dynamodb table hint", pattern: /\bTableName\b/ },
];

function walk(directory) {
  const found = [];
  for (const entry of readdirSync(directory)) {
    const full = join(directory, entry);
    if (statSync(full).isDirectory()) {
      found.push(...walk(full));
    } else {
      found.push(full);
    }
  }
  return found;
}

let failures = 0;
function fail(message) {
  console.error(`FAIL ${message}`);
  failures += 1;
}

let files;
try {
  files = walk(dist);
} catch {
  console.error("FAIL dist/ is missing; run the build first");
  process.exit(1);
}

for (const file of files) {
  const shown = relative(root, file);
  if (extname(file) === ".map") {
    // A source map ships the original sources and any string in them.
    fail(`${shown} is a source map and must not be published`);
    continue;
  }
  if (![".js", ".mjs", ".html", ".css", ".json"].includes(extname(file))) {
    continue;
  }
  const text = readFileSync(file, "utf8");
  for (const { name, pattern } of SECRET_PATTERNS) {
    if (pattern.test(text)) {
      fail(`${shown} contains what looks like a ${name}`);
    }
  }
  if (/sourceMappingURL=(?!data:)/.test(text)) {
    fail(`${shown} references an external source map`);
  }
}

if (files.length === 0) {
  fail("dist/ is empty");
}

if (failures > 0) {
  process.exit(1);
}
console.log(`Artifact checks passed: ${files.length} files inspected, no secrets or source maps`);
