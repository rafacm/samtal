/**
 * The strict-mode consumer fixture for the Hey API client.
 *
 * Nothing here runs. `npx tsc --noEmit` is the test: every probe is a
 * claim about the generated types that either compiles or does not.
 * The probes are the six the plan names, in this order.
 *
 *   1. Authentication, the bearer token the whole API requires.
 *   2. A read, a write and a delete for each of the five entities.
 *   3. Typed non-2xx problem responses, the RFC 9457 shape.
 *   4. Optional versus nullable, on a field of each character.
 *   5. The provider entries' extension properties.
 *   6. Every operation reachable under its operation_id name.
 *
 * A probe that cannot be made to hold is left failing with a comment
 * saying so, not contorted until it passes. The implementation doc
 * records what each one showed.
 */

import { createClient, createConfig } from "./generated/client";
import type { ClientOptions } from "./generated/types.gen";
import {
  readProviderProvidersStageNameGet,
  writeProviderProvidersStageNamePut,
  removeProviderProvidersStageNameDelete,
  readMcpServerMcpServersNameGet,
  writeMcpServerMcpServersNamePut,
  removeMcpServerMcpServersNameDelete,
  readPromptFragmentPromptFragmentsNameGet,
  writePromptFragmentPromptFragmentsNamePut,
  removePromptFragmentPromptFragmentsNameDelete,
  readAgentAgentsNameGet,
  writeAgentAgentsNamePut,
  removeAgentAgentsNameDelete,
  readAgentDefaultsAgentDefaultsGet,
  writeAgentDefaultsAgentDefaultsPut,
  removeDefaultAgentDefaultAgentDelete,
} from "./generated/sdk.gen";
import type {
  Acknowledgement,
  AgentConfig,
  AgentDefaults,
  DefaultAgent,
  Envelope,
  McpServerConfig,
  Problem,
  PromptFragmentConfig,
  ProviderConfig,
  ReadProviderProvidersStageNameGetErrors,
  WriteProviderProvidersStageNamePutErrors,
} from "./generated/types.gen";
import type { Equals, Expect, Nullable, Optional } from "../shared/expect";
import { holds } from "../shared/expect";

// ---------------------------------------------------------------------
// Probe 1: authentication
// ---------------------------------------------------------------------
//
// The document declares one security scheme, an HTTP bearer token, and
// applies it to every operation. The client takes the token as a config
// value and the generated SDK carries the scheme per operation, so a
// consumer never writes the word `Authorization` and never spells the
// `Bearer ` prefix itself.

declare const storedToken: string | undefined;

const api = createClient(
  createConfig<ClientOptions>({
    baseUrl: "https://vinga.example/api",
    auth: () => storedToken,
  }),
);

// The token may also arrive per call, and asynchronously, which is what
// an admin UI holding a session needs.
const perCall = { client: api, auth: async () => "the-token" } as const;

// ---------------------------------------------------------------------
// Probe 2: read, write and delete for the five entities
// ---------------------------------------------------------------------
//
// Every call below is annotated with the type it must return. The
// annotation is the assertion: a client that typed its responses as
// `unknown` would fail to compile here.

async function providerRoundTrip(): Promise<void> {
  const read = await readProviderProvidersStageNameGet({
    ...perCall,
    path: { stage: "llm", name: "main" },
  });
  holds<Envelope | undefined>(read.data);
  holds<Problem | undefined>(read.error);

  // `holds` would also accept `any`, so the two branches are pinned
  // exactly once, here, where the answer is at its most interesting.
  const dataIsExactly: Expect<Equals<typeof read.data, Envelope | undefined>> =
    true;
  const errorIsExactly: Expect<
    Equals<typeof read.error, Problem | undefined>
  > = true;
  holds<[true, true]>([dataIsExactly, errorIsExactly]);

  const written = await writeProviderProvidersStageNamePut({
    ...perCall,
    path: { stage: "llm", name: "main" },
    body: { type: "anthropic", api_key_env: "ANTHROPIC_API_KEY" },
  });
  holds<Acknowledgement | undefined>(written.data);

  const removed = await removeProviderProvidersStageNameDelete({
    ...perCall,
    path: { stage: "llm", name: "main" },
  });
  holds<Acknowledgement | undefined>(removed.data);
}

