from __future__ import annotations

import re
from dataclasses import dataclass
from threading import Lock
from types import MappingProxyType
from typing import Literal, Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


Capability = Literal[
    "text_generation",
    "audio_transcription",
    "speech_synthesis",
    "image_analysis",
    "video_analysis",
    "realtime_transcription",
    "realtime_translation",
]

_GENERATION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$")
_POLICY_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")
_WORKLOAD_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")


class _StrictPolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProfilePolicyDocument(_StrictPolicyModel):
    capability: Capability
    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    model: str = Field(min_length=1, max_length=255)
    usage: Literal["production", "evaluation"] = "production"
    enabled: bool = True
    required_for_readiness: bool = True


class WorkloadGrantDocument(_StrictPolicyModel):
    workload_type: Literal["product", "evaluator"] = "product"
    products: list[str] = Field(min_length=1, max_length=128)
    profiles: list[str] = Field(min_length=1, max_length=256)
    capabilities: list[Capability] = Field(min_length=1, max_length=32)
    disabled_products: list[str] = Field(default_factory=list, max_length=128)
    disabled_profiles: list[str] = Field(default_factory=list, max_length=256)
    disabled_capabilities: list[Capability] = Field(default_factory=list, max_length=32)
    enabled: bool = True

    @field_validator("products", "disabled_products")
    @classmethod
    def products_are_valid_and_unique(cls, value: list[str]) -> list[str]:
        return _validated_unique_names(value, field="product")

    @field_validator("profiles", "disabled_profiles")
    @classmethod
    def profiles_are_valid_and_unique(cls, value: list[str]) -> list[str]:
        return _validated_unique_names(value, field="profile")

    @field_validator("capabilities", "disabled_capabilities")
    @classmethod
    def capabilities_are_unique(cls, value: list[Capability]) -> list[Capability]:
        if len(value) != len(set(value)):
            raise ValueError("capabilities must be unique")
        return value

    @model_validator(mode="after")
    def kill_switches_reference_granted_values(self) -> WorkloadGrantDocument:
        if not set(self.disabled_products).issubset(self.products):
            raise ValueError("disabled products must be granted")
        if not set(self.disabled_profiles).issubset(self.profiles):
            raise ValueError("disabled profiles must be granted")
        if not set(self.disabled_capabilities).issubset(self.capabilities):
            raise ValueError("disabled capabilities must be granted")
        return self


class PolicyGenerationDocument(_StrictPolicyModel):
    generation_id: str
    profiles: dict[str, ProfilePolicyDocument] = Field(min_length=1, max_length=256)
    grants: dict[str, WorkloadGrantDocument] = Field(min_length=1, max_length=128)

    @field_validator("generation_id")
    @classmethod
    def generation_id_is_valid(cls, value: str) -> str:
        if not _GENERATION_PATTERN.fullmatch(value):
            raise ValueError("generation_id is invalid")
        return value

    @field_validator("profiles")
    @classmethod
    def profile_names_are_valid(
        cls, value: dict[str, ProfilePolicyDocument]
    ) -> dict[str, ProfilePolicyDocument]:
        for name in value:
            if not _POLICY_NAME_PATTERN.fullmatch(name):
                raise ValueError("profile name is invalid")
        return value

    @field_validator("grants")
    @classmethod
    def workload_names_are_valid(
        cls, value: dict[str, WorkloadGrantDocument]
    ) -> dict[str, WorkloadGrantDocument]:
        for name in value:
            if not _WORKLOAD_PATTERN.fullmatch(name):
                raise ValueError("workload name is invalid")
        return value

    @model_validator(mode="after")
    def grants_reference_profiles_and_capabilities(self) -> PolicyGenerationDocument:
        for grant in self.grants.values():
            for profile_name in grant.profiles:
                profile = self.profiles.get(profile_name)
                if profile is None:
                    raise ValueError("grant references an unknown profile")
                if profile.capability not in grant.capabilities:
                    raise ValueError("grant omits a referenced profile capability")
                if profile.usage == "evaluation" and grant.workload_type != "evaluator":
                    raise ValueError(
                        "evaluation profiles require a dedicated evaluator workload"
                    )
        return self


