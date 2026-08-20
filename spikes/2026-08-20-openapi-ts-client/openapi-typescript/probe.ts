/**
 * The runtime authentication probe for openapi-fetch.
 *
 * The counterpart of the Hey API probe, and the reason the two
 * candidates are not judged equal on authentication. openapi-fetch
 * knows nothing about the document's security schemes, so the same
 * request carries no `Authorization` header until a middleware the
 * consumer wrote adds one. Both halves are observed here.
 *
 * Run with `npm run probe`. No network: the fetch is injected, the host
 * does not exist, and the token is the literal below.
 */

import createClient from "openapi-fetch";
import type { Middleware } from "openapi-fetch";
import type { paths } from "./generated/api";
import { only, recordingFetch, same } from "../shared/observe";
import type { Seen } from "../shared/observe";

const TOKEN = "spike-token";
const BASE = "https://vinga.example/api";
const ENVELOPE = { entity: { type: "anthropic" }, secrets: {} };

const bearer: Middleware = {
  onRequest({ request }) {
    request.headers.set("Authorization", `Bearer ${TOKEN}`);
    return request;
  },
};

const readOneProvider = async (
  seen: Seen[],
  middleware: boolean,
): Promise<void> => {
  const client = createClient<paths>({
    baseUrl: BASE,
    fetch: recordingFetch(seen, ENVELOPE),
  });
  if (middleware) {
    client.use(bearer);
  }
  await client.GET("/providers/{stage}/{name}", {
    params: { path: { stage: "llm", name: "main" } },
  });
};

const run = async (): Promise<void> => {
  // The client a consumer gets by following the generated types and
  // nothing else. It is fully typed, it compiles, and it is
  // unauthenticated.
  console.log("openapi-fetch: a client built from the types alone");
  const bare: Seen[] = [];
  await readOneProvider(bare, false);
  const withoutMiddleware = only(bare);
  same("method", withoutMiddleware.method, "GET");
  same("url", withoutMiddleware.url, `${BASE}/providers/llm/main`);
  same("authorization", withoutMiddleware.authorization, null);

  // The header appears only once the consumer writes the scheme and the
  // prefix out by hand. Nothing generated said either word.
  console.log("openapi-fetch: the same client with the hand-written middleware");
  const middlewared: Seen[] = [];
  await readOneProvider(middlewared, true);
  const withMiddleware = only(middlewared);
  same("url", withMiddleware.url, `${BASE}/providers/llm/main`);
  same("authorization", withMiddleware.authorization, `Bearer ${TOKEN}`);

  console.log("openapi-fetch: authentication probe passed");
};

await run();
