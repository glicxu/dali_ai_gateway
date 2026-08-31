# Dali AI Gateway Agent Runtime and MCP Boundary Design

Status: Proposed architecture design  
Audience: AI Gateway, agent-runtime, product-service, MCP integration, security,
and operations owners  
Scope: Make Dali AI Gateway a provider-neutral inference backend that can be
used by Dali agent runtimes without turning Gateway into an MCP host, MCP client,
tool executor, or agent workflow service.

## 1. Decision

Dali AI Gateway supports agents as authenticated inference callers. It does not
run agents and does not connect to MCP servers.

The target boundary is:

```text
Product or shared Agent Runtime
|- owns the system prompt and conversation state
|- discovers MCP tools and resources
|- holds MCP credentials and applies user/product authorization
|- requests approval and executes tools
|- validates tool results and drives the agent loop
|
|- calls MCP servers ------------------------> Product/external tools
|
`- calls Dali AI Gateway
       |- authorizes the runtime workload/profile
       |- routes transient inference to an approved model provider
       |- normalizes text and tool-call proposals
       |- enforces capacity, timeout, cancellation, and safe errors
       `- returns content-free usage and discards request/response content
```

Gateway may transiently carry the messages, tool declarations, and tool results
that a model needs for one inference turn. It never stores them, executes a tool,
or decides whether a tool is safe or authorized.

This decision preserves the product/data-plane boundary in
[`multi_product_gateway_design.md`](multi_product_gateway_design.md).

## 2. Why MCP Does Not Belong Inside Gateway

MCP is a protocol for an agent host to discover and invoke tools, prompts, and
resources exposed by MCP servers. Operating that protocol requires product and
user context that Gateway intentionally does not possess:

- which user and product account initiated the action;
- which tools/resources that user may access;
- whether an operation requires confirmation;
- which MCP credentials and tenant apply;
- how to render or validate approval;
- how tool results affect durable product state;
- how to retry or compensate a side effect; and
- how conversation and workflow state should continue.

Putting those concerns in Gateway would make it a product workflow/control
plane, require user identity and credentials to cross the AI data plane, and
create durable content pressure. It would also weaken the ability of each
product to define its own safety and approval policy.

Gateway's role is narrower: execute a bounded provider inference operation and
return a provider-neutral result.

## 3. Terminology

- **Agent runtime**: the service that owns the agent loop, messages, tool policy,
  approval, MCP connections, and durable workflow state.
- **MCP host/client**: the agent-side component that connects to MCP servers and
  invokes their tools/resources/prompts.
- **MCP server**: a service exposing tools, resources, or prompts through MCP.
- **Inference turn**: one bounded model request producing assistant content,
  zero or more tool-call proposals, or a terminal error.
- **Tool declaration**: a transient, provider-neutral name, description, and
  input schema supplied to the model.
- **Tool-call proposal**: model output asking the agent runtime to invoke one
  declared tool. It has no authority by itself.
- **Tool result**: content returned by the agent runtime to the model in a later
  inference turn after authorization and execution.

## 4. Goals

1. Let approved Dali agent runtimes use Gateway rather than implement provider
   transports independently.
2. Normalize multi-message inference, streaming assistant output, structured
   tool-call proposals, usage, cancellation, and errors across providers.
3. Keep agent system prompts, conversation state, MCP configuration,
   credentials, approval, and tool execution outside Gateway.
4. Preserve exact workload/product/profile authorization and single-request
   content transience.
5. Keep model/provider selection behind server-owned abstract profiles.
6. Prevent a model-generated tool call from being mistaken for authorization.
7. Support safe provider routing/fallback only when agent/tool semantics are
   compatible.
8. Preserve existing Classroom v1 text and realtime contracts.

## 5. Non-Goals

Gateway does not:

- expose an MCP server interface merely to wrap its AI endpoints;
- act as an MCP host or client;
- discover, list, connect to, or health-check MCP servers;
- accept or store MCP server URLs, access tokens, OAuth grants, API keys, or
  cookies;
- execute a tool or read an MCP resource;
- own tool allowlists, user approval, or side-effect classification;
- drive a multi-turn agent loop;
- store system prompts, conversation messages, tool schemas, tool results,
  memory, checkpoints, or artifacts;
- own product/user authorization, account identity, plan/tier policy, or billing
  decisions;
- provide a general-purpose arbitrary-provider proxy; or
- allow mobile/browser clients to call agent inference directly.

If Dali later needs a shared agent service, it is a separate agent-runtime
service with its own authorization, storage, MCP, approval, and product
contracts. It may call Gateway as one dependency.

## 6. Ownership Boundary