@dataclass(frozen=True, slots=True)
class ModelProfile:
    name: str
    capability: Capability
    provider: str
    model: str
    usage: Literal["production", "evaluation"]
    enabled: bool
    required_for_readiness: bool


@dataclass(frozen=True, slots=True)
class WorkloadGrant:
    workload_id: str
    workload_type: Literal["product", "evaluator"]
    products: frozenset[str]
    profiles: frozenset[str]
    capabilities: frozenset[Capability]
    disabled_products: frozenset[str]
    disabled_profiles: frozenset[str]
    disabled_capabilities: frozenset[Capability]
    enabled: bool


@dataclass(frozen=True, slots=True)
class PolicyGeneration:
    generation_id: str
    profiles: Mapping[str, ModelProfile]
    grants: Mapping[str, WorkloadGrant]

    @classmethod
    def from_document(cls, document: PolicyGenerationDocument) -> PolicyGeneration:
        profiles = {
            name: ModelProfile(
                name=name,
                capability=value.capability,
                provider=value.provider,
                model=value.model,
                usage=value.usage,
                enabled=value.enabled,
                required_for_readiness=value.required_for_readiness,
            )
            for name, value in document.profiles.items()
        }
        grants = {
            workload_id: WorkloadGrant(
                workload_id=workload_id,
                workload_type=value.workload_type,
                products=frozenset(value.products),
                profiles=frozenset(value.profiles),
                capabilities=frozenset(value.capabilities),
                disabled_products=frozenset(value.disabled_products),
                disabled_profiles=frozenset(value.disabled_profiles),
                disabled_capabilities=frozenset(value.disabled_capabilities),
                enabled=value.enabled,
            )
            for workload_id, value in document.grants.items()
        }
        return cls(
            generation_id=document.generation_id,
            profiles=MappingProxyType(profiles),
            grants=MappingProxyType(grants),
        )


class PolicyStore:
    """Atomically activates complete, already validated policy generations."""

    def __init__(self, initial: PolicyGeneration) -> None:
        self._lock = Lock()
        self._current = initial
        self._validated = {initial.generation_id: initial}
        self._load_healthy = True
        self._load_outcome: Literal["active", "rejected", "rollback"] = "active"

    @property
    def current(self) -> PolicyGeneration:
        with self._lock:
            return self._current

    @property
    def load_healthy(self) -> bool:
        with self._lock:
            return self._load_healthy

    @property
    def load_outcome(self) -> Literal["active", "rejected", "rollback"]:
        with self._lock:
            return self._load_outcome

    def snapshot(
        self,
    ) -> tuple[
        PolicyGeneration,
        bool,
        Literal["active", "rejected", "rollback"],
    ]:
        with self._lock:
            return self._current, self._load_healthy, self._load_outcome

    def activate(self, candidate: PolicyGeneration) -> PolicyGeneration:
        with self._lock:
            existing = self._validated.get(candidate.generation_id)
            if existing is not None and existing != candidate:
                raise ValueError("policy generation ID conflicts with validated data")
            self._validated[candidate.generation_id] = candidate
            self._current = candidate
            self._load_healthy = True
            self._load_outcome = "active"
            return self._current

    def load_document(self, value: Mapping[str, object]) -> bool:
        """Validate then activate a whole generation, retaining current on failure."""
        try:
            document = PolicyGenerationDocument.model_validate(value)
            candidate = PolicyGeneration.from_document(document)
            self.activate(candidate)
            return True
        except (ValidationError, ValueError):
            with self._lock:
                self._load_healthy = False
                self._load_outcome = "rejected"
            return False

    def rollback(self, generation_id: str) -> PolicyGeneration:
        with self._lock:
            target = self._validated.get(generation_id)
            if target is None:
                raise ValueError("policy generation has not been validated")
            self._current = target
            self._load_healthy = True
            self._load_outcome = "rollback"
            return self._current


def _validated_unique_names(value: list[str], *, field: str) -> list[str]:
    if len(value) != len(set(value)):
        raise ValueError(f"{field} values must be unique")
    for name in value:
        if not _POLICY_NAME_PATTERN.fullmatch(name):
            raise ValueError(f"{field} name is invalid")
    return value
