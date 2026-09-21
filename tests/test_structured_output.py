import asyncio
import copy
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.errors import GatewayError
from app.main import create_app
from app.models import TextGenerationRequest
from app.providers.openai import OpenAIProvider


SCHEMA = {"name": "example", "strict": True, "schema": {
    "type": "object", "properties": {"items": {"type": "array", "items": {"type": "string"}}},
    "required": ["items"], "additionalProperties": False}}


def payload():
    return {"request_id": str(uuid4()), "product": "classroom",
            "profile": "classroom.translation.economy", "system_instruction": "Extract.",
            "input": "Synthetic input", "response_format": "json_schema",
            "structured_output": copy.deepcopy(SCHEMA)}


@pytest.mark.parametrize("keyword,value", [("$ref", "https://example.invalid/schema"),
    ("description", "schema content"), ("additionalProperties", True), ("required", []),
    ("type", "array"), ("description", "x" * 33000)],
    ids=["reference", "description", "extras", "missing-required", "root-array", "oversized"])
def test_schema_subset_rejects_unsupported(keyword, value):
    data = payload()
    data["structured_output"]["schema"][keyword] = value
    with pytest.raises(ValidationError):
        TextGenerationRequest.model_validate(data)


def test_schema_subset_accepts_bounded_product_constraints():
    data = payload()
    data["structured_output"]["schema"] = {
        "type": "object",
        "properties": {
            "country": {
                "anyOf": [
                    {"type": "string", "pattern": "^[A-Z]{2}$", "minLength": 2, "maxLength": 2},
                    {"type": "null"},
                ]
            },
            "context": {"type": "string", "enum": ["professional", "academic"]},
            "period": {"type": "string", "const": "unknown"},
            "score": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            "items": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0},
                "minItems": 1,
                "maxItems": 10,
            },
        },
        "required": ["country", "context", "period", "score", "items"],
        "additionalProperties": False,
    }
    assert TextGenerationRequest.model_validate(data).structured_output is not None


@pytest.mark.parametrize("node", [
    {"type": "string", "enum": ["x", "x"]},
    {"type": "string", "const": 1},
    {"type": ["string", "integer"]},
    {"type": ["string", "null", "integer"]},
    {"anyOf": [{"type": "string"}]},
    {"anyOf": [{"type": "string"}] * 5},
    {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 1},
    {"type": "number", "multipleOf": 0},
    {"type": "number", "minimum": -100_001},
    {"type": "number", "maximum": 100_001},
    {"type": "string", "pattern": "x" * 257},
])
def test_schema_subset_rejects_invalid_product_constraints(node):
    data = payload()
    data["structured_output"]["schema"]["properties"]["items"] = node
    with pytest.raises(ValidationError):
        TextGenerationRequest.model_validate(data)


def test_format_and_depth_bounds():
    data = payload()
    assert TextGenerationRequest.model_validate(data).structured_output is not None
    data["response_format"] = "json"
    with pytest.raises(ValidationError):
        TextGenerationRequest.model_validate(data)
    data = payload()
    node = data["structured_output"]["schema"]["properties"]["items"]
    for _ in range(15):
        node["items"] = {"type": "array"}
        node = node["items"]
    node["items"] = {"type": "string"}
    with pytest.raises(ValidationError):
        TextGenerationRequest.model_validate(data)


@pytest.mark.parametrize("count,width", [(257, 5), (240, 128)])
def test_schema_node_and_byte_limits(count, width):
    data = payload()
    properties = {str(index).zfill(width): {"type": "string"} for index in range(count)}
    data["structured_output"]["schema"] = {
        "type": "object", "properties": properties,
        "required": list(properties), "additionalProperties": False}
    with pytest.raises(ValidationError):
        TextGenerationRequest.model_validate(data)


@pytest.mark.parametrize("provider,outputs,status", [
    ("openai", None, 400), ("gemini", ["json_schema"], 400),
    ("openai", ["json_schema"], 200)])
def test_profile_opt_in_before_dispatch(settings, fake_provider, headers, provider, outputs, status):
    settings.model_profiles_json = json.dumps({"classroom.translation.economy": {
        "capability": "text_generation", "provider": provider, "model": "fixture-model",
        "supported_outputs": outputs}})
    app = create_app(settings, providers={"openai": fake_provider, "gemini": fake_provider})
    with TestClient(app) as client:
        response = client.post("/ai/v1/text/generations", headers=headers, json=payload())
    assert response.status_code == status
    assert len(fake_provider.generated_inputs) == (1 if status == 200 else 0)


@pytest.mark.parametrize("finish,refusal", [("stop", None), ("length", None), ("stop", "refused")])
def test_openai_exact_schema_and_no_partial_result(finish, refusal):
    async def run():
        def handler(request):
            sent = json.loads(request.content)
            assert sent["response_format"] == {"type": "json_schema", "json_schema": SCHEMA}
            return httpx.Response(200, json={"choices": [{"finish_reason": finish,
                "message": {"content": '{"items":[]}', "refusal": refusal}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAIProvider(api_key="fixture", base_url="https://fixture.invalid",
                                      timeout_seconds=1, client=client)
            call = provider.generate(model="fixture", system_instruction="Extract", input_text="Text",
                response_format="json_schema", temperature=0, structured_output=SCHEMA)
            if finish != "stop" or refusal:
                with pytest.raises(GatewayError):
                    await call
            else:
                assert (await call).output == '{"items":[]}'
    asyncio.run(run())