| Concern | Agent runtime/product service | AI Gateway |
|---|---|---|
| Agent identity | Determine user, product account, tenant, and initiating operation | Authenticate only the calling workload |
| System prompt | Build, version, test, localize, and select | Validate size and forward transiently |
| Conversation | Store/order messages, summarize history, enforce retention/deletion | Process only messages included in the current request |
| MCP | Discover servers/tools/resources, hold credentials, connect, retry | No MCP connection or credentials |
| Tool policy | Allowlist tools, assess risk, require approval, constrain arguments | Verify a proposed call references a declared tool; do not authorize it |
| Tool execution | Invoke tool, validate result, compensate side effects | Never invoke a tool |
| Agent loop | Decide whether to continue, call a tool, retry, stop, or escalate | Execute one inference turn |
| Model routing | Approve product profile and rollout | Select approved provider/model route behind the profile |
| Content | Own all durable prompts, messages, results, and artifacts | Hold bounded transient content only |
| Usage | Associate usage with durable product/account operation | Normalize provider-derived content-free measurement |
| Safety | Product action policy, approval UX, output use, moderation required by product | Transport validation, provider configuration, capacity, and content-safe errors |

## 7. Agent Inference Capability

Add a provider-neutral capability named `agent_inference`. It is separate from
the existing simple `text_generation` capability so existing callers do not
silently acquire tool-calling or multi-message behavior.

An abstract agent profile defines only operational/model policy:

- capability `agent_inference`;
- approved provider/model route candidates;
- supported input roles and content-part types;
- supported structured-output and tool-call features;
- maximum messages, tools, schema bytes, content bytes, and output tokens;
- timeout, streaming support, and capacity/cost class;
- compatible fallback policy; and
- rollout and kill-switch state.

It must not contain a system prompt, product tool list, MCP server reference,
conversation, or product workflow rule.

Example profiles:

```text
shared.agent.text.standard
shared.agent.tool_calling.standard
<product>.agent.<approved-purpose>
```

Despite a `shared.` namespace, every workload needs an exact profile grant.
Prefix-based access is prohibited.

## 8. Request Contract

### 8.1 Envelope

One inference request contains:

- random request ID;
- registered product and exact profile;
- ordered transient messages;
- optional transient tool declarations;
- optional provider-neutral response-format constraint;
- bounded generation controls allowed by the profile; and
- streaming preference.

The workload identity comes from authentication, not the body. The request
contains no Dali account ID, user token, MCP credential, or MCP endpoint.

### 8.2 Messages

The normalized message model supports only contract-defined roles and parts,
for example:

```text
system/developer instruction
user text or approved media reference/content
assistant text
assistant tool-call proposal
tool result associated with a prior call ID
```

Product services decide which roles/content to send. Gateway validates ordering,
size, types, and references, then maps them to the selected provider without
storing them.

Remote URLs supplied as message content are denied by default. Gateway must not
become an arbitrary URL fetcher. If a later profile supports remote media, it
requires a separate allowlisted fetch/proxy design with SSRF and retention
controls.

### 8.3 Tool declarations

A tool declaration contains only:

- stable runtime-local tool name;
- bounded human/model description;
- strict JSON Schema for arguments; and
- optional content-free annotations needed by the model contract.

The declaration does not contain an MCP server URI, bearer token, tenant
credential, callback URL, or executable code. Tool descriptions and schemas are
still transient product content and receive the same no-log/no-store handling
as prompts.

Gateway validates:

- names are unique and match the contract pattern;
- schemas are bounded and use the approved JSON Schema subset;
- unsupported recursive/executable/reference features are rejected;
- tool choice refers only to a declared tool; and
- profile tool-count/schema-size ceilings are enforced.

Validation does not imply that a user may execute the tool.

### 8.4 Tool results

The agent runtime executes an approved tool and may send a bounded result in a
later inference request. Gateway verifies the result references a prior call ID
present in the submitted message sequence, but Gateway does not look up that
call or maintain conversation state.

The product runtime owns sanitization, provenance, prompt-injection handling,
retention, and any distinction between trusted and untrusted tool output.

## 9. Response Contract

One inference turn returns exactly one normalized terminal outcome:

- assistant message content;
- one or more structured tool-call proposals;
- a declared structured response; or
- a normalized error/cancellation.

A tool-call proposal contains:

- Gateway/provider-neutral call ID;
- exact declared tool name;
- JSON arguments or a bounded argument stream;
- completion status; and
- no execution or authorization status.

Gateway validates final arguments against the submitted schema when possible.
Invalid arguments are returned as a normalized model-output error; Gateway does
not repair them by executing another hidden agent turn unless the product
explicitly requested an approved bounded generation policy.

The agent runtime must independently:

1. match the call to its current conversation/turn;
2. verify the tool remains allowed;
3. validate arguments against its authoritative schema and business rules;
4. obtain user approval when required;
5. execute through its MCP client or internal tool adapter; and
6. decide whether to submit the result for another inference turn.

Model output is never authority to perform an external action.

## 10. Streaming Protocol

Agent streaming uses a provider-neutral HTTP streaming or WebSocket contract
selected during contract design. Required events are:

- `response.ready`;
- `message.delta`;
- `message.final`;
- `tool_call.start`;
- `tool_call.arguments.delta`;
- `tool_call.final`;
- `usage.update` where available;
- `usage.final`;
- `response.completed`;
- `response.cancelled`; and
- normalized `error`.

Every event carries the request ID and a monotonically increasing sequence
number. Tool events include a stable call ID and exact declared tool name.
Callers ignore unknown optional events but never unknown terminal states.

Streaming content and partial tool arguments are transient. Gateway does not
buffer an entire response beyond bounded protocol/provider requirements and
does not offer later stream replay.

## 11. Cancellation, Timeout, and Retry

- Client cancellation propagates to the provider when supported and releases
  admission capacity promptly.
- Profile policy sets connection, first-event, idle, and total-turn timeouts.
- Gateway returns whether a failure occurred before provider acceptance, after
  an ambiguous acceptance, or during output streaming.
- Gateway may retry/fallback before unambiguous provider acceptance under the
  profile policy.
- Gateway does not automatically repeat a turn after partial assistant content
  or a tool-call proposal was emitted.
- The agent runtime decides whether to retry and how to prevent duplicate tool
  execution.
- Request IDs support correlation and usage deduplication, not response replay.

## 12. Provider Routing and Compatibility

An agent profile may have multiple provider routes only when they have
product-approved compatible semantics for:

- message roles and content types;
- system/developer instruction precedence;
- tool name and JSON Schema handling;
- parallel tool calls;
- structured output;
- streaming order and completion;
- token/context limits;
- safety/privacy/data-use terms; and
- usage measurement.

Provider adapters translate their native protocol into the normalized contract.
Raw provider events, finish reasons, errors, request IDs, or tool representations
do not cross the boundary unless an explicitly normalized field exists.

Fallback from a tool-capable profile to a text-only model is prohibited.
Fallback cannot silently drop tools, schemas, instructions, or structured-output
constraints.

## 13. Workload Authorization

Agent inference uses the workload model from the multi-product Gateway design.
The grant is the intersection of:

```text
authenticated agent-runtime workload
AND registered product
AND exact agent profile
AND agent_inference capability
AND environment
AND rollout/kill-switch state
```

Optional grant policy may cap tool count, schema bytes, message bytes, output
tokens, concurrent turns, and cost class per runtime/profile.

Gateway never accepts a mobile/user Platform token for agent inference. The
agent runtime performs user/product authorization and calls with its own
workload identity.

## 14. Privacy and Security Invariants

1. Gateway has no MCP credentials, endpoints, connections, or durable MCP state.
2. Gateway executes no tool and reads no MCP resource.
3. System prompts, messages, tool declarations, arguments, and results are
   transient content and never logged or stored.
4. No Dali account ID, user token, tenant name, product object name, or approval
   record enters Gateway.
5. Tool-call proposals are untrusted model output and confer no authority.
6. Tool schemas are bounded and validated against an approved subset.
7. Remote content fetching is denied unless a later explicit design authorizes
   it.
8. Provider errors and raw events never cross the Gateway boundary.
9. Exact workload/product/profile grants are deny-by-default.
10. Content never enters metrics, traces, audit records, circuit state, usage
    events, or exception details.
11. Realtime/stream buffers are bounded and cleared on completion, cancellation,
    error, timeout, disconnect, and shutdown.
12. Provider routes are enabled only after privacy/data-use terms are approved
    for the calling product.

## 15. Usage and Billing Boundary

Gateway emits only content-free measurements such as:

- request/event ID;
- workload, product, profile, and capability;
- provider/model accounting route where approved;
- input, cached, reasoning, and output token counts where available;
- tool declaration count and aggregate schema bytes;
- proposed tool-call count;
- latency, fallback count, and terminal disposition; and
- measurement schema version.

Measurements never contain tool names, descriptions, schemas, arguments,
results, prompts, messages, or user/account identity.

The product/agent runtime durably associates usage with its authorized product
operation. Platform accepts only the agreed content-free usage event. Gateway
does not decide the user's plan/tier or whether a proposed tool call consumes a
product quota.

## 16. Observability

Allowed metrics include:

