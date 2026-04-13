import { join, normalize } from "node:path";

import { handleApiRequest } from "./routes/api";
import { handleUpstreamRequest } from "./routes/upstream";

const port = Number(process.env.PORT ?? "3000");
const webDistDir = join(process.cwd(), "web", "dist");

Bun.serve({
  port,
  async fetch(request) {
    const url = new URL(request.url);

    try {
      const upstreamResponse = await handleUpstreamRequest(request, url.pathname);
      if (upstreamResponse) {
        return upstreamResponse;
      }

      const apiResponse = await handleApiRequest(request, url.pathname);
      if (apiResponse) {
        return apiResponse;
      }

      if (request.method !== "GET" && request.method !== "HEAD") {
        return new Response("Method Not Allowed", { status: 405 });
      }

      const staticResponse = await serveStaticFile(url.pathname);
      if (staticResponse) {
        return staticResponse;
      }

      return new Response("Not found", { status: 404 });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unexpected server error";
      return new Response(JSON.stringify({ error: message }), {
        status: 500,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
      });
    }
  },
});

console.log(`Acme inbox assistant server running at http://localhost:${port}`);

async function serveStaticFile(pathname: string): Promise<Response | undefined> {
  const safePath = pathname === "/" ? "index.html" : pathname.slice(1);
  const normalizedPath = normalize(safePath);

  if (normalizedPath.startsWith("..")) {
    return new Response("Forbidden", { status: 403 });
  }

  const candidatePath = join(webDistDir, normalizedPath);
  const candidate = Bun.file(candidatePath);

  if (await candidate.exists()) {
    return new Response(candidate, {
      headers: { "Content-Type": contentTypeForPath(candidatePath) },
    });
  }

  const indexFile = Bun.file(join(webDistDir, "index.html"));
  if (await indexFile.exists()) {
    return new Response(indexFile, {
      headers: { "Content-Type": "text/html; charset=utf-8" },
    });
  }

  if (pathname === "/") {
    return new Response(
      "Frontend assets are not built yet. Run `PATH=$HOME/.bun/bin:$PATH bun run build` or use `bun run dev` for local development.",
      {
        status: 503,
        headers: { "Content-Type": "text/plain; charset=utf-8" },
      },
    );
  }

  return undefined;
}

function contentTypeForPath(path: string): string {
  if (path.endsWith(".html")) {
    return "text/html; charset=utf-8";
  }

  if (path.endsWith(".js")) {
    return "application/javascript; charset=utf-8";
  }

  if (path.endsWith(".css")) {
    return "text/css; charset=utf-8";
  }

  if (path.endsWith(".json")) {
    return "application/json; charset=utf-8";
  }

  if (path.endsWith(".svg")) {
    return "image/svg+xml";
  }

  return "application/octet-stream";
}
