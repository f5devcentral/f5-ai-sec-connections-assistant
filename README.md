# Connection Assistant (V1)

Connection Assistant (V1) is a stateless FastAPI + React web app that helps solutions engineers determine whether an AI endpoint can use a **Direct Connection** in CalypsoAI, or must be flagged as **Proxy Required**.

If the endpoint is direct-compatible, the app generates deterministic CalypsoAI workflow YAML.

## Features

- Deterministic compatibility decision engine
- Output decision: `Direct Connection` or `Proxy Required`
- Explicit reasons and warnings
- Manual response path override with inline examples
- YAML validation preview before deployment
- YAML generation for:
  - No auth
  - API key header
  - Bearer token (static)
  - OAuth2 Client Credentials (2-stage flow)
- Optional provider creation test via API (create provider from generated YAML and report status)
- Optional prompt test via Prompt API (`/prompts`) to quickly validate created providers
- V2 profile generation flow for proxy-style integrations (YAML + `PROFILES_JSON` fragment)
- Optional `curl` parsing to prefill missing values
- Optional OpenAI fallback (`OPENAI_API_KEY`) only for response-path suggestion when deterministic detection fails
- Stateless: no database, no persistence

## API Endpoints

- `GET /health`
- `POST /analyze`
- `POST /generate-yaml`
- `POST /validate-yaml`
- `POST /generate-profile-yaml`
- `POST /validate-profile-yaml`
- `POST /create-provider`
- `POST /delete-provider`
- `POST /test-provider-prompt`

## Deterministic Decision Rules

Returns `Proxy Required` if any condition is true:

- `streaming_type != none`
- `auth_type == oauth_private_key_jwt`
- `auth_type == cookie_session`
- `auth_type == interactive`
- endpoint URL is local/private (`localhost`, `127.0.0.1`, `10.x.x.x`, `192.168.x.x`, `*.svc.cluster.local`)
- request requires `multipart/form-data`
- sample success response is not valid JSON
- response requires aggregation of stream chunks

Otherwise returns `Direct Connection`.

## Response Path Detection Order

The backend checks these paths in order:

1. `choices[0].message.content`
2. `output.message.content[0].text`
3. `data.output.final_response`
4. `data.output.output`
5. `result.response`
6. `responseDetail.response`
7. `answer`
8. `message`
9. `content`
10. `text`
11. Fallback to `String.decode(response.body)`

## Local Run

### 1) Backend

```bash
cd /path/to/f5-ai-sec-connections-assistant
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

### 2) Frontend (dev server)

```bash
cd /path/to/f5-ai-sec-connections-assistant/frontend
npm install
npm run dev
```

Frontend runs on `http://127.0.0.1:5173` and proxies API requests to `http://127.0.0.1:8000`.

## Docker Run

```bash
cd /path/to/f5-ai-sec-connections-assistant
docker build -t connection-assistant-v1 .
docker run --rm -p 10000:10000 connection-assistant-v1
```

Open `http://127.0.0.1:10000`.

## Deploy to Render

### Option A: Blueprint (`render.yaml`)

1. Push this folder to GitHub.
2. In Render: **New +** -> **Blueprint**.
3. Select repo and deploy.
4. (Optional) Set `OPENAI_API_KEY` env var.

### Option B: Manual Web Service

1. Create new **Web Service**.
2. Environment: `Docker`.
3. Select repo.
4. Health check path: `/health`.
5. (Optional) Add env vars:
   - `OPENAI_API_KEY`
   - `OPENAI_MODEL` (default `gpt-5-mini`)

## Example Input

```json
{
  "endpoint_url": "https://api.example.com/v1/chat/completions",
  "http_method": "POST",
  "auth_type": "bearer_static",
  "headers": {
    "X-App-Id": "demo"
  },
  "request_body": "{\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}]}",
  "prompt_location": "messages[0].content",
  "response_content_path": "choices[0].message.content",
  "sample_success_response": "{\"choices\":[{\"message\":{\"content\":\"Hello there\"}}]}",
  "streaming_type": "none"
}
```

## Example Output (Direct)

- Decision: `Direct Connection`
- Reason: `All V1 direct-connection checks passed.`
- YAML generated with retry request workflow and detected response path.

## Example Output (Proxy)

- Decision: `Proxy Required`
- Reasons may include:
  - `Streaming type 'sse' requires proxy handling.`
  - `Request requires multipart/form-data.`
- Placeholder message:
  - `Proxy support coming in V2`

## V2 Profile Flow

Use the **Generate Profile (V2)** action in the UI to create a proxy profile bundle for profile-based routers/loaders.

The generator returns:

- `profile_yaml`: YAML bundle with `type`, `version`, and `profiles` map
- `profiles_json_fragment`: JSON object you can merge into `PROFILES_JSON`

Authentication behavior in generated profile steps:

- `bearer_static`: `inject_bearer_token: true`
- `api_key_header`: emits `X-API-Key: "{{api_token}}"` (or source key variant)
- `oauth_client_credentials`: emits deterministic 2-step token flow (`token` + target step)

## Notes

- The app does **not** generate proxy configuration in V1.
- User data is never persisted.
- Secrets are masked in logs where possible, and warnings are shown if likely secrets are detected in user input.
- If `prompt_location` matches a field in `request_body`, YAML generation replaces that field with the workflow variable `prompt` instead of hardcoded text.
- Authorization and API-key headers are emitted as variable placeholders (for example `Authorization: Bearer {{ apiKey }}`) instead of fixed secrets.
- You can set `response_content_path` (for example `message` or `retrieved[0].name`) to force the extraction path used for `outputs.content`.
- If `response_content_path` is syntactically valid but not found in sample JSON, the generator still uses it and adds a warning.
- Provider creation test uses `POST {base_url}/providers` with payload fields `name`, `template`, `inputs`, and `test`.
- Provider inputs field accepts either JSON objects or YAML mappings.
- Provider deletion uses `DELETE {base_url}/providers/{provider_id}` and sends the `Authorization` header exactly as entered.
- Prompt test uses `POST {base_url}/prompts` with payload `{ input, provider, verbose }` and returns status/outcome/response.
- V2 profile generator outputs a proxy profile bundle YAML:
  - top-level `profiles` map keyed by profile name
  - step definitions compatible with profile-loader patterns (`PROFILES_FILE`, `PROFILES_JSON`)
  - parser mapping inferred from streaming type (`none->json/text`, `sse->sse`, `ndjson->ndjson`, `multipart->multipart`, `websocket->raw`)