- admission, active turns, cancellation, timeout, and completion counts;
- first-token/tool-call and total-turn latency;
- normalized terminal outcome and provider circuit state;
- token and aggregate schema-byte histograms;
- tool-call proposal counts without tool names;
- streaming disconnect/sequence errors;
- fallback and ambiguous-outcome counts; and
- usage-delivery state.

Logs and traces use random operational request IDs and normalized codes only.
Debugging must rely on product-controlled reproducible test fixtures, never
production prompt/message capture in Gateway.

## 17. Existing Dali Agent Runtime Boundary

Any existing `app_server` shared/product agent runtime remains the agent host.
This design does not move its prompts, profiles, conversations, tools, MCP
connections, approval, or state into Gateway.

A future migration may replace only its provider-inference adapter:

```text
existing agent runtime -> direct provider
```

with:

```text
existing agent runtime -> Dali AI Gateway -> approved provider
```

That change requires a separate consumer migration manifest, contract adapter,
shadow comparison, canary, and direct-provider rollback. It must not change the
agent's product behavior merely because transport moved behind Gateway.

## 18. Optional MCP Facade

Gateway does not need an MCP server facade for Dali agent runtimes; they can call
its private HTTP/streaming inference contract directly.

If an external integration later requires Gateway capabilities to appear as MCP
tools, implement a separate thin, stateless, private facade with its own workload
authentication and exact profile grants:

```text
MCP client -> dedicated Dali MCP inference facade -> AI Gateway
```

The facade must not expose arbitrary prompts/models, persist content, or broaden
Gateway authorization. It is a transport adapter, not an agent runtime. Its need
and threat model require separate approval.

## 19. Contract Evolution

The current simple text-generation contract remains unchanged. Agent inference
is a new capability and endpoint/stream contract rather than an incompatible
expansion of existing text fields.

Contract work includes:

- OpenAPI 3.1 request/non-stream response definitions;
- a streaming event schema using the selected transport;
- strict message/content-part/tool/schema models;
- normalized tool-call and terminal outcomes;
- usage schema/version;
- examples without real prompts, tools, or credentials;
- compatibility and negative fixtures; and
- provider-adapter conformance fixtures.

Published fields are not repurposed. Optional additions remain ignorable unless
they represent a terminal event, in which case unknown values fail closed.

## 20. Implementation Roadmap

1. Complete the multi-product Gateway authorization/readiness foundation.
2. Inventory the first approved agent runtime's provider protocol and tool
   semantics without changing it.
3. Define normalized messages, tool declarations/calls/results, usage, errors,
   and streaming schemas.
4. Implement one fake-provider adapter and exhaustive contract/privacy tests.
5. Implement one approved provider route behind a disabled agent profile.
6. Add cancellation, timeout, admission, usage, and circuit integration.
7. Run golden prompt/tool-call conformance and shadow comparison using synthetic
   or explicitly approved test fixtures.
8. Canary one read-only/no-side-effect agent workflow with direct-provider
   rollback.
9. Consider side-effecting tools only after the agent runtime's authorization,
   approval, idempotency, and compensation gates pass independently.

## 21. Readiness Criteria

Gateway is agent-runtime-ready only when:

- the multi-product workload/profile/admission/readiness gates pass;
- agent inference is a separate exact-grant capability;
- request and streaming contracts validate and remain provider-neutral;
- tool schemas/calls are bounded and strictly validated;
- Gateway has no MCP connection, credential, tool execution, or durable content;
- account/workload/product authorization boundaries pass negative tests;
- cancellation, partial streaming, ambiguous outcome, and retry behavior are
  tested;
- at least one provider adapter passes common conformance fixtures;
- content/privacy scans cover messages, prompts, tools, arguments, and results;
- usage is content-free, idempotent, and durably deliverable under the approved
  Gateway usage pattern;
- load, outage, fallback, and rollback tests pass; and
- the first product/agent owner approves the canary and rollback gates.

Passing these gates does not approve an MCP server, tool, product, provider,
model, or side-effecting workflow automatically.

## 22. Decisions Required Before Implementation

1. First approved Dali agent runtime and read-only pilot workflow.
2. HTTP streaming versus WebSocket transport for agent inference.
3. Approved normalized message/content-part model.
4. Supported JSON Schema subset for tool arguments and structured output.
5. Parallel tool-call semantics and maximum tool/schema/message limits.
6. Tool-call argument validation and invalid-output behavior.
7. Provider route compatibility/fallback rules for tool calling.
8. Cancellation and ambiguous provider-acceptance semantics.
9. Content-free agent usage schema and durable delivery pattern.
10. Whether a separate MCP inference facade has any real consumer; default is
    not to build one.

No unresolved decision should be replaced by a provider-specific production
default.
