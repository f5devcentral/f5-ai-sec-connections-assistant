from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import shlex
from dataclasses import dataclass
from typing import Any

import httpx
import yaml

from .models import (
    AnalyzeContext,
    AnalyzeResponse,
    AuthType,
    CreateProviderRequest,
    CreateProviderResponse,
    DeleteProviderRequest,
    DeleteProviderResponse,
    DetectionResult,
    EndpointInput,
    GenerateYamlResponse,
    ProfileGenerationRequest,
    ProfileGenerationResponse,
    PromptTestRequest,
    PromptTestResponse,
    ValidateProfileYamlResponse,
    ValidateYamlResponse,
)

logger = logging.getLogger(__name__)


PRIVATE_CLUSTER_SUFFIX = ".svc.cluster.local"
SENSITIVE_KEY_PATTERN = re.compile(r"(token|secret|key|authorization|cookie|password)", re.IGNORECASE)
PROMPT_SENTINEL = "__CONNECTION_ASSISTANT_PROMPT_VAR__"
PATH_CANDIDATES: list[tuple[str, list[Any], str]] = [
    (
        "choices[0].message.content",
        ["choices", 0, "message", "content"],
        "response.json()?.choices?.[0]?.message?.content",
    ),
    (
        "output.message.content[0].text",
        ["output", "message", "content", 0, "text"],
        "response.json()?.output?.message?.content?.[0]?.text",
    ),
    (
        "data.output.final_response",
        ["data", "output", "final_response"],
        "response.json()?.data?.output?.final_response",
    ),
    (
        "data.output.output",
        ["data", "output", "output"],
        "response.json()?.data?.output?.output",
    ),
    (
        "result.response",
        ["result", "response"],
        "response.json()?.result?.response",
    ),
    (
        "responseDetail.response",
        ["responseDetail", "response"],
        "response.json()?.responseDetail?.response",
    ),
    ("answer", ["answer"], "response.json()?.answer"),
    ("message", ["message"], "response.json()?.message"),
    ("content", ["content"], "response.json()?.content"),
    ("text", ["text"], "response.json()?.text"),
]
TEMPLATE_BLOCK_PATTERN = re.compile(r"\{\{([\s\S]*?)\}\}")
TEMPLATE_QUOTED_KEY_PATTERN = re.compile(r'"[A-Za-z_][A-Za-z0-9_]*"\s*:')
PROFILE_SUPPORTED_PARSERS = {"auto", "sse", "multipart", "ndjson", "json", "text", "raw"}
PROFILE_STREAMING_TO_PARSER = {
    "none": "json",
    "sse": "sse",
    "websocket": "raw",
    "multipart": "multipart",
    "ndjson": "ndjson",
}
PROFILE_DEFAULT_TEXT_PATHS = [
    "data.delta.content.*.text",
    "responseMessage.content.*.text",
    "payload.data.stream.deltas.*.value",
    "choices.*.delta.content",
    "choices.*.message.content",
    "delta.content",
    "message.content",
    "message",
    "content",
    "text",
    "value",
    "answer",
]


@dataclass
class ParsedCurl:
    url: str | None = None
    method: str | None = None
    headers: dict[str, str] | None = None
    data: str | None = None
    multipart: bool = False


def _mask_value(value: str) -> str:
    if not value:
        return value
    if len(value) <= 8:
        return "***"
    return f"{value[:2]}***{value[-2:]}"


def _mask_dict(values: dict[str, str]) -> dict[str, str]:
    masked: dict[str, str] = {}
    for k, v in values.items():
        if SENSITIVE_KEY_PATTERN.search(k):
            masked[k] = _mask_value(v)
        elif isinstance(v, str) and re.match(r"^bearer\s+\S+", v, re.IGNORECASE):
            masked[k] = "Bearer ***"
        else:
            masked[k] = v
    return masked


def _authorization_value(token: str) -> str:
    stripped = token.strip()
    if re.match(r"^bearer\s+", stripped, re.IGNORECASE):
        return stripped
    return f"Bearer {stripped}"


def _parse_json(data: str | None) -> tuple[Any | None, bool]:
    if not data:
        return None, False
    candidate = data.strip()
    if not candidate:
        return None, False

    def _parse_nested_json(value: str) -> tuple[Any | None, bool]:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None, False

        # Some pasted bodies come through as JSON strings that themselves contain a JSON object.
        if isinstance(parsed, str):
            nested = parsed.strip()
            if nested.startswith("{") or nested.startswith("["):
                try:
                    return json.loads(nested), True
                except json.JSONDecodeError:
                    return parsed, True
        return parsed, True

    parsed, ok = _parse_nested_json(candidate)
    if ok:
        return parsed, True

    # Tolerate shell-style wrapping: '{"prompt":"Hello"}'
    if len(candidate) >= 2 and candidate[0] == "'" and candidate[-1] == "'":
        return _parse_nested_json(candidate[1:-1].strip())

    return None, False


def _looks_like_default_request_body(body: str | None) -> bool:
    parsed, ok = _parse_json(body)
    if not ok or not isinstance(parsed, dict):
        return False
    if set(parsed.keys()) != {"prompt"}:
        return False
    return parsed.get("prompt") == "Hello"


def _parse_header(header: str) -> tuple[str, str] | None:
    if ":" not in header:
        return None
    k, v = header.split(":", 1)
    key = k.strip().rstrip(":").strip()
    if not key:
        return None
    return key, v.strip()


def _sanitize_headers(headers: dict[str, Any] | None) -> dict[str, str]:
    if not headers:
        return {}

    out: dict[str, str] = {}
    canonical: dict[str, str] = {}
    for raw_key, raw_value in headers.items():
        if not isinstance(raw_key, str):
            continue
        key = raw_key.strip().rstrip(":").strip()
        if not key:
            continue

        value = "" if raw_value is None else str(raw_value).strip()
        lower = key.lower()
        previous_key = canonical.get(lower)
        if previous_key and previous_key != key:
            out.pop(previous_key, None)
        canonical[lower] = key
        out[key] = value

    return out


