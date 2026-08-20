/**
 * The runtime authentication probe for the Hey API client.
 *
 * `consumer.ts` proves what the generated types say. This proves what
 * the generated code does: that an operation carrying the document's
 * bearer scheme really puts `Authorization: Bearer <token>` on the
 * wire, and that it is the security declaration plus the `auth` option
 * that put it there rather than anything the consumer wrote.
 *
 * Run with `npm run probe`. No network: the fetch is injected, the host
 * does not exist, and the token is the literal below.
 */

import { createClient, createConfig } from "./generated/client";
import type { ClientOptions } from "./generated/types.gen";
import { readProviderProvidersStageNameGet } from "./generated/sdk.gen";
import { only, recordingFetch, same } from "../shared/observe";
import type { Seen } from "../shared/observe";

const TOKEN = "spike-token";
const BASE = "https://vinga.example/api";
const ENVELOPE = { entity: { type: "anthropic" }, secrets: {} };

const readOneProvider = async (seen: Seen[], auth: boolean): Promise<void> => {
  const client = createClient(
    createConfig<ClientOptions>({
      baseUrl: BASE,
      fetch: recordingFetch(seen, ENVELOPE),
      ...(auth ? { auth: () => TOKEN } : {}),
    }),
  );
  await readProviderProvidersStageNameGet({
    client,
    path: { stage: "llm", name: "main" },
  });
};

const run = async (): Promise<void> => {
  console.log("hey-api: a client configured with a token");
  const authenticated: Seen[] = [];
  await readOneProvider(authenticated, true);
  const withToken = only(authenticated);
  same("method", withToken.method, "GET");
  same("url", withToken.url, `${BASE}/providers/llm/main`);
  same("authorization", withToken.authorization, `Bearer ${TOKEN}`);

  // The header is not a fixed part of the request the generated
  // function builds: drop the token and it is gone, which is what makes
  // the assertion above evidence that the `auth` option is what carries
  // it rather than a constant somewhere in the client.
  console.log("hey-api: the same operation on a client with no token");
  const anonymous: Seen[] = [];
  await readOneProvider(anonymous, false);
  const withoutToken = only(anonymous);
  same("url", withoutToken.url, `${BASE}/providers/llm/main`);
  same("authorization", withoutToken.authorization, null);

  console.log("hey-api: authentication probe passed");
};

await run();
