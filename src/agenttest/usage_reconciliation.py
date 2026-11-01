"""Trusted local support receipts; there is intentionally no model or browser write API."""

from typing import Literal

from pydantic import Field, model_validator

from agenttest.safety import StrictModel


class ProviderUsageReceipt(StrictModel):
    call_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    task_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source: Literal["OPERATOR_VERIFIED_PROVIDER_RECORD"]
    provider_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,120}$")
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    provider_confirmed_not_received: bool = False

    @model_validator(mode="after")
    def consistent(self):
        from agenttest.onboarding import _public_metadata

        _public_metadata(self.provider_reference)
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("USAGE_RECEIPT_TOTAL_MISMATCH")
        if self.provider_confirmed_not_received != (self.total_tokens == 0):
            raise ValueError("ZERO_USAGE_REQUIRES_PROVIDER_CONFIRMATION")
        return self


class UnknownUsageException(StrictModel):
    call_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    task_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source: Literal["EXPLICIT_OPERATOR_EXCEPTION"]
    authorization_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: Literal["THIS_CALL_ONLY_PRESERVE_UNKNOWN"]
