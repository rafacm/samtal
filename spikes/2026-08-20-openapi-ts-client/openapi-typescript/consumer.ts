/**
 * The strict-mode consumer fixture for openapi-typescript plus
 * openapi-fetch.
 *
 * Nothing here runs. `npx tsc --noEmit` is the test: every probe is a
 * claim about the generated types that either compiles or does not. The
 * probes are the same six the Hey API fixture makes, in the same order,
 * so the two can be read side by side.
 *
 *   1. Authentication, the bearer token the whole API requires.
 *   2. A read, a write and a delete for each of the five entities.
 *   3. Typed non-2xx problem responses, the RFC 9457 shape.
 *   4. Optional versus nullable, on a field of each character.
 *   5. The provider entries' extension properties.
 *   6. Every operation reachable under its operation_id name.
 *
 * The client shape differs from Hey API's, which is why this is a
 * second fixture rather than a shared one: openapi-typescript emits
 * types only, and openapi-fetch is addressed by path template and HTTP
 * method rather than by generated function. Where that difference makes
 * a probe fail, the probe is left failing with a comment saying so.
 */

import createClient from "openapi-fetch";
import type { Middleware } from "openapi-fetch";
import type { components, operations, paths } from "./generated/api";
import type { Equals, Expect, Nullable, Optional } from "../shared/expect";
import { holds } from "../shared/expect";

type Acknowledgement = components["schemas"]["Acknowledgement"];
type AgentConfig = components["schemas"]["AgentConfig"];
type AgentDefaults = components["schemas"]["AgentDefaults"];
type DefaultAgent = components["schemas"]["DefaultAgent"];
type Envelope = components["schemas"]["Envelope"];
type McpServerConfig = components["schemas"]["McpServerConfig"];
type Problem = components["schemas"]["Problem"];
type PromptFragmentConfig = components["schemas"]["PromptFragmentConfig"];
type ProviderConfig = components["schemas"]["ProviderConfig"];

// ---------------------------------------------------------------------
// Probe 1: authentication
// ---------------------------------------------------------------------
//
// openapi-typescript emits the security schemes as documentation and
// nothing else, and openapi-fetch has no notion of them, so the header
// name and the `Bearer ` prefix are written out here by hand. The
// document says the token is bearer and applies to every operation;
// nothing in the generated types says so, and nothing checks that this
// is what a consumer did.

declare const storedToken: string | undefined;

const authorization: Middleware = {
  onRequest({ request }) {
    if (storedToken !== undefined) {
      request.headers.set("Authorization", `Bearer ${storedToken}`);
    }
    return request;
  },
};

const api = createClient<paths>({ baseUrl: "https://vinga.example/api" });
api.use(authorization);

// The mistake the types cannot catch: nothing here is wrong to the
// compiler, and every call would come back 401.
const unauthenticated = createClient<paths>({
  baseUrl: "https://vinga.example/api",
});
holds<typeof api>(unauthenticated);

// ---------------------------------------------------------------------
// Probe 2: read, write and delete for the five entities
// ---------------------------------------------------------------------

