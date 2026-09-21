# Generic structured output (P3-01)

2026-09-15. The transport is product-neutral; model-profile policy controls
where it is active.

Text generation accepts `response_format: json_schema` together with
`structured_output: {name, strict: true, schema}`. Existing text/JSON requests
remain compatible. The schema stays in transient request memory; products own
its meaning and must validate returned content. No schema enters Platform.

The bounded subset accepts objects, arrays, strings, numbers, integers, booleans
and null. It also accepts scalar `enum` and `const`, nullable type unions,
non-root `anyOf`, string patterns and lengths, numeric bounds and `multipleOf`,
and array item-count bounds. The root must be an object. Every object requires
every property and sets `additionalProperties: false`. Arrays require `items`.
References and descriptions remain rejected; products must flatten their own
definitions before calling the Gateway. Bounds include 32 KiB canonical ASCII
JSON, 12 levels, 256 nodes, 100 properties per object, four union branches, 100
enum members per node, 256 enum members in aggregate, 16 KiB of enum/constant
strings, patterns of at most 256 characters, and numeric/length/item bounds no
greater than 100,000 in magnitude. Property names are 1-128 characters. Schema
names are 1-64 alphanumeric/underscore/hyphen characters. This intentionally
does not claim full JSON Schema support.

Only an OpenAI text profile explicitly listing `json_schema` in
`supported_outputs` can dispatch this format. Other providers/profiles reject it
before admission/execution. The adapter forwards strict Chat Completions schema
output and rejects refusals/incomplete completions; it never downgrades or retries
through a different provider. OpenAI Docs informed this transport mapping:
[Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

The candidate OpenAPI snapshot is regenerated. No provider inference or new
capacity is implied. DaliJob activates the format only for the reviewed parsing
and job-extraction profiles; other application profiles retain their configured
output modes. Keep product schemas and prompts out of Gateway configuration.
