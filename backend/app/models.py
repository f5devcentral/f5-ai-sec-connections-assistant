from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AuthType(str, Enum):
    none = "none"
    api_key_header = "api_key_header"
    bearer_static = "bearer_static"
    oauth_client_credentials = "oauth_client_credentials"
    oauth_private_key_jwt = "oauth_private_key_jwt"
    cookie_session = "cookie_session"
    interactive = "interactive"


class StreamingType(str, Enum):
    none = "none"
    sse = "sse"
    websocket = "websocket"
    multipart = "multipart"
    ndjson = "ndjson"


class OAuthConfig(BaseModel):
    token_url: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    scope: str | None = None


class EndpointInput(BaseModel):
    endpoint_url: str | None = None
    http_method: str = "POST"
    auth_type: AuthType = AuthType.none
    headers: dict[str, str] = Field(default_factory=dict)
    request_body: str | None = None
    prompt_location: str | None = None
    response_content_path: str | None = None
    sample_success_response: str | None = None
    sample_error_response: str | None = None
    streaming_type: StreamingType = StreamingType.none
    raw_curl: str | None = None
    oauth: OAuthConfig | None = None
    requires_response_aggregation: bool = False


class AnalyzeResponse(BaseModel):
    decision: str
    reasons: list[str]
    warnings: list[str]
    detected_response_path: str | None = None
    response_path_expression: str | None = None
    response_path_confident: bool = False


class GenerateYamlResponse(BaseModel):
    decision: str
    reasons: list[str]
    warnings: list[str]
    yaml: str | None = None
    proxy_placeholder: str | None = None
    detected_response_path: str | None = None
    response_path_expression: str | None = None
    response_path_confident: bool = False


class ValidateYamlRequest(BaseModel):
    yaml: str


class ValidateYamlResponse(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    stage_count: int | None = None
    workflow_type: str | None = None


class CreateProviderRequest(BaseModel):
    yaml: str
    provider_name: str
    api_token: str
    base_url: str = "https://us1.calypsoai.app/backend/v1"
    inputs_json: str | None = None
    run_test: bool = True


class CreateProviderResponse(BaseModel):
    success: bool
    status_code: int | None = None
    message: str
    errors: list[str] = Field(default_factory=list)
    response_body: Any | None = None
    provider_id: str | None = None


class DeleteProviderRequest(BaseModel):
    provider_id: str
    api_token: str
    base_url: str = "https://us1.calypsoai.app/backend/v1"


class DeleteProviderResponse(BaseModel):
    success: bool
    status_code: int | None = None
    message: str
    errors: list[str] = Field(default_factory=list)
    response_body: Any | None = None
    provider_id: str | None = None


class PromptTestRequest(BaseModel):
    api_token: str
    base_url: str = "https://us1.calypsoai.app/backend/v1"
    prompt: str
    provider: str | None = None
    verbose: bool = True
    external_metadata: dict[str, Any] | None = None


class PromptTestResponse(BaseModel):
    success: bool
    status_code: int | None = None
    message: str
    errors: list[str] = Field(default_factory=list)
    response_body: Any | None = None
    provider: str | None = None
    outcome: str | None = None
    prompt_response: Any | None = None


class ProfileGenerationRequest(BaseModel):
    endpoint: EndpointInput
    profile_name: str = "generated_profile"
    step_name: str = "target"
    result_step: str = "target"
    default_text_paths: list[str] = Field(default_factory=list)
    parser_override: str | None = None
    include_metadata: bool = True


class ProfileGenerationResponse(BaseModel):
    decision: str
    reasons: list[str]
    warnings: list[str]
    profile_name: str
    profile_yaml: str
    profiles_json_fragment: dict[str, Any]


class ValidateProfileYamlRequest(BaseModel):
    profile_yaml: str


class ValidateProfileYamlResponse(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    profile_name: str | None = None
    step_count: int | None = None
    parser: str | None = None


class DetectionResult(BaseModel):
    path: str | None = None
    expression: str | None = None
    confident: bool = False
    reason: str | None = None


class AnalyzeContext(BaseModel):
    normalized_input: EndpointInput
    parsed_success_json: Any | None = None
    success_json_valid: bool = False
    multipart_required: bool = False