async function providerRoundTrip(): Promise<void> {
  const read = await api.GET("/providers/{stage}/{name}", {
    params: { path: { stage: "llm", name: "main" } },
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

  const written = await api.PUT("/providers/{stage}/{name}", {
    params: { path: { stage: "llm", name: "main" } },
    body: { type: "anthropic", api_key_env: "ANTHROPIC_API_KEY" },
  });
  holds<Acknowledgement | undefined>(written.data);

  const removed = await api.DELETE("/providers/{stage}/{name}", {
    params: { path: { stage: "llm", name: "main" } },
  });
  holds<Acknowledgement | undefined>(removed.data);
}

async function mcpServerRoundTrip(): Promise<void> {
  const read = await api.GET("/mcp-servers/{name}", {
    params: { path: { name: "clock" } },
  });
  holds<Envelope | undefined>(read.data);

  const written = await api.PUT("/mcp-servers/{name}", {
    params: { path: { name: "clock" } },
    body: { transport: "stdio", command: "uvx", args: ["mcp-clock"] },
  });
  holds<Acknowledgement | undefined>(written.data);

  const removed = await api.DELETE("/mcp-servers/{name}", {
    params: { path: { name: "clock" } },
  });
  holds<Acknowledgement | undefined>(removed.data);
}

async function promptFragmentRoundTrip(): Promise<void> {
  const read = await api.GET("/prompt-fragments/{name}", {
    params: { path: { name: "house-rules" } },
  });
  holds<Envelope | undefined>(read.data);

  const written = await api.PUT("/prompt-fragments/{name}", {
    params: { path: { name: "house-rules" } },
    body: { text: "Answer in the language you were addressed in." },
  });
  holds<Acknowledgement | undefined>(written.data);

  const removed = await api.DELETE("/prompt-fragments/{name}", {
    params: { path: { name: "house-rules" } },
  });
  holds<Acknowledgement | undefined>(removed.data);
}

async function agentRoundTrip(): Promise<void> {
  const read = await api.GET("/agents/{name}", {
    params: { path: { name: "kitchen" } },
  });
  holds<Envelope | undefined>(read.data);

  const written = await api.PUT("/agents/{name}", {
    params: { path: { name: "kitchen" } },
    body: { llm: "main", prompt: "You are the kitchen assistant." },
  });
  holds<Acknowledgement | undefined>(written.data);

  const removed = await api.DELETE("/agents/{name}", {
    params: { path: { name: "kitchen" } },
  });
  holds<Acknowledgement | undefined>(removed.data);
}

// As in the Hey API fixture: the agent defaults are read and written
// and never deleted, because the document declares GET and PUT on
// `/agent-defaults` and nothing else. The defaults are a singleton that
// always exists, so there is no state a DELETE could reach. The delete
// third of this probe is not applicable to this entity rather than
// passing.
async function agentDefaultsRoundTrip(): Promise<void> {
  const read = await api.GET("/agent-defaults", {});
  holds<Envelope | undefined>(read.data);

  const written = await api.PUT("/agent-defaults", {
    body: { llm: "main", tts: "voice" },
  });
  holds<Acknowledgement | undefined>(written.data);
}

// The absence stated as a claim. openapi-typescript spells an
// undeclared method `never` on the path item, so the assertion reads
// off the generated table rather than off a comment, and a delete
// appearing later is a loud diff here.
export type AgentDefaultsHaveNoDelete = Expect<
  Equals<paths["/agent-defaults"]["delete"], undefined>
>;
export type AgentDefaultsHaveARead = Expect<
  Equals<
    NonNullable<paths["/agent-defaults"]["get"]>,
    operations["read_agent_defaults_agent_defaults_get"]
  >
>;

// `/default-agent` is a different resource, not the agent defaults'
// delete. The defaults are the provider references every agent
// inherits; the default agent is which agent covers a device with no
// binding of its own. It does have all three operations.
async function defaultAgentRoundTrip(): Promise<void> {
  const read = await api.GET("/default-agent", {});
  holds<components["schemas"]["DefaultAgent"] | undefined>(read.data);

  const written = await api.PUT("/default-agent", {
    body: { name: "kitchen" },
  });
  holds<Acknowledgement | undefined>(written.data);

  const cleared = await api.DELETE("/default-agent", {});
  holds<Acknowledgement | undefined>(cleared.data);
}

// A path the document does not declare is refused, and so is a method
// the document does not declare on a path it does.
async function undeclaredCallsAreRefused(): Promise<void> {
  // @ts-expect-error there is no `/providers/{name}` in the document.
  await api.GET("/providers/{name}", { params: { path: { name: "main" } } });
  // @ts-expect-error `/agent-defaults` has no DELETE, so the call itself is refused.
  await api.DELETE("/agent-defaults", {});
}

// ---------------------------------------------------------------------
// Probe 3: typed non-2xx problem responses
// ---------------------------------------------------------------------

async function refusalIsTyped(): Promise<string> {
  const result = await api.GET("/agents/{name}", {
    params: { path: { name: "absent" } },
  });

  if (result.error !== undefined) {
    const problem: Problem = result.error;
    const first = problem.errors[0];
    const pointer = first === undefined ? "" : first.path;
    return `${problem.status} ${problem.title}: ${problem.detail} ${pointer}`;
  }

  const envelope: Envelope = result.data;
  return Object.keys(envelope.secrets).join(",");
}

async function unguardedReadIsRefused(): Promise<void> {
  const result = await api.GET("/agents/{name}", {
    params: { path: { name: "absent" } },
  });
  // @ts-expect-error `data` is `Envelope | undefined` until `error` is checked.
  holds<Record<string, unknown>>(result.data.entity);
}

// The statuses stay apart in the generated operation type, so a
// consumer can branch on 404 versus 409 from the document rather than
// from a magic number of its own. openapi-fetch itself collapses them
// into one `error`, which is why this probe reads the operation type.
type RefusalBody<TCode extends number, TOperation extends { responses: object }> =
  TOperation["responses"] extends Record<
    TCode,
    { content: { "application/problem+json": infer TBody } }
  >
    ? TBody
    : never;

export type ProviderReadNotFoundIsAProblem = Expect<
  Equals<
    RefusalBody<404, operations["read_provider_providers__stage___name__get"]>,
    Problem
  >
>;
export type ProviderWriteHasNoNotFound = Expect<
  Equals<
    RefusalBody<404, operations["write_provider_providers__stage___name__put"]>,
    never
  >
>;

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
//   optional, not nullable : AgentConfig.prompt      (`prompt?: string`)
//   required, nullable     : DefaultAgent.name       (`name: string | null`)
//   optional and nullable  : ProviderConfig.api_key_env
//
// These hold only because the generation script passes
// `--default-non-nullable false`. Under the generator's own default,
// every property carrying a JSON Schema `default` loses its `?`, which
// turns each of these entity models into a request body demanding keys
// the server treats as optional. The finding is recorded in the
// implementation doc; the flag is not a contortion of the fixture but
// the setting a document of request bodies needs.

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
// openapi-typescript renders `additionalProperties: true` as an
// intersection with an index signature rather than as an index
// signature on the object itself. The consequence is worth pinning: the
// intersection member absorbs the excess-property check, so unknown
// keys are admitted, and the declared keys keep their declared types.

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

const providerWithAWrongDeclaredKey: ProviderConfig = {
  type: "anthropic",
  // @ts-expect-error `egress` is declared as a boolean, passthrough or not.
  egress: "false",
};
holds<ProviderConfig>(providerWithAWrongDeclaredKey);

function readAnExtensionProperty(entry: ProviderConfig): string {
  const model: unknown = entry["model"];
  return typeof model === "string" ? model : "";
}

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
// The operation ids survive as keys of the generated `operations`
// interface, verbatim rather than camel-cased, so the request and
// response types of every operation are reachable by the document's own
// name. A call is not: openapi-fetch is addressed by path template and
// method, and the operation id never appears at a call site. Renaming
// an operation id in the document therefore breaks this table and
// leaves every call above compiling.

// The claim under test is about all thirty-eight operations, not the
// fifteen the round trips call, so both surfaces are inventoried
// exhaustively and in both directions. The operation ids first, which
// is where the document's own names survive.

const everyOperationId = [
  "read_config_config_get",
  "read_providers_providers_get",
  "read_provider_providers__stage___name__get",
  "write_provider_providers__stage___name__put",
  "remove_provider_providers__stage___name__delete",
  "write_provider_secret_providers__stage___name__secrets__slot__put",
  "remove_provider_secret_providers__stage___name__secrets__slot__delete",
  "read_mcp_servers_mcp_servers_get",
  "read_mcp_server_mcp_servers__name__get",
  "write_mcp_server_mcp_servers__name__put",
  "remove_mcp_server_mcp_servers__name__delete",
  "write_mcp_secret_mcp_servers__name__secrets__slot__put",
  "remove_mcp_secret_mcp_servers__name__secrets__slot__delete",
  "read_prompt_fragments_prompt_fragments_get",
  "read_prompt_fragment_prompt_fragments__name__get",
  "write_prompt_fragment_prompt_fragments__name__put",
  "remove_prompt_fragment_prompt_fragments__name__delete",
  "read_agents_agents_get",
  "read_agent_agents__name__get",
  "write_agent_agents__name__put",
  "remove_agent_agents__name__delete",
  "read_agent_defaults_agent_defaults_get",
  "write_agent_defaults_agent_defaults_put",
  "read_default_agent_default_agent_get",
  "write_default_agent_default_agent_put",
  "remove_default_agent_default_agent_delete",
  "read_devices_devices_get",
  "read_device_devices__mac__get",
  "write_device_devices__mac__put",
  "remove_device_devices__mac__delete",
  "read_pending_devices_devices_pending_get",
  "add_device_devices_pending__code__post",
  "read_mcp_server_status_runtime_mcp_servers_get",
  "reload_mcp_servers_runtime_mcp_servers_reload_post",
  "read_agent_prompt_runtime_agents__name__prompt_get",
  "read_conversations_conversations_get",
  "read_conversation_conversations__session__get",
  "read_conversation_turns_conversations__session__turns_get",
] as const;

export type EveryOperationIdIsAKey = Expect<
  Equals<(typeof everyOperationId)[number], keyof operations>
>;
export type TheDocumentDeclaresThirtyEight = Expect<
  Equals<(typeof everyOperationId)["length"], 38>
>;

// And the path table, because with this candidate the path is the call
// site: a path that vanished from the document would break a call, and
// a renamed operation id would not. Twenty-three paths carry the
// thirty-eight operations.

const everyPath = [
  "/config",
  "/providers",
  "/providers/{stage}/{name}",
  "/providers/{stage}/{name}/secrets/{slot}",
  "/mcp-servers",
  "/mcp-servers/{name}",
  "/mcp-servers/{name}/secrets/{slot}",
  "/prompt-fragments",
  "/prompt-fragments/{name}",
  "/agents",
  "/agents/{name}",
  "/agent-defaults",
  "/default-agent",
  "/devices",
  "/devices/{mac}",
  "/devices/pending",
  "/devices/pending/{code}",
  "/runtime/mcp-servers",
  "/runtime/mcp-servers/reload",
  "/runtime/agents/{name}/prompt",
  "/conversations",
  "/conversations/{session}",
  "/conversations/{session}/turns",
] as const;

export type EveryPathIsAKey = Expect<
  Equals<(typeof everyPath)[number], keyof paths>
>;
export type TheDocumentDeclaresTwentyThreePaths = Expect<
  Equals<(typeof everyPath)["length"], 23>
>;

export type EntityOperations = {
  readProvider: operations["read_provider_providers__stage___name__get"];
  writeProvider: operations["write_provider_providers__stage___name__put"];
  removeProvider: operations["remove_provider_providers__stage___name__delete"];
  readMcpServer: operations["read_mcp_server_mcp_servers__name__get"];
  writeMcpServer: operations["write_mcp_server_mcp_servers__name__put"];
  removeMcpServer: operations["remove_mcp_server_mcp_servers__name__delete"];
  readPromptFragment: operations["read_prompt_fragment_prompt_fragments__name__get"];
  writePromptFragment: operations["write_prompt_fragment_prompt_fragments__name__put"];
  removePromptFragment: operations["remove_prompt_fragment_prompt_fragments__name__delete"];
  readAgent: operations["read_agent_agents__name__get"];
  writeAgent: operations["write_agent_agents__name__put"];
  removeAgent: operations["remove_agent_agents__name__delete"];
  readAgentDefaults: operations["read_agent_defaults_agent_defaults_get"];
  writeAgentDefaults: operations["write_agent_defaults_agent_defaults_put"];
  // The default agent, which is a resource of its own and the only one
  // of the two with a delete.
  readDefaultAgent: operations["read_default_agent_default_agent_get"];
  writeDefaultAgent: operations["write_default_agent_default_agent_put"];
  removeDefaultAgent: operations["remove_default_agent_default_agent_delete"];
};

// The path table and the operation table agree, which is the property
// that makes calling by path safe at all.
export type ProviderPutIsTheProviderWriteOperation = Expect<
  Equals<
    NonNullable<paths["/providers/{stage}/{name}"]["put"]>,
    operations["write_provider_providers__stage___name__put"]
  >
>;

export const roundTrips = {
  provider: providerRoundTrip,
  mcpServer: mcpServerRoundTrip,
  promptFragment: promptFragmentRoundTrip,
  agent: agentRoundTrip,
  agentDefaults: agentDefaultsRoundTrip,
  defaultAgent: defaultAgentRoundTrip,
  undeclared: undeclaredCallsAreRefused,
  refusal: refusalIsTyped,
  unguarded: unguardedReadIsRefused,
  extension: readAnExtensionProperty,
} as const;
