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
let publicPrivacyBase = null;
if (process.env.PRIVACY_BROWSER_FIXTURE) {
  const privateFixture = JSON.parse(await readFile(process.env.PRIVACY_BROWSER_FIXTURE, "utf8"));
  const configured = new URL(privateFixture.bridge_url);
  if (configured.protocol !== "http:" || !["127.0.0.1", "[::1]"].includes(configured.hostname) ||
      configured.username || configured.password || configured.pathname !== "/" || configured.search || configured.hash) {
    throw new Error("Privacy rehearsal requires a configured numeric loopback origin");
  }
  publicPrivacyBase = configured.origin;
}
let dropPath = null;
let gatewayFault = null;
let lastFaultReceipt = null;
const mime = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css" };
createServer(async (request, response) => {
  try {
    const url = new URL(request.url, "http://127.0.0.1:4174");
    if (url.pathname === "/local-config.json") {
      if (request.method !== "GET" || url.search || !publicPrivacyBase) {
        response.writeHead(404).end();
      } else {
        response.writeHead(200, { "content-type": "application/json", "cache-control": "no-store" })
          .end(JSON.stringify({ privacy_bridge_base: publicPrivacyBase }));
      }
      return;
    }
    if (url.pathname === "/__test__/gateway-next-response" && request.method === "POST" && request.headers["x-test-control"] === "local-rehearsal") {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const { task_id, status } = JSON.parse(Buffer.concat(chunks).toString());
      if (!Object.values(fixture.tasks).includes(task_id) || ![0, 502, 504].includes(status)) {
        response.writeHead(400).end();
        return;
      }
      gatewayFault = { path: `/v1/review-tasks/${encodeURIComponent(task_id)}/responses`, status };
      response.writeHead(204).end();
      return;
    }
    if (url.pathname === "/__test__/last-fault-receipt" && request.method === "GET" && request.headers["x-test-control"] === "local-rehearsal") {
      response.writeHead(lastFaultReceipt === null ? 404 : 200, { "content-type": "application/json" }).end(lastFaultReceipt);
      return;
    }
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
      const privacyRequestId = result.headers.get("x-privacy-request-id");
      if (privacyRequestId) response.setHeader("X-Privacy-Request-Id", privacyRequestId);
      if (request.method === "POST" && url.pathname === gatewayFault?.path && result.ok) {
        const { status } = gatewayFault;
        gatewayFault = null;
        lastFaultReceipt = bytes;
        if (status === 0) {
          response.writeHead(200, { "content-type": "application/json", "content-length": bytes.length });
          response.flushHeaders();
          response.write(bytes.subarray(0, 1));
          setTimeout(() => response.destroy(), 50);
        } else {
          response.writeHead(status, { "content-type": "text/html" }).end("<html>Gateway response unavailable</html>");
        }
        return;
      }
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