def _parse_raw_curl(raw_curl: str | None) -> ParsedCurl:
    if not raw_curl:
        return ParsedCurl(headers={})

    try:
        tokens = shlex.split(raw_curl)
    except ValueError:
        return ParsedCurl(headers={})

    method = None
    headers: dict[str, str] = {}
    data = None
    url = None
    multipart = False

    i = 0
    while i < len(tokens):
        token = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None

        if token == "-X" and nxt:
            method = nxt.upper()
            i += 2
            continue

        if token in {"-H", "--header"} and nxt:
            header = _parse_header(nxt)
            if header:
                headers[header[0]] = header[1]
            i += 2
            continue

        if token in {"-d", "--data", "--data-raw", "--data-binary"} and nxt:
            data = nxt
            i += 2
            continue

        if token in {"-F", "--form"}:
            multipart = True
            i += 2 if nxt else 1
            continue

        if token.startswith("http://") or token.startswith("https://"):
            url = token
            i += 1
            continue

        i += 1

    if not method:
        method = "POST" if data else "GET"

    return ParsedCurl(url=url, method=method, headers=headers, data=data, multipart=multipart)


def _is_private_or_local_url(url: str) -> bool:
    lower = url.lower()
    if "localhost" in lower or "127.0.0.1" in lower or PRIVATE_CLUSTER_SUFFIX in lower:
        return True

    host_match = re.search(r"https?://([^/:]+)", lower)
    if not host_match:
        return False

    host = host_match.group(1)
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback:
            return True
    except ValueError:
        pass

    if re.search(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", lower):
        return True
    if re.search(r"\b192\.168\.\d{1,3}\.\d{1,3}\b", lower):
        return True

    return False


def _extract_path_value(payload: Any, path: list[Any]) -> tuple[bool, Any | None]:
    current = payload
    for segment in path:
        if isinstance(segment, int):
            if not isinstance(current, list) or segment >= len(current):
                return False, None
            current = current[segment]
            continue

        if not isinstance(current, dict) or segment not in current:
            return False, None
        current = current[segment]

    if current is None:
        return False, None
    return True, current


def _looks_like_token_chunk_array(payload: Any) -> bool:
    if not isinstance(payload, list) or len(payload) < 3:
        return False

    dict_items = [item for item in payload if isinstance(item, dict)]
    if len(dict_items) < 3:
        return False

    chunk_count = 0
    assistant_roles = 0
    for item in dict_items:
        if "content" in item and isinstance(item.get("content"), str):
            chunk_count += 1
            if item.get("role") == "assistant":
                assistant_roles += 1

    if chunk_count < max(3, int(len(dict_items) * 0.6)):
        return False

    # Strong signal for tokenized/fragmented output.
    short_chunks = sum(
        1 for item in dict_items if isinstance(item.get("content"), str) and len(item.get("content", "")) <= 12
    )
    return assistant_roles >= 1 and short_chunks >= 3


def _detect_response_path(sample_json: Any) -> DetectionResult:
    for path_name, path_segments, expression in PATH_CANDIDATES:
        found, _ = _extract_path_value(sample_json, path_segments)
        if found:
            return DetectionResult(path=path_name, expression=expression, confident=True)

    return DetectionResult(
        path=None,
        expression="String.decode(response.body)",
        confident=False,
        reason="No known response content path was found in sample JSON.",
    )


def _safe_extract_response_text(resp_json: dict[str, Any]) -> str | None:
    if isinstance(resp_json.get("output_text"), str):
        return resp_json["output_text"]

    output = resp_json.get("output")
    if not isinstance(output, list):
        return None

    texts: list[str] = []
    for item in output:
        content = item.get("content") if isinstance(item, dict) else None
        if not isinstance(content, list):
            continue
        for chunk in content:
            if not isinstance(chunk, dict):
                continue
            if chunk.get("type") == "output_text" and isinstance(chunk.get("text"), str):
                texts.append(chunk["text"])

    return "\n".join(texts) if texts else None


def _build_openai_prompt(sample_json: Any) -> str:
    allowed = [item[0] for item in PATH_CANDIDATES]
    return (
        "Pick the best response content field path from the allowed list. "
        "Return JSON only with keys path and explanation. "
        "path must be one of allowed paths or null.\n"
        f"Allowed paths: {allowed}\n"
        f"Sample JSON: {json.dumps(sample_json, ensure_ascii=True)}"
    )


def _suggest_path_with_openai(sample_json: Any) -> DetectionResult | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    model = os.getenv("OPENAI_MODEL", "gpt-5-mini")
    body = {
        "model": model,
        "input": _build_openai_prompt(sample_json),
    }

    try:
        with httpx.Client(timeout=8.0) as client:
            response = client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
            )
            response.raise_for_status()
            raw = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenAI response-path suggestion failed: %s", exc)
        return None

    text = _safe_extract_response_text(raw)
    if not text:
        return None

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None

    candidate = parsed.get("path")
    explanation = parsed.get("explanation")
    if not isinstance(candidate, str):
        return None

    for path_name, _, expression in PATH_CANDIDATES:
        if path_name == candidate:
            return DetectionResult(path=path_name, expression=expression, confident=False, reason=explanation)

    return None