async function mcpServerRoundTrip(): Promise<void> {
  const read = await readMcpServerMcpServersNameGet({
    ...perCall,
    path: { name: "clock" },
  });
  holds<Envelope | undefined>(read.data);

  const written = await writeMcpServerMcpServersNamePut({
    ...perCall,
    path: { name: "clock" },
    body: { transport: "stdio", command: "uvx", args: ["mcp-clock"] },
  });
  holds<Acknowledgement | undefined>(written.data);

  const removed = await removeMcpServerMcpServersNameDelete({
    ...perCall,
    path: { name: "clock" },
  });
  holds<Acknowledgement | undefined>(removed.data);
}

async function promptFragmentRoundTrip(): Promise<void> {
  const read = await readPromptFragmentPromptFragmentsNameGet({
    ...perCall,
    path: { name: "house-rules" },
  });
  holds<Envelope | undefined>(read.data);

  const written = await writePromptFragmentPromptFragmentsNamePut({
    ...perCall,
    path: { name: "house-rules" },
    body: { text: "Answer in the language you were addressed in." },
  });
  holds<Acknowledgement | undefined>(written.data);

  const removed = await removePromptFragmentPromptFragmentsNameDelete({
    ...perCall,
    path: { name: "house-rules" },
  });
  holds<Acknowledgement | undefined>(removed.data);
}

async function agentRoundTrip(): Promise<void> {
  const read = await readAgentAgentsNameGet({
    ...perCall,
    path: { name: "kitchen" },
  });
  holds<Envelope | undefined>(read.data);

  const written = await writeAgentAgentsNamePut({
    ...perCall,
    path: { name: "kitchen" },
    body: { llm: "main", prompt: "You are the kitchen assistant." },
  });
  holds<Acknowledgement | undefined>(written.data);

  const removed = await removeAgentAgentsNameDelete({
    ...perCall,
    path: { name: "kitchen" },
  });
  holds<Acknowledgement | undefined>(removed.data);
}

async function agentDefaultsRoundTrip(): Promise<void> {
  const read = await readAgentDefaultsAgentDefaultsGet(perCall);
  holds<Envelope | undefined>(read.data);

  const written = await writeAgentDefaultsAgentDefaultsPut({
    ...perCall,
    body: { llm: "main", tts: "voice" },
  });
  holds<Acknowledgement | undefined>(written.data);

  // The agent defaults have no DELETE of their own: the document
  // declares GET and PUT on `/agent-defaults` and nothing else, because
  // the defaults are a singleton that always exists. The nearest delete
  // in the same family is the one that clears the default agent, and it
  // is the operation an admin UI's "clear this" control would call.
  const cleared = await removeDefaultAgentDefaultAgentDelete(perCall);
  holds<Acknowledgement | undefined>(cleared.data);
}

// ---------------------------------------------------------------------
// Probe 3: typed non-2xx problem responses
// ---------------------------------------------------------------------
//
// The refusals are served as `application/problem+json` with the RFC
// 9457 shape. What matters to a consumer is that the error branch is
// typed as that shape rather than as `unknown`, and that the branches
// are discriminated so reading `data` without checking `error` is an
// error.

async function refusalIsTyped(): Promise<string> {
  const result = await readAgentAgentsNameGet({
    ...perCall,
    path: { name: "absent" },
  });

  if (result.error !== undefined) {
    const problem: Problem = result.error;
    const first = problem.errors[0];
    // `errors` is a declared array, so its entries are typed; the
    // element access is guarded because the array may be empty.
    const pointer = first === undefined ? "" : first.path;
    return `${problem.status} ${problem.title}: ${problem.detail} ${pointer}`;
  }

  // In the success branch `data` is known to be present.
  const envelope: Envelope = result.data;
  return Object.keys(envelope.secrets).join(",");
}

