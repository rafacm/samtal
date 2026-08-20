# OpenAPI TypeScript client spike, 2026-08-20

## What this is

A spike, run once, to answer one question: can a TypeScript client for
the vinga configuration API be generated from
[`docs/reference/api-openapi.json`](../../docs/reference/api-openapi.json),
so that the admin UI of issue #129 never hand-writes a request or a
response type. It is milestone M5 of
[the governance simplification plan](../../docs/plans/2026-08-19-governance-simplification.md),
whose decision 6 says OpenAPI is the frontend client seam and asks for
this evaluation before anything is built on it.

**This is not shipped code.** Nothing here is imported by the server,
nothing is published, nothing runs in CI, and the admin UI will
generate its own client into its own tree when it exists. What survives
the spike is the recommendation, recorded in the M5 section of
[the implementation doc](../../docs/plans/2026-08-19-governance-simplification-implementation.md).
The tree is committed so a reviewer can read the generated output and
the fixtures rather than take the summary's word for it.

**The document is read, never written.** Neither sub-project
regenerates or edits `docs/reference/api-openapi.json`; it is
regenerated from the server by `uv run vinga-server config openapi` and
drift-checked in CI, and this spike is a consumer of it. No generated
file here has been edited by hand either: everything under a
`generated/` directory is exactly what the generator wrote, which is
what makes the determinism check below mean anything.

## The two sub-projects

Each is a self-contained npm project with its own pinned versions
(exact, no `^` and no `~`), its own lockfile, its own generation script
and its own strict-mode consumer fixture.

| Directory | Generator | Client runtime |
| --- | --- | --- |
| [`hey-api/`](hey-api/) | `@hey-api/openapi-ts` 0.99.0 | generated into the output, no runtime dependency |
| [`openapi-typescript/`](openapi-typescript/) | `openapi-typescript` 7.13.0 | `openapi-fetch` 0.17.0 |

Both type-check under TypeScript 5.9.3, which is what both sub-projects
pin, and both also type-check under TypeScript 7.0.2. Both run their
probe under `tsx` 4.23.12, pinned the same way.

## The consumer fixtures

A generated client that compiles can still be unusable, so each
sub-project carries a `consumer.ts` making the same six claims about
the types it was given. Five of the six are compile-time, and
`tsc --noEmit` is their whole test: there is no server.

1. **Authentication.** The bearer token every operation requires. This
   is the one claim settled by running rather than by compiling, in
   `probe.ts` below.
2. **Five entities, read and write and delete.** Providers, MCP
   servers, prompt fragments, agents, agent defaults. The agent
   defaults have no delete, because a singleton that always exists has
   no state a DELETE could reach; the fixtures assert that absence
   rather than substituting another resource for it.
3. **Typed non-2xx problem responses.** The RFC 9457 problem document
   the API answers refusals with.
4. **Optional versus nullable.** One field of each character, since
   conflating them is how a client sends `null` where the server reads
   absence and means the opposite.
5. **The provider entries' extension properties.** A provider carries
   whatever options its `type` takes, and the server passes them
   through, so a type that refused unknown keys would make the provider
   form unwritable.
6. **Operation identity.** Whether an operation is reachable under the
   `operation_id` the document gives it, over an exhaustive inventory
   of all thirty-eight, checked in both directions so an addition is as
   loud as a loss.

The claims are written in the vocabulary of
[`shared/expect.ts`](shared/expect.ts), shared because it is the same
vocabulary in both, while the fixtures themselves are separate because
the two client shapes are: one calls generated functions, the other
calls a client keyed by path template and HTTP method.

A claim that something is allowed is an annotated value, which fails to
compile when the generated type refuses it. A claim that something is
refused is a `@ts-expect-error`, which fails the run when the error it
expects does not happen: that is the direction that catches a client
typing everything as `any`. Where a claim could not be made to hold, it
is left failing with a comment saying so rather than contorted until it
passes, and the implementation doc records it as a finding.

Probe 1 is the exception to the compile-time rule, because it has to
be. What the types say about an `auth` option is not what the client
puts on the wire, and the recommendation turns on that difference, so
each sub-project also carries a `probe.ts` that runs: it injects a
fetch which records the request and answers from memory, invokes a
generated operation, and asserts the `Authorization` header it observes.
It is hermetic by construction, with no network, a host that does not
exist and a token that is a literal in the file, and it shares the
runtime vocabulary in [`shared/observe.ts`](shared/observe.ts).

## Re-running it

Node 24 and npm. From either sub-project directory:

```bash
npm ci            # install the pinned versions from the lockfile
npm run generate  # regenerate into ./generated
npm run check     # typecheck, then run the authentication probe
```

`npm run check` is `npm run typecheck` (`tsc --noEmit` over the
fixture, the probe and the whole generated output) followed by
`npm run probe` (`tsx probe.ts`). Both fail loudly: an assertion that
does not hold throws, and the exit code is what says so.

`npm run generate` overwrites `generated/` in place, so
`git status` after it is the determinism check: both generators were
run three times each while the spike was written and produced
byte-identical output every time.

To reproduce the determinism check explicitly:

```bash
cp -R generated /tmp/run1 && rm -rf generated && npm run generate
diff -r /tmp/run1 generated
```

## What it decided

The per-criterion evaluation, the findings each generator turned up,
and the recommendation for #129 are in the M5 section of
[`docs/plans/2026-08-19-governance-simplification-implementation.md`](../../docs/plans/2026-08-19-governance-simplification-implementation.md).
Read that first; this directory is its evidence.