def _normalize_input(payload: EndpointInput) -> AnalyzeContext:
    parsed_curl = _parse_raw_curl(payload.raw_curl)

    endpoint_url = payload.endpoint_url or parsed_curl.url
    http_method = (payload.http_method or parsed_curl.method or "POST").upper()

    merged_headers: dict[str, str] = {}
    if parsed_curl.headers:
        merged_headers.update(parsed_curl.headers)
    merged_headers.update(payload.headers or {})
    merged_headers = _sanitize_headers(merged_headers)

    request_body = payload.request_body
    if parsed_curl.data and payload.raw_curl:
        if not request_body or not request_body.strip() or _looks_like_default_request_body(request_body):
            request_body = parsed_curl.data
    elif request_body is None:
        request_body = parsed_curl.data
    multipart_by_header = any(
        k.lower() == "content-type" and "multipart/form-data" in v.lower() for k, v in merged_headers.items()
    )

    normalized = EndpointInput(
        endpoint_url=endpoint_url,
        http_method=http_method,
        auth_type=payload.auth_type,
        headers=merged_headers,
        request_body=request_body,
        prompt_location=payload.prompt_location,
        response_content_path=payload.response_content_path,
        sample_success_response=payload.sample_success_response,
        sample_error_response=payload.sample_error_response,
        streaming_type=payload.streaming_type,
        raw_curl=payload.raw_curl,
        oauth=payload.oauth,
        requires_response_aggregation=payload.requires_response_aggregation,
    )

    success_json, success_json_valid = _parse_json(normalized.sample_success_response)
    return AnalyzeContext(
        normalized_input=normalized,
        parsed_success_json=success_json,
        success_json_valid=success_json_valid,
        multipart_required=parsed_curl.multipart or multipart_by_header,
    )


def _validate_json_input(context: AnalyzeContext, warnings: list[str], reasons: list[str]) -> None:
    if not context.normalized_input.sample_success_response:
        warnings.append("Sample success response is missing; response path detection may be unreliable.")
        return

    if not context.success_json_valid:
        reasons.append("Response is not valid JSON.")


def _contains_possible_secret(context: AnalyzeContext) -> bool:
    payload = context.normalized_input
    candidate_text = [
        payload.request_body or "",
        payload.raw_curl or "",
        payload.sample_error_response or "",
    ]
    for v in payload.headers.values():
        candidate_text.append(v)

    aggregate = " ".join(candidate_text)
    secret_patterns = [
        r"sk-[A-Za-z0-9]{16,}",
        r"Bearer\\s+[A-Za-z0-9_\\-\\.]{12,}",
        r"(?i)api[_-]?key\\s*[:=]\\s*[A-Za-z0-9_\\-]{8,}",
        r"(?i)client[_-]?secret\\s*[:=]\\s*[A-Za-z0-9_\\-]{8,}",
    ]
    return any(re.search(pattern, aggregate) for pattern in secret_patterns)


def _determine_decision(context: AnalyzeContext) -> tuple[str, list[str], list[str]]:
    reasons: list[str] = []
    warnings: list[str] = []
    payload = context.normalized_input

    if payload.streaming_type.value != "none":
        reasons.append(f"Streaming type '{payload.streaming_type.value}' requires proxy handling.")
        warnings.append("Streaming detected → Proxy required")

    if payload.auth_type == AuthType.oauth_private_key_jwt:
        reasons.append("Auth type oauth_private_key_jwt is not supported for direct connections in V1.")
        warnings.append("OAuth private key JWT not supported in V1")

    if payload.auth_type == AuthType.cookie_session:
        reasons.append("Cookie/session auth requires interactive state and is not supported directly in V1.")

    if payload.auth_type == AuthType.interactive:
        reasons.append("Interactive auth flow is not supported for direct connections in V1.")

    if payload.endpoint_url and _is_private_or_local_url(payload.endpoint_url):
        reasons.append("Endpoint is private or local and may require proxy/network access.")
        warnings.append("Private endpoint → may require network access")

    if context.multipart_required:
        reasons.append("Request requires multipart/form-data.")

    if payload.requires_response_aggregation:
        reasons.append("Response requires stream chunk aggregation.")

    _validate_json_input(context, warnings, reasons)
    if context.success_json_valid and _looks_like_token_chunk_array(context.parsed_success_json):
        reasons.append("Response must be aggregated (tokenized JSON chunks).")
        warnings.append("Chunked JSON response detected → use V2 profile flow")

    if _contains_possible_secret(context):
        warnings.append("Potential secrets detected in input. Remove or mask secrets before sharing.")

    if reasons:
        return "Proxy Required", reasons, warnings

    return "Direct Connection", ["All V1 direct-connection checks passed."], warnings


def _yaml_single_quote(value: str) -> str:
    return value.replace("'", "''")


def _path_to_env_name(header_name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", header_name).strip("_").lower()
    return normalized or "secret"


def _parse_location_path(path: str | None) -> list[str | int]:
    if not path:
        return []

    segments: list[str | int] = []
    token_pattern = re.compile(r"([a-zA-Z_][a-zA-Z0-9_]*)|\[(\d+)\]")

    for raw_part in path.split("."):
        part = raw_part.strip()
        if not part:
            return []

        idx = 0
        for match in token_pattern.finditer(part):
            if match.start() != idx:
                return []
            key, arr_idx = match.group(1), match.group(2)
            if key is not None:
                segments.append(key)
            else:
                segments.append(int(arr_idx))
            idx = match.end()

        if idx != len(part):
            return []

    return segments


def _path_segments_to_response_expression(path_segments: list[str | int]) -> str:
    expression = "response.json()?"
    for idx, segment in enumerate(path_segments):
        is_last = idx == len(path_segments) - 1
        if isinstance(segment, int):
            expression += f".[{segment}]"
        else:
            expression += f".{segment}"
        if not is_last:
            expression += "?"
    return expression


def _set_path_value(payload: Any, path: list[str | int], value: Any) -> bool:
    if not path:
        return False

    current = payload
    for segment in path[:-1]:
        if isinstance(segment, int):
            if not isinstance(current, list) or segment >= len(current):
                return False
            current = current[segment]
            continue

        if not isinstance(current, dict) or segment not in current:
            return False
        current = current[segment]

    last = path[-1]
    if isinstance(last, int):
        if not isinstance(current, list) or last >= len(current):
            return False
        current[last] = value
        return True

    if not isinstance(current, dict) or last not in current:
        return False
    current[last] = value
    return True


def _sanitize_profile_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip()).strip("_").lower()
    return cleaned or "generated_profile"