// The union really is discriminated: outside the guard, `data` is
// possibly undefined and reading through it is refused.
async function unguardedReadIsRefused(): Promise<void> {
  const result = await readAgentAgentsNameGet({
    ...perCall,
    path: { name: "absent" },
  });
  // @ts-expect-error `data` is `Envelope | undefined` until `error` is checked.
  holds<Record<string, unknown>>(result.data.entity);
}

// The refusal statuses are kept apart by status code, each carrying the
// problem shape, so a consumer can branch on 404 versus 409 from the
// generated type rather than from a magic number of its own.
export type ProviderWriteRefusalsAreTypedPerStatus = Expect<
  Equals<
    WriteProviderProvidersStageNamePutErrors,
    { 401: Problem; 409: Problem; 422: Problem; 500: Problem }
  >
>;
export type ProviderReadRefusalsIncludeNotFound = Expect<
  Equals<
    ReadProviderProvidersStageNameGetErrors,
    { 401: Problem; 404: Problem; 409: Problem; 422: Problem; 500: Problem }
  >
>;

// The problem document has exactly the four members the server declares
// and refuses a fifth, which is what `additionalProperties: false`
// should buy a consumer.
const wellFormedProblem: Problem = {
  title: "Not Found",
  status: 404,
  detail: "No agent named absent.",
  errors: [],
};
holds<Problem>(wellFormedProblem);

const problemWithAnExtraKey: Problem = {
  title: "Not Found",
  status: 404,
  detail: "No agent named absent.",
  errors: [],
  // @ts-expect-error `type` and `instance` are deliberately absent from this API's problems.
  type: "about:blank",
};
holds<Problem>(problemWithAnExtraKey);

// ---------------------------------------------------------------------
// Probe 4: optional versus nullable
// ---------------------------------------------------------------------
//
// Three characters exist in this document and the fixture pins one
// example of each, because conflating them is how a client writes
// `null` where the server means "leave it out" and means the opposite.
//
//   optional, not nullable : AgentConfig.prompt      (`prompt?: string`)
//   required, nullable     : DefaultAgent.name       (`name: string | null`)
//   optional and nullable  : ProviderConfig.api_key_env

export type PromptIsOptional = Expect<Optional<AgentConfig, "prompt">>;
export type PromptIsNotNullable = Expect<
  Equals<Nullable<AgentConfig, "prompt">, false>
>;

const agentWithoutAPrompt: AgentConfig = { llm: "main" };
holds<AgentConfig>(agentWithoutAPrompt);

const agentWithANullPrompt: AgentConfig = {
  llm: "main",
  // @ts-expect-error absence is what an omitted prompt means; `null` is not a prompt.
  prompt: null,
};
holds<AgentConfig>(agentWithANullPrompt);

export type DefaultAgentNameIsRequired = Expect<
  Equals<Optional<DefaultAgent, "name">, false>
>;
export type DefaultAgentNameIsNullable = Expect<Nullable<DefaultAgent, "name">>;

const noDefaultAgent: DefaultAgent = { name: null };
holds<DefaultAgent>(noDefaultAgent);

// @ts-expect-error the read always says which way it is, so the key cannot be left out.
const defaultAgentWithoutTheKey: DefaultAgent = {};
holds<DefaultAgent>(defaultAgentWithoutTheKey);

export type ApiKeyEnvIsOptional = Expect<Optional<ProviderConfig, "api_key_env">>;
export type ApiKeyEnvIsNullable = Expect<Nullable<ProviderConfig, "api_key_env">>;

// The other four entity models keep the distinction too.
export type McpUrlIsOptionalAndNullable = Expect<
  Equals<
    [Optional<McpServerConfig, "url">, Nullable<McpServerConfig, "url">],
    [true, true]
  >
>;
export type McpTimeoutIsOptionalNotNullable = Expect<
  Equals<
    [
      Optional<McpServerConfig, "tool_timeout_s">,
      Nullable<McpServerConfig, "tool_timeout_s">,
    ],
    [true, false]
  >
