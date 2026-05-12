from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .logic import (
    analyze_payload,
    create_provider_from_yaml,
    delete_provider,
    generate_profile_yaml,
    generate_yaml,
    test_provider_prompt,
    validate_profile_yaml,
    validate_yaml_template,
)
from .models import (
    CreateProviderRequest,
    DeleteProviderRequest,
    EndpointInput,
    ProfileGenerationRequest,
    PromptTestRequest,
    ValidateProfileYamlRequest,
    ValidateYamlRequest,
)

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Connection Assistant (V1)", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze")
def analyze(input_data: EndpointInput):
    return analyze_payload(input_data)


@app.post("/generate-yaml")
def generate(input_data: EndpointInput):
    return generate_yaml(input_data)


@app.post("/generate-profile-yaml")
def generate_profile(input_data: ProfileGenerationRequest):
    return generate_profile_yaml(input_data)


@app.post("/validate-yaml")
def validate_yaml(input_data: ValidateYamlRequest):
    return validate_yaml_template(input_data.yaml)


@app.post("/validate-profile-yaml")
def validate_profile(input_data: ValidateProfileYamlRequest):
    return validate_profile_yaml(input_data.profile_yaml)


@app.post("/create-provider")
def create_provider(input_data: CreateProviderRequest):
    return create_provider_from_yaml(input_data)


@app.post("/delete-provider")
def delete_provider_route(input_data: DeleteProviderRequest):
    return delete_provider(input_data)


@app.post("/test-provider-prompt")
def test_provider_prompt_route(input_data: PromptTestRequest):
    return test_provider_prompt(input_data)


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    @app.get("/")
    def root() -> FileResponse:
        return FileResponse(frontend_dist / "index.html")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str) -> FileResponse:  # noqa: ARG001
        return FileResponse(frontend_dist / "index.html")