def _profile_parser_from_context(
    payload: EndpointInput,
    context: AnalyzeContext,
    parser_override: str | None,
    warnings: list[str],
) -> str:
    override = (parser_override or "").strip().lower()
    if override:
        if override not in PROFILE_SUPPORTED_PARSERS:
            warnings.append(
                f"Unsupported parser override '{parser_override}'. Falling back to deterministic parser mapping."
            )
        else:
            return override

    parser = PROFILE_STREAMING_TO_PARSER.get(payload.streaming_type.value, "json")
    if payload.streaming_type.value == "none":
        parser = "json" if context.success_json_valid else "text"
    if payload.streaming_type.value == "websocket":
        warnings.append("Websocket mapped to parser 'raw'. Validate upstream response handling.")
    return parser


def _replace_prompt_in_body(
    body_obj: Any,
    prompt_location: str | None,
) -> tuple[Any, bool]:
    if not isinstance(body_obj, (dict, list)):
        return body_obj, False

    prompt_path = _parse_location_path(prompt_location)
    replaced = _set_path_value(body_obj, prompt_path, "{{prompt}}") if prompt_path else False
    if not replaced and isinstance(body_obj, dict):
        for fallback_key in ("prompt", "question", "query", "input", "text", "message"):
            if fallback_key in body_obj:
                replaced = _set_path_value(body_obj, [fallback_key], "{{prompt}}")
                if replaced:
                    break
    return body_obj, replaced


def _profile_step_body(payload: EndpointInput, context: AnalyzeContext, warnings: list[str]) -> tuple[str, Any | None]:
    method = (payload.http_method or "POST").upper()
    raw = payload.request_body

    if context.multipart_required:
        if raw and raw.strip():
            return "multipart", raw
        return "multipart", "{{prompt}}"

    if not raw or not raw.strip():
        if method in {"POST", "PUT", "PATCH"}:
            return "json", {"prompt": "{{prompt}}"}
        return "none", None

    parsed, is_json = _parse_json(raw)
    if not is_json:
        return "text", raw

    parsed_copy = json.loads(json.dumps(parsed))
    replaced_body, replaced = _replace_prompt_in_body(parsed_copy, payload.prompt_location)
    if not replaced:
        warnings.append("Prompt location not found in request body; prompt variable was not injected.")
    return "json", replaced_body


def _profile_step_headers(payload: EndpointInput, body_mode: str) -> tuple[dict[str, str], bool]:
    headers: dict[str, str] = {}
    inject_bearer_token = False

    for k, v in payload.headers.items():
        lower = k.lower()
        if lower == "authorization":
            continue
        if lower in {"x-api-key", "api-key"}:
            continue
        if isinstance(v, str) and re.match(r"^bearer\s+\S+", v, re.IGNORECASE):
            var_name = _path_to_env_name(k)
            headers[k] = f"Bearer {{{{{var_name}}}}}"
            continue
        if SENSITIVE_KEY_PATTERN.search(k):
            var_name = _path_to_env_name(k)
            headers[k] = f"{{{{{var_name}}}}}"
            continue
        headers[k] = v

    headers.setdefault("Accept", "application/json")
    if body_mode == "json":
        headers.setdefault("Content-Type", "application/json")
    elif body_mode == "text":
        headers.setdefault("Content-Type", "text/plain")
    elif body_mode == "form":
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

    if payload.auth_type == AuthType.bearer_static:
        inject_bearer_token = True
    elif payload.auth_type == AuthType.api_key_header:
        header_name = "X-API-Key"
        for existing in payload.headers:
            if existing.lower() in {"x-api-key", "api-key"}:
                header_name = existing
                break
        headers[header_name] = "{{api_token}}"

    return headers, inject_bearer_token


def _profile_step_from_endpoint(
    payload: EndpointInput,
    context: AnalyzeContext,
    step_name: str,
    parser: str,
    warnings: list[str],
) -> dict[str, Any]:
    body_mode, body = _profile_step_body(payload, context, warnings)
    headers, inject_bearer_token = _profile_step_headers(payload, body_mode)

    step: dict[str, Any] = {
        "name": step_name,
        "method": (payload.http_method or "POST").upper(),
        "url": payload.endpoint_url or "",
        "headers": headers,
        "parser": parser,
        "inject_bearer_token": inject_bearer_token,
    }
    if body_mode != "none":
        step["body_mode"] = body_mode
    if body is not None:
        step["body"] = body

    return step