>;
export type FragmentTextIsRequired = Expect<
  Equals<Optional<PromptFragmentConfig, "text">, false>
>;
export type DefaultsLlmIsOptionalAndNullable = Expect<
  Equals<
    [Optional<AgentDefaults, "llm">, Nullable<AgentDefaults, "llm">],
    [true, true]
  >
>;

// ---------------------------------------------------------------------
// Probe 5: the provider entries' extension properties
// ---------------------------------------------------------------------
//
// A provider entry carries whatever options its `type` takes, and the
// server passes them through rather than declaring them
// (`additionalProperties: true`). A generated type that refused unknown
// keys would make the whole provider form unwritable, so this is the
// probe that matters most for #129.

const providerWithPassthroughOptions: ProviderConfig = {
  type: "openai_compatible",
  api_key_env: "OPENAI_API_KEY",
  egress: false,
  base_url: "http://localhost:8080/v1",
  model: "qwen2.5:7b",
  temperature: 0.4,
  extra_headers: { "X-Tenant": "kitchen" },
};
holds<ProviderConfig>(providerWithPassthroughOptions);

// The declared keys keep their types even with the passthrough open:
// an extension property cannot smuggle a wrong value into a declared
// one.
const providerWithAWrongDeclaredKey: ProviderConfig = {
  type: "anthropic",
  // @ts-expect-error `egress` is declared as a boolean, passthrough or not.
  egress: "false",
};
holds<ProviderConfig>(providerWithAWrongDeclaredKey);

// Reading an extension property back gives `unknown`, which is honest:
// the document does not say what these are, so the consumer narrows.
function readAnExtensionProperty(entry: ProviderConfig): string {
  const model: unknown = entry["model"];
  return typeof model === "string" ? model : "";
}

// The entities that declare `additionalProperties: false` do refuse
// invented keys, so the passthrough is a provider fact rather than a
// hole in every model.
const fragmentWithAnInventedKey: PromptFragmentConfig = {
  text: "Answer briefly.",
  // @ts-expect-error prompt fragments declare their keys and refuse the rest.
  notes: "not a field",
};
holds<PromptFragmentConfig>(fragmentWithAnInventedKey);

// ---------------------------------------------------------------------
// Probe 6: operations under their operation_id names
// ---------------------------------------------------------------------
//
// The SDK names each function after the document's `operationId`,
// camel-cased. The probe is the import list at the top of this file
// plus this exhaustive re-export: a rename in the document is a compile
// error here rather than a runtime 404.

export const entityOperations = {
  readProvider: readProviderProvidersStageNameGet,
  writeProvider: writeProviderProvidersStageNamePut,
  removeProvider: removeProviderProvidersStageNameDelete,
  readMcpServer: readMcpServerMcpServersNameGet,
  writeMcpServer: writeMcpServerMcpServersNamePut,
  removeMcpServer: removeMcpServerMcpServersNameDelete,
  readPromptFragment: readPromptFragmentPromptFragmentsNameGet,
  writePromptFragment: writePromptFragmentPromptFragmentsNamePut,
  removePromptFragment: removePromptFragmentPromptFragmentsNameDelete,
  readAgent: readAgentAgentsNameGet,
  writeAgent: writeAgentAgentsNamePut,
  removeAgent: removeAgentAgentsNameDelete,
  readAgentDefaults: readAgentDefaultsAgentDefaultsGet,
  writeAgentDefaults: writeAgentDefaultsAgentDefaultsPut,
  removeDefaultAgent: removeDefaultAgentDefaultAgentDelete,
} as const;

export const roundTrips = {
  provider: providerRoundTrip,
  mcpServer: mcpServerRoundTrip,
  promptFragment: promptFragmentRoundTrip,
  agent: agentRoundTrip,
  agentDefaults: agentDefaultsRoundTrip,
  refusal: refusalIsTyped,
  unguarded: unguardedReadIsRefused,
  extension: readAnExtensionProperty,
} as const;
