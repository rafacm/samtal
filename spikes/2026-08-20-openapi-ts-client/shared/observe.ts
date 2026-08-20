/**
 * The runtime vocabulary both probes are written in.
 *
 * `expect.ts` states what the generated types say. This states what the
 * generated code does, which is a different question and the one the
 * authentication criterion actually turns on: a fixture can annotate an
 * `auth` option all day without ever proving that a request carries an
 * `Authorization` header. So each sub-spike also runs, once, against a
 * fetch that answers from memory.
 *
 * Hermetic by construction. The injected fetch never reaches the
 * network, the base URL names a host that does not exist, and the token
 * is a literal in the file. Nothing here needs a server, and nothing
 * here may acquire one.
 */

/** What the injected fetch saw, reduced to the facts a probe asserts. */
export interface Seen {
  readonly method: string;
  readonly url: string;
  readonly authorization: string | null;
}

/**
 * A fetch that records what it was asked and answers `body` as JSON.
 *
 * The recorded array is the probe's observation window: it is read
 * after the call, so an assertion names a request that really happened
 * rather than one the probe hoped for.
 */
export const recordingFetch = (
  seen: Seen[],
  body: unknown,
): typeof fetch => {
  return async (input, init) => {
    const request =
      input instanceof Request ? input : new Request(input, init);
    seen.push({
      method: request.method,
      url: request.url,
      authorization: request.headers.get("authorization"),
    });
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
};

/** The one request the window holds, or a failure naming what it holds instead. */
export const only = (seen: Seen[]): Seen => {
  if (seen.length !== 1) {
    throw new Error(`expected exactly one request, saw ${seen.length}`);
  }
  const [request] = seen;
  if (request === undefined) {
    throw new Error("expected exactly one request, saw none");
  }
  return request;
};

/** Fails the run when `actual` is not `expected`, naming both. */
export const same = (what: string, actual: unknown, expected: unknown): void => {
  if (actual !== expected) {
    throw new Error(
      `${what}: expected ${JSON.stringify(expected)}, observed ${JSON.stringify(actual)}`,
    );
  }
  console.log(`  ok  ${what}: ${JSON.stringify(actual)}`);
};