def _build_profile_structure(request: ProfileGenerationRequest) -> ProfileGenerationResponse:
    context = _normalize_input(request.endpoint)
    analysis = analyze_payload(request.endpoint)
    warnings = list(analysis.warnings)

    profile_name = _sanitize_profile_name(request.profile_name)
    step_name = _sanitize_profile_name(request.step_name or "target")
    result_step = _sanitize_profile_name(request.result_step or step_name)

    parser = _profile_parser_from_context(request.endpoint, context, request.parser_override, warnings)
    text_paths = request.default_text_paths or list(PROFILE_DEFAULT_TEXT_PATHS)
    if result_step not in {"target", step_name, "last"}:
        warnings.append(f"result_step '{result_step}' does not match generated steps; using '{step_name}'.")
        result_step = step_name

    profile: dict[str, Any] = {
        "result_step": result_step,
        "default_text_paths": text_paths,
        "steps": [],
    }
    if request.include_metadata:
        profile["_meta"] = {
            "generated_by": "connection-assistant-v2",
            "source_decision": analysis.decision,
        }

    if request.endpoint.auth_type == AuthType.oauth_client_credentials:
        oauth = request.endpoint.oauth
        token_url = oauth.token_url if oauth and oauth.token_url else "{{token_url}}"
        token_step = {
            "name": "token",
            "method": "POST",
            "url": token_url,
            "headers": {
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            "body_mode": "form",
            "body": {
                "grant_type": "client_credentials",
                "client_id": "{{client_id}}",
                "client_secret": "{{client_secret}}",
                "scope": "{{scope}}",
            },
            "parser": "json",
            "save": {
                "access_token": "access_token",
            },
            "inject_bearer_token": False,
        }

        target_step = _profile_step_from_endpoint(
            request.endpoint,
            context,
            step_name,
            parser,
            warnings,
        )
        target_headers = target_step.setdefault("headers", {})
        target_headers["Authorization"] = "Bearer {{access_token}}"
        target_step["inject_bearer_token"] = False

        profile["steps"] = [token_step, target_step]
        profile["result_step"] = step_name
    else:
        target_step = _profile_step_from_endpoint(
            request.endpoint,
            context,
            step_name,
            parser,
            warnings,
        )
        profile["steps"] = [target_step]
        profile["result_step"] = step_name if result_step == "target" else result_step

    profile_map = {profile_name: profile}
    profile_doc: dict[str, Any] = {
        "type": "proxy_profile_bundle",
        "version": "v2",
        "profiles": profile_map,
    }

    profile_yaml = yaml.safe_dump(profile_doc, sort_keys=False)
    return ProfileGenerationResponse(
        decision=analysis.decision,
        reasons=analysis.reasons,
        warnings=sorted(set(warnings)),
        profile_name=profile_name,
        profile_yaml=profile_yaml,
        profiles_json_fragment=profile_map,
    )


def _request_body_yaml_lines(payload: EndpointInput) -> list[str]:
    raw = payload.request_body
    if not raw:
        return ["        {}"]

    parsed, is_json = _parse_json(raw)
    if not is_json:
        return [f"        {row}" for row in raw.splitlines()] or ["        {}"]

    parsed_json = parsed
    prompt_path = _parse_location_path(payload.prompt_location)
    prompt_replaced = _set_path_value(parsed_json, prompt_path, PROMPT_SENTINEL) if prompt_path else False
    if not prompt_replaced and isinstance(parsed_json, dict):
        for fallback_key in ("prompt", "question", "query", "input", "text", "message"):
            if fallback_key in parsed_json:
                prompt_replaced = _set_path_value(parsed_json, [fallback_key], PROMPT_SENTINEL)
                if prompt_replaced:
                    break

    rendered = json.dumps(parsed_json, indent=2)
    if prompt_replaced:
        rendered = rendered.replace(f'"{PROMPT_SENTINEL}"', "prompt")

    # Keep JSON bodies in template-object form for consistency with workflow templates.
    rendered = re.sub(r'"([A-Za-z_][A-Za-z0-9_]*)":', r"\1:", rendered)
    expr_lines = rendered.splitlines() or ["{}"]

    if len(expr_lines) == 1:
        return [f"        {{{{ {expr_lines[0]} }}}}"]

    out = [f"        {{{{ {expr_lines[0]}"]
    out.extend([f"        {line}" for line in expr_lines[1:-1]])
    out.append(f"        {expr_lines[-1]} }}}}")
    return out


def _content_expression_lines(response_expr: str) -> list[str]:
    fallback = "String.decode(response.body)"
    if response_expr.strip() == fallback:
        return ["        content: '{{ String.decode(response.body) }}'"]

    return [
        "        content: >-",
        "          {{",
        "            response.statusCode == 200",
        f"              ? {response_expr} || String.decode(response.body)",
        "              : String.decode(response.body)",
        "          }}",
    ]


def _extra_headers_from_input(
    payload: EndpointInput,
    *,
    include_authorization: bool,
    include_api_key: bool,
) -> list[str]:
    lines: list[str] = []
    for k, v in payload.headers.items():
        lower = k.lower()
        if lower in {"accept", "content-type"}:
            continue

        if lower == "authorization":
            if include_authorization:
                if v.lower().startswith("bearer "):
                    lines.append("        Authorization: Bearer {{ apiKey }}")
                else:
                    lines.append("        Authorization: '{{ authHeader }}'")
            continue

        if lower in {"x-api-key", "api-key"}:
            if include_api_key:
                lines.append(f"        {k}: '{{{{ apiKey }}}}'")
            continue

        if SENSITIVE_KEY_PATTERN.search(k):
            var_name = _path_to_env_name(k)
            lines.append(f"        {k}: '{{{{ {var_name} }}}}'")
            continue

        lines.append(f"        {k}: '{_yaml_single_quote(v)}'")

    return lines


def _auth_headers_for_yaml(payload: EndpointInput) -> list[str]:
    lines: list[str] = []
    if payload.auth_type == AuthType.api_key_header:
        lines.append("        X-API-Key: '{{ apiKey }}'")
    elif payload.auth_type == AuthType.bearer_static:
        lines.append("        Authorization: Bearer {{ apiKey }}")
    lines.extend(
        _extra_headers_from_input(
            payload,
            include_authorization=payload.auth_type != AuthType.bearer_static,
            include_api_key=payload.auth_type != AuthType.api_key_header,
        )
    )
    return lines


def _build_direct_yaml(payload: EndpointInput, detection: DetectionResult) -> str:
    method = payload.http_method or "POST"
    endpoint = payload.endpoint_url or "{{ endpoint_url }}"
    request_json_lines = _request_body_yaml_lines(payload)
    response_expr = detection.expression or "String.decode(response.body)"

    auth_header_lines = _auth_headers_for_yaml(payload)
    header_lines = [
        "      headers:",
        "        Accept: application/json",
        "        Content-Type: application/json",
    ]
    header_lines.extend(auth_header_lines)

    lines = [
        "type: workflow",
        "outputs:",
        "  content: '{{ content }}'",
        "  statusCode: '{{ statusCode }}'",
        "  error: '{{ error }}'",
        "  responseBody: '{{ responseBody }}'",
        "stages:",
        "  - type: retry",
        "    attempts: 3",
        "    backoff: '{{ 2 * attempt }}'",
        "    block:",
        "      type: request",
        f"      method: {method}",
        "      timeout: 300",
        f"      url: {endpoint}",
    ]
    lines.extend(header_lines)
    lines.extend(
        [
            "      queryParams: {}",
            "      json: |-",
        ]
    )

    lines.extend(request_json_lines)

    lines.append("      outputs:")
    lines.extend(_content_expression_lines(response_expr))
    lines.extend(
        [
            "        statusCode: '{{ response.statusCode }}'",
            "        error: '{{ response.statusCode == 200 ? null : String.decode(response.body) }}'",
            "        responseBody: '{{ String.decode(response.body) }}'",
            "    when: '{{ Array.contains([429, 500, 502, 503, 504], statusCode) }}'",
        ]
    )

    return "\n".join(lines)


def _build_oauth_yaml(payload: EndpointInput, detection: DetectionResult) -> str:
    oauth = payload.oauth
    token_url = oauth.token_url if oauth and oauth.token_url else "{{ token_url }}"
    endpoint = payload.endpoint_url or "{{ endpoint_url }}"
    request_json_lines = _request_body_yaml_lines(payload)
    response_expr = detection.expression or "String.decode(response.body)"

    request_body = (
        "grant_type=client_credentials&client_id={{ client_id }}"
        "&client_secret={{ client_secret }}&scope={{ scope }}"
    )

    lines = [
        "type: workflow",
        "outputs:",
        "  content: '{{ content }}'",
        "  statusCode: '{{ statusCode }}'",
        "  error: '{{ error }}'",
        "  responseBody: '{{ responseBody }}'",
        "stages:",
        "  - type: request",
        "    method: POST",
        "    timeout: 60",
        f"    url: {token_url}",
        "    headers:",
        "      Content-Type: application/x-www-form-urlencoded",
        "      Accept: application/json",
        "    body: |-",
        f"      {request_body}",
        "    outputs:",
        "      access_token: '{{ response.json()?.access_token }}'",
        "  - type: retry",
        "    attempts: 3",
        "    backoff: '{{ 2 * attempt }}'",
        "    block:",
        "      type: request",
        f"      method: {payload.http_method or 'POST'}",
        "      timeout: 300",
        f"      url: {endpoint}",
        "      headers:",
        "        Accept: application/json",
        "        Content-Type: application/json",
        "        Authorization: 'Bearer {{ access_token }}'",
    ]
    lines.extend(
        _extra_headers_from_input(
            payload,
            include_authorization=False,
            include_api_key=False,
        )
    )
    lines.extend(["      queryParams: {}", "      json: |-"])

    lines.extend(request_json_lines)

    lines.append("      outputs:")
    lines.extend(_content_expression_lines(response_expr))
    lines.extend(
        [
            "        statusCode: '{{ response.statusCode }}'",
            "        error: '{{ response.statusCode == 200 ? null : String.decode(response.body) }}'",
            "        responseBody: '{{ String.decode(response.body) }}'",
            "    when: '{{ Array.contains([429, 500, 502, 503, 504], statusCode) }}'",
        ]
    )

    return "\n".join(lines)


def analyze_payload(payload: EndpointInput) -> AnalyzeResponse:
    context = _normalize_input(payload)
    decision, reasons, warnings = _determine_decision(context)

    detection = DetectionResult(path=None, expression="String.decode(response.body)", confident=False)
    override_applied = False
    user_override_path = (context.normalized_input.response_content_path or "").strip()
    if user_override_path:
        override_segments = _parse_location_path(user_override_path)
        if not override_segments:
            warnings.append("Provided response path format is invalid; using auto-detection.")
        else:
            override_expression = _path_segments_to_response_expression(override_segments)
            detection = DetectionResult(path=user_override_path, expression=override_expression, confident=False)
            override_applied = True
            if context.success_json_valid and context.parsed_success_json is not None:
                found, _ = _extract_path_value(context.parsed_success_json, override_segments)
                if found:
                    detection = DetectionResult(path=user_override_path, expression=override_expression, confident=True)
                else:
                    warnings.append("Provided response path not found in sample response; using provided path anyway.")

    if not override_applied and context.success_json_valid and context.parsed_success_json is not None:
        detection = _detect_response_path(context.parsed_success_json)
        if not detection.confident:
            ai_suggestion = _suggest_path_with_openai(context.parsed_success_json)
            if ai_suggestion:
                detection = ai_suggestion

    if not detection.confident:
        warnings.append("Response path not confidently detected")

    masked_headers = _mask_dict(context.normalized_input.headers)
    logger.info(
        "Analyze request: endpoint=%s method=%s auth=%s headers=%s",
        context.normalized_input.endpoint_url,
        context.normalized_input.http_method,
        context.normalized_input.auth_type,
        masked_headers,
    )

    return AnalyzeResponse(
        decision=decision,
        reasons=reasons,
        warnings=sorted(set(warnings)),
        detected_response_path=detection.path,
        response_path_expression=detection.expression,
        response_path_confident=detection.confident,
    )


def generate_yaml(payload: EndpointInput) -> GenerateYamlResponse:
    context = _normalize_input(payload)
    analysis = analyze_payload(payload)

    if analysis.decision != "Direct Connection":
        return GenerateYamlResponse(
            decision=analysis.decision,
            reasons=analysis.reasons,
            warnings=analysis.warnings,
            yaml=None,
            proxy_placeholder="Proxy support coming in V2",
            detected_response_path=analysis.detected_response_path,
            response_path_expression=analysis.response_path_expression,
            response_path_confident=analysis.response_path_confident,
        )

    if not context.success_json_valid:
        analysis.warnings.append("Sample success response JSON is invalid; using fallback content extraction.")

    detection = DetectionResult(
        path=analysis.detected_response_path,
        expression=analysis.response_path_expression or "String.decode(response.body)",
        confident=analysis.response_path_confident,
    )

    if payload.auth_type == AuthType.oauth_client_credentials:
        rendered_yaml = _build_oauth_yaml(context.normalized_input, detection)
    else:
        rendered_yaml = _build_direct_yaml(context.normalized_input, detection)

    return GenerateYamlResponse(
        decision=analysis.decision,
        reasons=analysis.reasons,
        warnings=sorted(set(analysis.warnings)),
        yaml=rendered_yaml,
        proxy_placeholder=None,
        detected_response_path=analysis.detected_response_path,
        response_path_expression=analysis.response_path_expression,
        response_path_confident=analysis.response_path_confident,
    )


def generate_profile_yaml(request: ProfileGenerationRequest) -> ProfileGenerationResponse:
    return _build_profile_structure(request)


def _extract_provider_id(response_body: Any) -> str | None:
    if isinstance(response_body, dict):
        for key in ("id", "providerId", "provider_id"):
            value = response_body.get(key)
            if isinstance(value, (str, int)):
                return str(value)

        provider_obj = response_body.get("provider")
        if isinstance(provider_obj, dict):
            for key in ("id", "providerId", "provider_id"):
                value = provider_obj.get(key)
                if isinstance(value, (str, int)):
                    return str(value)

    return None


def validate_yaml_template(yaml_text: str) -> ValidateYamlResponse:
    errors: list[str] = []
    warnings: list[str] = []
    workflow_type: str | None = None
    stage_count: int | None = None

    if not yaml_text or not yaml_text.strip():
        return ValidateYamlResponse(valid=False, errors=["YAML is empty."])

    if yaml_text.count("{{") != yaml_text.count("}}"):
        errors.append("Template delimiter mismatch: number of '{{' and '}}' markers does not match.")

    for block in TEMPLATE_BLOCK_PATTERN.finditer(yaml_text):
        content = block.group(1)
        if "{" not in content:
            continue
        if TEMPLATE_QUOTED_KEY_PATTERN.search(content):
            errors.append(
                "Template object literal uses quoted keys inside '{{ ... }}'. "
                "Use unquoted keys (for example question: prompt)."
            )
            break

    parsed_yaml: Any
    try:
        parsed_yaml = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        errors.append(f"YAML parse error: {exc}")
        return ValidateYamlResponse(valid=False, errors=errors, warnings=warnings)

    if not isinstance(parsed_yaml, dict):
        errors.append("YAML root must be an object.")
        return ValidateYamlResponse(valid=False, errors=errors, warnings=warnings)

    workflow_type = parsed_yaml.get("type")
    if workflow_type != "workflow":
        warnings.append("Top-level 'type' is not 'workflow'.")

    stages = parsed_yaml.get("stages")
    if not isinstance(stages, list) or not stages:
        errors.append("Top-level 'stages' must be a non-empty list.")
    else:
        stage_count = len(stages)

    outputs = parsed_yaml.get("outputs")
    if not isinstance(outputs, dict):
        warnings.append("Top-level 'outputs' is missing or not an object.")

    return ValidateYamlResponse(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        stage_count=stage_count,
        workflow_type=str(workflow_type) if workflow_type is not None else None,
    )


def validate_profile_yaml(profile_yaml_text: str) -> ValidateProfileYamlResponse:
    errors: list[str] = []
    warnings: list[str] = []

    if not profile_yaml_text or not profile_yaml_text.strip():
        return ValidateProfileYamlResponse(valid=False, errors=["Profile YAML is empty."])

    parsed: Any
    try:
        parsed = yaml.safe_load(profile_yaml_text)
    except yaml.YAMLError as exc:
        return ValidateProfileYamlResponse(valid=False, errors=[f"YAML parse error: {exc}"])

    if not isinstance(parsed, dict):
        return ValidateProfileYamlResponse(valid=False, errors=["Profile YAML root must be an object."])

    profiles = parsed.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        errors.append("Top-level 'profiles' must be a non-empty object.")
        return ValidateProfileYamlResponse(valid=False, errors=errors, warnings=warnings)

    first_name = next(iter(profiles.keys()))
    first_profile = profiles[first_name]
    if not isinstance(first_profile, dict):
        errors.append(f"Profile '{first_name}' must be an object.")
        return ValidateProfileYamlResponse(valid=False, errors=errors, warnings=warnings)

    steps = first_profile.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append(f"Profile '{first_name}' must include a non-empty steps list.")
        return ValidateProfileYamlResponse(valid=False, errors=errors, warnings=warnings, profile_name=first_name)

    parser_name: str | None = None
    first_step = steps[0]
    if isinstance(first_step, dict):
        parser_value = first_step.get("parser")
        if isinstance(parser_value, str):
            parser_name = parser_value
            if parser_value not in PROFILE_SUPPORTED_PARSERS:
                warnings.append(
                    f"Parser '{parser_value}' is not in supported set {sorted(PROFILE_SUPPORTED_PARSERS)}."
                )
        else:
            warnings.append("First step parser missing; proxy may fall back to auto parser behavior.")

    return ValidateProfileYamlResponse(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        profile_name=first_name,
        step_count=len(steps),
        parser=parser_name,
    )


def create_provider_from_yaml(request: CreateProviderRequest) -> CreateProviderResponse:
    validation = validate_yaml_template(request.yaml)
    if not validation.valid:
        return CreateProviderResponse(
            success=False,
            status_code=None,
            message="YAML validation failed. Fix errors before creating provider.",
            errors=validation.errors,
            response_body={"warnings": validation.warnings},
        )

    if not request.api_token.strip():
        return CreateProviderResponse(success=False, status_code=None, message="API token is required.")
    if not request.provider_name.strip():
        return CreateProviderResponse(success=False, status_code=None, message="Provider name is required.")

    try:
        template = yaml.safe_load(request.yaml)
    except yaml.YAMLError as exc:
        return CreateProviderResponse(success=False, status_code=None, message=f"YAML parse error: {exc}")

    inputs: dict[str, Any] = {}
    if request.inputs_json and request.inputs_json.strip():
        raw_inputs = request.inputs_json.strip()
        parsed_inputs: Any = None
        parse_error_message: str | None = None

        try:
            parsed_inputs = json.loads(raw_inputs)
        except json.JSONDecodeError as json_exc:
            try:
                parsed_inputs = yaml.safe_load(raw_inputs)
            except yaml.YAMLError as yaml_exc:
                parse_error_message = (
                    "Inputs parse error. Provide a JSON object or YAML mapping. "
                    f"JSON error: {json_exc}. YAML error: {yaml_exc}"
                )

        if parse_error_message:
            return CreateProviderResponse(
                success=False,
                status_code=None,
                message=parse_error_message,
            )

        if parsed_inputs is None:
            parsed_inputs = {}

        if not isinstance(parsed_inputs, dict):
            return CreateProviderResponse(
                success=False,
                status_code=None,
                message="Inputs must be an object/mapping when provided.",
            )
        inputs = parsed_inputs

    payload = {
        "name": request.provider_name.strip(),
        "template": template,
        "inputs": inputs,
        "test": request.run_test,
    }

    url = f"{request.base_url.rstrip('/')}/providers"
    token_preview = _mask_value(request.api_token)
    logger.info("Create provider request: base_url=%s provider_name=%s token=%s", request.base_url, request.provider_name, token_preview)

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {request.api_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except Exception as exc:  # noqa: BLE001
        return CreateProviderResponse(
            success=False,
            status_code=None,
            message=f"Provider API request failed: {exc}",
        )

    response_body: Any
    try:
        response_body = response.json()
    except Exception:  # noqa: BLE001
        response_body = response.text

    provider_id = _extract_provider_id(response_body)
    success = 200 <= response.status_code < 300
    message = (
        "Provider created successfully."
        if success
        else f"Provider API returned HTTP {response.status_code}."
    )

    return CreateProviderResponse(
        success=success,
        status_code=response.status_code,
        message=message,
        errors=[],
        response_body=response_body,
        provider_id=provider_id,
    )


def delete_provider(request: DeleteProviderRequest) -> DeleteProviderResponse:
    provider_id = request.provider_id.strip()
    api_token = request.api_token.strip()
    base_url = request.base_url.strip()

    if not provider_id:
        return DeleteProviderResponse(success=False, status_code=None, message="Provider ID is required.")
    if not api_token:
        return DeleteProviderResponse(success=False, status_code=None, message="API token is required.")
    if not base_url:
        return DeleteProviderResponse(success=False, status_code=None, message="Base URL is required.")

    url = f"{base_url.rstrip('/')}/providers/{provider_id}"
    token_preview = _mask_value(api_token)
    logger.info("Delete provider request: base_url=%s provider_id=%s token=%s", base_url, provider_id, token_preview)

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.delete(
                url,
                headers={
                    "Authorization": api_token,
                },
            )
    except Exception as exc:  # noqa: BLE001
        return DeleteProviderResponse(
            success=False,
            status_code=None,
            message=f"Provider delete request failed: {exc}",
            provider_id=provider_id,
        )

    response_body: Any
    try:
        response_body = response.json()
    except Exception:  # noqa: BLE001
        response_body = response.text

    success = 200 <= response.status_code < 300
    message = (
        "Provider deleted successfully."
        if success
        else f"Provider delete returned HTTP {response.status_code}."
    )

    return DeleteProviderResponse(
        success=success,
        status_code=response.status_code,
        message=message,
        errors=[],
        response_body=response_body,
        provider_id=provider_id,
    )


def test_provider_prompt(request: PromptTestRequest) -> PromptTestResponse:
    api_token = request.api_token.strip()
    base_url = request.base_url.strip()
    prompt = request.prompt.strip()
    provider = (request.provider or "").strip()

    if not api_token:
        return PromptTestResponse(success=False, status_code=None, message="API token is required.")
    if not base_url:
        return PromptTestResponse(success=False, status_code=None, message="Base URL is required.")
    if not prompt:
        return PromptTestResponse(success=False, status_code=None, message="Prompt text is required.")
    if not provider:
        return PromptTestResponse(success=False, status_code=None, message="Provider is required.")

    url = f"{base_url.rstrip('/')}/prompts"
    token_preview = _mask_value(api_token)
    logger.info("Prompt test request: base_url=%s provider=%s token=%s", base_url, provider, token_preview)

    payload: dict[str, Any] = {
        "input": prompt,
        "provider": provider,
        "verbose": bool(request.verbose),
    }
    if request.external_metadata:
        payload["externalMetadata"] = request.external_metadata

    try:
        with httpx.Client(timeout=45.0) as client:
            response = client.post(
                url,
                headers={
                    "Authorization": _authorization_value(api_token),
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except Exception as exc:  # noqa: BLE001
        return PromptTestResponse(
            success=False,
            status_code=None,
            message=f"Prompt API request failed: {exc}",
            provider=provider,
        )

    response_body: Any
    try:
        response_body = response.json()
    except Exception:  # noqa: BLE001
        response_body = response.text

    outcome: str | None = None
    prompt_response: Any | None = None
    if isinstance(response_body, dict):
        result = response_body.get("result")
        if isinstance(result, dict):
            outcome_value = result.get("outcome")
            if isinstance(outcome_value, str):
                outcome = outcome_value
            prompt_response = result.get("response")

    success = 200 <= response.status_code < 300
    if success and outcome:
        message = f"Prompt test completed (outcome: {outcome})."
    elif success:
        message = "Prompt test completed."
    else:
        message = f"Prompt API returned HTTP {response.status_code}."

    return PromptTestResponse(
        success=success,
        status_code=response.status_code,
        message=message,
        errors=[],
        response_body=response_body,
        provider=provider,
        outcome=outcome,
        prompt_response=prompt_response,
    )
