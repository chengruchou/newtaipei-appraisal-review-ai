// Isolated synthetic browser rehearsal server. Never deploy this fault-injection proxy.
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname } from "node:path";

const fixture = JSON.parse(await readFile(process.env.REVIEW_BROWSER_FIXTURE, "utf8"));
const upstream = new URL(fixture.api_base_url);
if (upstream.protocol !== "http:" || !["localhost", "127.0.0.1"].includes(upstream.hostname)) {
  throw new Error("Browser rehearsal requires a configured loopback API");
}
const root = resolve("dist");
let dropPath = null;
const mime = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css" };
createServer(async (request, response) => {
  try {
    const url = new URL(request.url, "http://127.0.0.1:4174");
    if (url.pathname === "/__test__/drop-next-response" && request.method === "POST" && request.headers["x-test-control"] === "local-rehearsal") {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const { task_id } = JSON.parse(Buffer.concat(chunks).toString());
      dropPath = `/v1/review-tasks/${encodeURIComponent(task_id)}/responses`;
      response.writeHead(204).end();
      return;
    }
    if (url.pathname.startsWith("/v1/")) {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const body = Buffer.concat(chunks);
      const headers = { accept: request.headers.accept ?? "application/json" };
      if (request.headers.authorization) headers.authorization = request.headers.authorization;
      if (body.length) headers["content-type"] = "application/json";
      const result = await fetch(new URL(url.pathname + url.search, upstream), {
        method: request.method, headers, ...(body.length ? { body } : {}),
      });
      // Consume the real response before faulting: the upstream write really ran.
      const bytes = Buffer.from(await result.arrayBuffer());
      response.writeHead(result.status, { "content-type": result.headers.get("content-type") ?? "application/json", "content-length": bytes.length });
      if (request.method === "POST" && url.pathname === dropPath && result.ok) {
        dropPath = null;
        response.flushHeaders();
        response.write(bytes.subarray(0, 1));
        setTimeout(() => response.destroy(), 50);
        return;
      }
      response.end(bytes);
      return;
    }
    const file = resolve(root, "." + decodeURIComponent(url.pathname));
    if (!file.startsWith(root + "/") && file !== root) {
      response.writeHead(404).end();
      return;
    }
    let bytes;
    let type;
    try { bytes = await readFile(file); type = mime[extname(file)] ?? "application/octet-stream"; }
    catch { bytes = await readFile(resolve(root, "index.html")); type = "text/html"; }
    response.writeHead(200, { "content-type": type, "cache-control": "no-store" }).end(bytes);
  } catch {
    response.writeHead(502, { "content-type": "application/json" }).end('{"code":"execution_failed"}');
  }
}).listen(4174, "127.0.0.1");
