import { useMemo, useState } from 'react'

const defaultHeaders = [{ key: '', value: '' }]

const authOptions = [
  { value: 'none', label: 'None' },
  { value: 'api_key_header', label: 'API Key (header)' },
  { value: 'bearer_static', label: 'Bearer token (static)' },
  { value: 'oauth_client_credentials', label: 'OAuth2 Client Credentials' },
  { value: 'oauth_private_key_jwt', label: 'OAuth Private Key JWT (unsupported V1)' },
  { value: 'cookie_session', label: 'Cookie Session (unsupported V1)' },
  { value: 'interactive', label: 'Interactive Auth (unsupported V1)' }
]

const streamingOptions = ['none', 'sse', 'multipart', 'ndjson']

const defaultSample = `{
  "message": "Hello!",
  "retrieved": [
    {
      "name": "Bob Lee",
      "title": "Product Manager"
    }
  ],
  "provider_used": "gpt-4o-mini"
}`

const fieldHelp = {
  endpoint_url: {
    summary: 'Target API endpoint URL for the provider request.',
    required: 'Yes, unless your raw curl includes the URL.',
    examples: ['https://people-assistant.onrender.com/ask']
  },
  http_method: {
    summary: 'HTTP verb used when calling the endpoint.',
    required: 'Usually POST.',
    examples: ['POST', 'GET']
  },
  auth_type: {
    summary: 'Authentication pattern used by the endpoint.',
    required: 'Choose the endpoint auth style.',
    examples: ['None', 'API Key (header)', 'Bearer token (static)']
  },
  streaming_type: {
    summary: 'Streaming mode used by the endpoint.',
    required: 'Set to none for direct V1 connections.',
    examples: ['none', 'sse', 'ndjson', 'multipart']
  },
  oauth_token_url: {
    summary: 'Token endpoint for OAuth2 client credentials.',
    required: 'Required for oauth_client_credentials auth.',
    examples: ['https://auth.example.com/oauth2/token']
  },
  oauth_scope: {
    summary: 'Scope sent in the token request body.',
    required: 'Optional unless your auth server requires it.',
    examples: ['read write', 'api:invoke']
  },
  oauth_client_id: {
    summary: 'OAuth client ID placeholder/reference.',
    required: 'Needed for oauth_client_credentials flow.',
    examples: ['{{ client_id }}']
  },
  oauth_client_secret: {
    summary: 'OAuth client secret placeholder/reference.',
    required: 'Needed for oauth_client_credentials flow.',
    examples: ['{{ client_secret }}']
  },
  headers: {
    summary: 'Additional request headers sent to the endpoint.',
    required: 'Optional, except auth headers when required.',
    examples: ['Authorization: Bearer <token>', 'X-App-Id: demo']
  },
  prompt_location: {
    summary: 'Path in request JSON where runtime prompt should be injected.',
    required: 'Recommended for prompt templating.',
    examples: ['question', 'messages[0].content', 'messages[0].mm_content[0].value']
  },
  response_content_path: {
    summary: 'Path in response JSON that maps to outputs.content.',
    required: 'Optional; leave blank for auto-detection.',
    examples: ['message', 'retrieved[0].name', 'data.output.final_response']
  },
  request_body: {
    summary: 'Request payload template used by generated YAML.',
    required: 'Usually yes for POST endpoints.',
    examples: ['{"question":"Who are the people?","top_k":"all"}']
  },
  sample_success_response: {
    summary: 'Real JSON success response used for path validation.',
    required: 'Strongly recommended.',
    examples: ['{"message":"Hello","retrieved":[...]}']
  },
  sample_error_response: {
    summary: 'Optional error payload sample for troubleshooting.',
    required: 'Optional.',
    examples: ['{"error":"Unauthorized"}']
  },
  raw_curl: {
    summary: 'Paste a curl command to auto-infer URL, method, headers, and body.',
    required: 'Optional.',
    examples: ['curl -X POST https://api.example.com ...']
  },
  provider_base_url: {
    summary: 'CalypsoAI backend base URL for provider creation API.',
    required: 'Yes for Create Provider.',
    examples: ['https://us1.calypsoai.app/backend/v1']
  },
  provider_name: {
    summary: 'Name for the new provider resource.',
    required: 'Yes.',
    examples: ['people-assistant-provider']
  },
  provider_id: {
    summary: 'Existing provider ID used for delete operations.',
    required: 'Required only when deleting a provider.',
    examples: ['019ddd8c-aa8e-70c6-b73d-2b6d5303393f']
  },
  provider_api_token: {
    summary: 'Platform API token used to call POST /providers.',
    required: 'Yes.',
    examples: ['<F5_API_TOKEN>']
  },
  provider_inputs_json: {
    summary: 'Object/mapping for provider template variables (inputs). Accepts JSON or YAML.',
    required: 'Use {} (JSON) or leave blank if no template variables are needed.',
    examples: ['{}', '{"apiKey":"YOUR_ENDPOINT_BEARER_TOKEN"}', 'apiKey: >-\\n  YOUR_ENDPOINT_BEARER_TOKEN']
  },
  prompt_test_provider: {
    summary: 'Provider reference for Prompt API test. If blank, app uses Provider ID first, then Provider name.',
    required: 'Required unless Provider ID or Provider name is already filled.',
    examples: ['019ddd8c-aa8e-70c6-b73d-2b6d5303393f', 'people-assistant-provider']
  },
  prompt_test_input: {
    summary: 'Prompt text sent to Prompt API for a fast runtime sanity check.',
    required: 'Yes for prompt test.',
    examples: ['Who are the people in the company?']
  },
  prompt_test_verbose: {
    summary: 'When enabled, Prompt API may return additional result details.',
    required: 'Optional.',
    examples: ['true', 'false']
  },
  profile_name_v2: {
    summary: 'Stable profile key used by proxy profile loaders (for example PROFILES_JSON).',
    required: 'Recommended.',
    examples: ['people_assistant_v2']
  },
  profile_step_name_v2: {
    summary: 'Primary step name for the generated proxy workflow.',
    required: 'Optional.',
    examples: ['target', 'ask']
  },
  profile_parser_override_v2: {
    summary: 'Optional parser override. Leave blank for deterministic parser mapping.',
    required: 'Optional.',
    examples: ['json', 'sse', 'ndjson', 'multipart', 'text', 'raw']
  },
  profile_text_paths_v2: {
    summary: 'Optional extraction paths (comma/newline separated). Used by proxy to normalize content.',
    required: 'Optional.',
    examples: ['message', 'choices.*.message.content', 'result.response']
  }
}

function headersToMap(rows) {
  return rows.reduce((acc, row) => {
    const key = row.key.trim()
    if (key) acc[key] = row.value
    return acc
  }, {})
}

function HelpTip({ helpId, openHelpId, setOpenHelpId }) {
  const help = fieldHelp[helpId]
  if (!help) return null

  const isOpen = openHelpId === helpId

  return (
    <span className="help-tip-wrap">
      <button
        type="button"
        className="help-tip-button"
        aria-label={`Help for ${helpId}`}
        onClick={(e) => {
          e.preventDefault()
          setOpenHelpId(isOpen ? null : helpId)
        }}
      >
        i
      </button>
      {isOpen && (
        <div className="help-tip-popover">
          <p>{help.summary}</p>
          <p><strong>Required:</strong> {help.required}</p>
          <p><strong>Examples:</strong> {help.examples.join(' | ')}</p>
        </div>
      )}
    </span>
  )
}

function App() {
  const [form, setForm] = useState({
    endpoint_url: '',
    http_method: 'POST',
    auth_type: 'none',
    request_body: '',
    prompt_location: '',
    response_content_path: '',
    sample_success_response: defaultSample,
    sample_error_response: '',
    streaming_type: 'none',
    raw_curl: '',
    requires_response_aggregation: false,
    oauth_token_url: '',
    oauth_client_id: '',
    oauth_client_secret: '',
    oauth_scope: ''
  })

  const [providerForm, setProviderForm] = useState({
    base_url: 'https://us1.calypsoai.app/backend/v1',
    provider_name: 'connection-assistant-provider',
    provider_id: '',
    api_token: '',
    inputs_json: '{}',
    run_test: true
  })
  const [profileForm, setProfileForm] = useState({
    profile_name: 'generated_profile_v2',
    step_name: 'target',
    result_step: 'target',
    parser_override: '',
    default_text_paths: '',
    include_metadata: true
  })
  const [promptTestForm, setPromptTestForm] = useState({
    provider: '',
    prompt: 'Who are the people in the company?',
    verbose: true
  })

  const [headersRows, setHeadersRows] = useState(defaultHeaders)
  const [result, setResult] = useState(null)
  const [loadingAction, setLoadingAction] = useState('')
  const [copyStatus, setCopyStatus] = useState('')
  const [error, setError] = useState('')
  const [yamlValidation, setYamlValidation] = useState(null)
  const [validatingYaml, setValidatingYaml] = useState(false)
  const [providerResult, setProviderResult] = useState(null)
  const [creatingProvider, setCreatingProvider] = useState(false)
  const [deleteResult, setDeleteResult] = useState(null)
  const [deletingProvider, setDeletingProvider] = useState(false)
  const [promptTestResult, setPromptTestResult] = useState(null)
  const [testingPrompt, setTestingPrompt] = useState(false)
  const [profileResult, setProfileResult] = useState(null)
  const [profileValidation, setProfileValidation] = useState(null)
  const [generatingProfile, setGeneratingProfile] = useState(false)
  const [validatingProfile, setValidatingProfile] = useState(false)
  const [activeTab, setActiveTab] = useState('v1')
  const [openHelpId, setOpenHelpId] = useState(null)

  const builtPayload = useMemo(() => {
    const payload = {
      endpoint_url: form.endpoint_url.trim() || null,
      http_method: form.http_method.trim() || 'POST',
      auth_type: form.auth_type,
      headers: headersToMap(headersRows),
      request_body: form.request_body || null,
      prompt_location: form.prompt_location || null,
      response_content_path: form.response_content_path || null,
      sample_success_response: form.sample_success_response || null,
      sample_error_response: form.sample_error_response || null,
      streaming_type: form.streaming_type,
      raw_curl: form.raw_curl || null,
      requires_response_aggregation: Boolean(form.requires_response_aggregation)
    }

    if (form.auth_type === 'oauth_client_credentials') {
      payload.oauth = {
        token_url: form.oauth_token_url || null,
        client_id: form.oauth_client_id || null,
        client_secret: form.oauth_client_secret || null,
        scope: form.oauth_scope || null
      }
    }

    return payload
  }, [form, headersRows])

  const setField = (name, value) => {
    setForm((prev) => ({ ...prev, [name]: value }))
  }

  const setProviderField = (name, value) => {
    setProviderForm((prev) => ({ ...prev, [name]: value }))
  }

  const setProfileField = (name, value) => {
    setProfileForm((prev) => ({ ...prev, [name]: value }))
  }

  const setPromptTestField = (name, value) => {
    setPromptTestForm((prev) => ({ ...prev, [name]: value }))
  }

  const setHeader = (index, field, value) => {
    setHeadersRows((prev) => prev.map((row, i) => (i === index ? { ...row, [field]: value } : row)))
  }

  const addHeaderRow = () => setHeadersRows((prev) => [...prev, { key: '', value: '' }])
  const removeHeaderRow = (index) => {
    setHeadersRows((prev) => (prev.length === 1 ? prev : prev.filter((_, i) => i !== index)))
  }

  async function validateYaml(yamlText) {
    if (!yamlText) {
      setYamlValidation(null)
      return null
    }

    setValidatingYaml(true)
    try {
      const res = await fetch('/validate-yaml', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ yaml: yamlText })
      })

      if (!res.ok) {
        const body = await res.text()
        throw new Error(body || `Validation failed with status ${res.status}`)
      }

      const data = await res.json()
      setYamlValidation(data)
      return data
    } catch (e) {
      setYamlValidation({ valid: false, errors: [e.message || 'YAML validation failed'], warnings: [] })
      return null
    } finally {
      setValidatingYaml(false)
    }
  }

  async function validateProfileYaml(profileYamlText) {
    if (!profileYamlText) {
      setProfileValidation(null)
      return null
    }

    setValidatingProfile(true)
    try {
      const res = await fetch('/validate-profile-yaml', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile_yaml: profileYamlText })
      })

      if (!res.ok) {
        const body = await res.text()
        throw new Error(body || `Profile validation failed with status ${res.status}`)
      }

      const data = await res.json()
      setProfileValidation(data)
      return data
    } catch (e) {
      setProfileValidation({ valid: false, errors: [e.message || 'Profile validation failed'], warnings: [] })
      return null
    } finally {
      setValidatingProfile(false)
    }
  }

  async function runAction(action) {
    setLoadingAction(action)
    setError('')
    setCopyStatus('')
    if (action !== 'generate') {
      setYamlValidation(null)
      setProviderResult(null)
    }

    try {
      const endpoint = action === 'analyze' ? '/analyze' : '/generate-yaml'
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(builtPayload)
      })

      if (!res.ok) {
        const body = await res.text()
        throw new Error(body || `Request failed with status ${res.status}`)
      }

      const data = await res.json()
      setResult({ type: action, ...data })

      if (action === 'generate') {
        setProviderResult(null)
        setDeleteResult(null)
        setPromptTestResult(null)
        await validateYaml(data.yaml || '')
      }
    } catch (e) {
      setError(e.message || 'Unexpected error')
    } finally {
      setLoadingAction('')
    }
  }

  async function generateProfile() {
    setGeneratingProfile(true)
    setProfileResult(null)
    setProfileValidation(null)
    setError('')

    const textPaths = profileForm.default_text_paths
      .split(/\n|,/)
      .map((item) => item.trim())
      .filter(Boolean)

    try {
      const res = await fetch('/generate-profile-yaml', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          endpoint: builtPayload,
          profile_name: profileForm.profile_name,
          step_name: profileForm.step_name,
          result_step: profileForm.result_step,
          default_text_paths: textPaths,
          parser_override: profileForm.parser_override || null,
          include_metadata: Boolean(profileForm.include_metadata)
        })
      })

      if (!res.ok) {
        const body = await res.text()
        throw new Error(body || `Generate profile failed with status ${res.status}`)
      }

      const data = await res.json()
      setProfileResult(data)
      await validateProfileYaml(data.profile_yaml || '')
    } catch (e) {
      setProfileResult({
        decision: 'Proxy Required',
        reasons: [e.message || 'Generate profile failed'],
        warnings: [e.message || 'Generate profile failed'],
        profile_name: null,
        profile_yaml: '',
        profiles_json_fragment: {}
      })
    } finally {
      setGeneratingProfile(false)
    }
  }

  async function createProvider() {
    const yamlText = result?.yaml
    if (!yamlText) return

    setCreatingProvider(true)
    setProviderResult(null)
    setDeleteResult(null)
    setPromptTestResult(null)
    setError('')

    try {
      const res = await fetch('/create-provider', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          yaml: yamlText,
          provider_name: providerForm.provider_name,
          api_token: providerForm.api_token,
          base_url: providerForm.base_url,
          inputs_json: providerForm.inputs_json,
          run_test: providerForm.run_test
        })
      })

      if (!res.ok) {
        const body = await res.text()
        throw new Error(body || `Create provider failed with status ${res.status}`)
      }

      const data = await res.json()
      setProviderResult(data)
      if (data?.provider_id) {
        setProviderForm((prev) => ({ ...prev, provider_id: data.provider_id }))
        setPromptTestForm((prev) => ({
          ...prev,
          provider: prev.provider.trim() ? prev.provider : data.provider_id
        }))
      }
    } catch (e) {
      setProviderResult({
        success: false,
        message: e.message || 'Create provider failed',
        errors: [e.message || 'Unexpected error'],
        status_code: null
      })
    } finally {
      setCreatingProvider(false)
    }
  }

  async function deleteProvider() {
    if (!providerForm.provider_id.trim()) {
      setDeleteResult({
        success: false,
        message: 'Provider ID is required for delete.',
        errors: ['Provider ID is required for delete.'],
        status_code: null
      })
      return
    }

    setDeletingProvider(true)
    setDeleteResult(null)
    setPromptTestResult(null)
    setError('')

    try {
      const res = await fetch('/delete-provider', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider_id: providerForm.provider_id,
          api_token: providerForm.api_token,
          base_url: providerForm.base_url
        })
      })

      if (!res.ok) {
        const body = await res.text()
        throw new Error(body || `Delete provider failed with status ${res.status}`)
      }

      const data = await res.json()
      setDeleteResult(data)
    } catch (e) {
      setDeleteResult({
        success: false,
        message: e.message || 'Delete provider failed',
        errors: [e.message || 'Unexpected error'],
        status_code: null
      })
    } finally {
      setDeletingProvider(false)
    }
  }

  async function testProviderPrompt() {
    const providerRef =
      promptTestForm.provider.trim() ||
      providerForm.provider_id.trim() ||
      providerForm.provider_name.trim()

    if (!promptTestForm.prompt.trim()) {
      setPromptTestResult({
        success: false,
        message: 'Prompt text is required.',
        errors: ['Prompt text is required.'],
        status_code: null
      })
      return
    }

    if (!providerRef) {
      setPromptTestResult({
        success: false,
        message: 'Provider reference is required. Fill Provider ID, Provider name, or Prompt test provider.',
        errors: ['Provider reference is required.'],
        status_code: null
      })
      return
    }

    setTestingPrompt(true)
    setPromptTestResult(null)
    setError('')

    try {
      const res = await fetch('/test-provider-prompt', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          api_token: providerForm.api_token,
          base_url: providerForm.base_url,
          prompt: promptTestForm.prompt,
          provider: providerRef,
          verbose: Boolean(promptTestForm.verbose)
        })
      })

      if (!res.ok) {
        const body = await res.text()
        throw new Error(body || `Prompt test failed with status ${res.status}`)
      }

      const data = await res.json()
      setPromptTestResult(data)
    } catch (e) {
      setPromptTestResult({
        success: false,
        message: e.message || 'Prompt test failed',
        errors: [e.message || 'Unexpected error'],
        status_code: null
      })
    } finally {
      setTestingPrompt(false)
    }
  }

  async function copyOutput() {
    const text = activeTab === 'v2'
      ? (profileResult?.profile_yaml || '')
      : (result?.yaml || result?.proxy_placeholder || '')
    if (!text) return

    try {
      await navigator.clipboard.writeText(text)
      setCopyStatus('Copied')
      setTimeout(() => setCopyStatus(''), 1500)
    } catch {
      setCopyStatus('Copy failed')
    }
  }

  const hasCopyableOutput = activeTab === 'v2'
    ? Boolean(profileResult?.profile_yaml)
    : Boolean(result?.yaml || result?.proxy_placeholder)

  return (
    <main className="app-shell">
      <h1>Connection Assistant</h1>
      <p className="subtitle">
        {activeTab === 'v1'
          ? 'V1: deterministic compatibility checks and Connections YAML generation.'
          : 'V2: deterministic proxy profile generation for profile-based routing.'}
      </p>
      <div className="tab-strip" role="tablist" aria-label="Connection Assistant version tabs">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'v1'}
          className={`tab-button ${activeTab === 'v1' ? 'tab-button-active' : ''}`}
          onClick={() => setActiveTab('v1')}
        >
          V1: Connections YAML
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'v2'}
          className={`tab-button ${activeTab === 'v2' ? 'tab-button-active' : ''}`}
          onClick={() => setActiveTab('v2')}
        >
          V2: Profiles
        </button>
      </div>

      <div className="layout-grid">
        <section className="panel input-panel">
        <h2>{activeTab === 'v1' ? 'Input (V1)' : 'Input (V2)'}</h2>
        <div className="grid two-col">
          <label>
            <span className="label-title-row">
              <span>Endpoint URL</span>
              <HelpTip helpId="endpoint_url" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
            </span>
            <input value={form.endpoint_url} onChange={(e) => setField('endpoint_url', e.target.value)} placeholder="https://api.example.com/v1/chat/completions" />
          </label>
          <label>
            <span className="label-title-row">
              <span>HTTP method</span>
              <HelpTip helpId="http_method" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
            </span>
            <input value={form.http_method} onChange={(e) => setField('http_method', e.target.value.toUpperCase())} />
          </label>
        </div>

        <div className="grid two-col">
          <label>
            <span className="label-title-row">
              <span>Auth type</span>
              <HelpTip helpId="auth_type" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
            </span>
            <select value={form.auth_type} onChange={(e) => setField('auth_type', e.target.value)}>
              {authOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </label>

          <label>
            <span className="label-title-row">
              <span>Streaming type</span>
              <HelpTip helpId="streaming_type" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
            </span>
            <select value={form.streaming_type} onChange={(e) => setField('streaming_type', e.target.value)}>
              {streamingOptions.map((opt) => (
                <option key={opt} value={opt}>{opt}</option>
              ))}
            </select>
          </label>
        </div>

        {form.auth_type === 'oauth_client_credentials' && (
          <div className="grid two-col">
            <label>
              <span className="label-title-row">
                <span>OAuth token endpoint</span>
                <HelpTip helpId="oauth_token_url" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
              </span>
              <input value={form.oauth_token_url} onChange={(e) => setField('oauth_token_url', e.target.value)} placeholder="https://auth.example.com/oauth2/token" />
            </label>
            <label>
              <span className="label-title-row">
                <span>OAuth scope</span>
                <HelpTip helpId="oauth_scope" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
              </span>
              <input value={form.oauth_scope} onChange={(e) => setField('oauth_scope', e.target.value)} placeholder="read write" />
            </label>
            <label>
              <span className="label-title-row">
                <span>OAuth client_id</span>
                <HelpTip helpId="oauth_client_id" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
              </span>
              <input value={form.oauth_client_id} onChange={(e) => setField('oauth_client_id', e.target.value)} placeholder="{{ client_id }}" />
            </label>
            <label>
              <span className="label-title-row">
                <span>OAuth client_secret</span>
                <HelpTip helpId="oauth_client_secret" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
              </span>
              <input value={form.oauth_client_secret} onChange={(e) => setField('oauth_client_secret', e.target.value)} placeholder="{{ client_secret }}" type="password" />
            </label>
          </div>
        )}

        <div className="headers-block">
          <div className="section-row">
            <h3>
              Headers <HelpTip helpId="headers" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
            </h3>
            <button type="button" onClick={addHeaderRow}>Add Header</button>
          </div>
          {headersRows.map((row, idx) => (
            <div key={`header-${idx}`} className="header-row">
              <input value={row.key} onChange={(e) => setHeader(idx, 'key', e.target.value)} placeholder="Header name" />
              <input value={row.value} onChange={(e) => setHeader(idx, 'value', e.target.value)} placeholder="Header value" />
              <button type="button" onClick={() => removeHeaderRow(idx)}>Remove</button>
            </div>
          ))}
        </div>

        <div className="grid two-col">
          <label>
            <span className="label-title-row">
              <span>Prompt location</span>
              <HelpTip helpId="prompt_location" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
            </span>
            <input value={form.prompt_location} onChange={(e) => setField('prompt_location', e.target.value)} placeholder="messages[0].content" />
          </label>
          {activeTab === 'v1' && (
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={form.requires_response_aggregation}
                onChange={(e) => setField('requires_response_aggregation', e.target.checked)}
              />
              Response must be aggregated (stream chunks)
            </label>
          )}
        </div>

        {activeTab === 'v1' && (
          <label>
            <span className="label-title-row">
              <span>Response content path (optional override)</span>
              <HelpTip helpId="response_content_path" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
            </span>
            <input
              value={form.response_content_path}
              onChange={(e) => setField('response_content_path', e.target.value)}
              placeholder="message or retrieved[0].name"
            />
          </label>
        )}

        <label>
          <span className="label-title-row">
            <span>Request body (JSON)</span>
            <HelpTip helpId="request_body" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
          </span>
          <textarea rows={8} value={form.request_body} onChange={(e) => setField('request_body', e.target.value)} />
        </label>

        <label>
          <span className="label-title-row">
            <span>Sample success response (JSON)</span>
            <HelpTip helpId="sample_success_response" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
          </span>
          <textarea rows={8} value={form.sample_success_response} onChange={(e) => setField('sample_success_response', e.target.value)} />
        </label>

        {activeTab === 'v1' && (
          <label>
            <span className="label-title-row">
              <span>Sample error response (optional)</span>
              <HelpTip helpId="sample_error_response" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
            </span>
            <textarea rows={5} value={form.sample_error_response} onChange={(e) => setField('sample_error_response', e.target.value)} />
          </label>
        )}

        <label>
          <span className="label-title-row">
            <span>Raw curl input (optional)</span>
            <HelpTip helpId="raw_curl" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
          </span>
          <textarea rows={5} value={form.raw_curl} onChange={(e) => setField('raw_curl', e.target.value)} placeholder="curl -X POST https://api.example.com ..." />
        </label>

        {activeTab === 'v2' && (
          <section className="profile-v2-config">
            <h3>Profile Generation (V2)</h3>
            <p className="muted-text">Generate proxy profile YAML as a separate creation flow.</p>
            <div className="grid two-col">
              <label>
                <span className="label-title-row">
                  <span>Profile name</span>
                  <HelpTip helpId="profile_name_v2" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
                </span>
                <input
                  value={profileForm.profile_name}
                  onChange={(e) => setProfileField('profile_name', e.target.value)}
                  placeholder="generated_profile_v2"
                />
              </label>
              <label>
                <span className="label-title-row">
                  <span>Step name</span>
                  <HelpTip helpId="profile_step_name_v2" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
                </span>
                <input
                  value={profileForm.step_name}
                  onChange={(e) => setProfileField('step_name', e.target.value)}
                  placeholder="target"
                />
              </label>
              <label>
                <span className="label-title-row">
                  <span>Parser override (optional)</span>
                  <HelpTip helpId="profile_parser_override_v2" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
                </span>
                <input
                  value={profileForm.parser_override}
                  onChange={(e) => setProfileField('parser_override', e.target.value)}
                  placeholder="json"
                />
              </label>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={profileForm.include_metadata}
                  onChange={(e) => setProfileField('include_metadata', e.target.checked)}
                />
                Include metadata
              </label>
            </div>
            <label>
              <span className="label-title-row">
                <span>Default text paths (optional)</span>
                <HelpTip helpId="profile_text_paths_v2" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
              </span>
              <textarea
                rows={3}
                value={profileForm.default_text_paths}
                onChange={(e) => setProfileField('default_text_paths', e.target.value)}
                placeholder="message,content,text"
              />
            </label>
          </section>
        )}

        <div className="actions">
          {activeTab === 'v1' && (
            <>
              <button type="button" onClick={() => runAction('analyze')} disabled={loadingAction !== ''}>
                {loadingAction === 'analyze' ? 'Analyzing...' : 'Analyze'}
              </button>
              <button type="button" onClick={() => runAction('generate')} disabled={loadingAction !== ''}>
                {loadingAction === 'generate' ? 'Generating...' : 'Generate YAML'}
              </button>
            </>
          )}
          {activeTab === 'v2' && (
            <button type="button" onClick={generateProfile} disabled={generatingProfile || loadingAction !== ''}>
              {generatingProfile ? 'Generating Profile...' : 'Generate Profile (V2)'}
            </button>
          )}
        </div>

        {error && <p className="error">{error}</p>}
        </section>

        <section className="panel output-panel">
        <div className="section-row">
          <h2>{activeTab === 'v1' ? 'Output (V1)' : 'Output (V2)'}</h2>
          <button
            type="button"
            onClick={copyOutput}
            disabled={!hasCopyableOutput}
          >
            Copy
          </button>
        </div>

        {copyStatus && <p className="copy-status">{copyStatus}</p>}

        {activeTab === 'v1' && !result && <p>No output yet.</p>}
        {activeTab === 'v2' && !profileResult && <p>No output yet.</p>}

        {activeTab === 'v1' && result && (
          <>
            <div className="decision-pill">{result.decision}</div>

            <h3>Reasons</h3>
            <ul>
              {(result.reasons || []).map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>

            <h3>Response Extraction</h3>
            <p>
              Path: {result.detected_response_path || 'fallback'}
            </p>
            <p>
              Expression: {result.response_path_expression || 'String.decode(response.body)'}
            </p>
            <p>
              Confidence: {result.response_path_confident ? 'high' : 'low'}
            </p>

            <h3>Warnings</h3>
            {result.warnings && result.warnings.length > 0 ? (
              <ul>
                {result.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            ) : (
              <p>None</p>
            )}

            {result.yaml && (
              <>
                <h3>Generated YAML</h3>
                <pre>{result.yaml}</pre>

                <section className="validation-panel">
                  <div className="section-row">
                    <h3>YAML Validation</h3>
                    <button type="button" onClick={() => validateYaml(result.yaml)} disabled={validatingYaml}>
                      {validatingYaml ? 'Validating...' : 'Re-validate YAML'}
                    </button>
                  </div>

                  {!yamlValidation && <p className="muted-text">Validation not run yet.</p>}

                  {yamlValidation && (
                    <>
                      <p className={yamlValidation.valid ? 'ok-text' : 'error'}>
                        {yamlValidation.valid ? 'YAML looks valid.' : 'YAML has validation errors.'}
                      </p>
                      {yamlValidation.workflow_type && <p>Workflow type: {yamlValidation.workflow_type}</p>}
                      {yamlValidation.stage_count !== null && <p>Stage count: {yamlValidation.stage_count}</p>}

                      {yamlValidation.errors?.length > 0 && (
                        <>
                          <h4>Errors</h4>
                          <ul>
                            {yamlValidation.errors.map((item) => (
                              <li key={item}>{item}</li>
                            ))}
                          </ul>
                        </>
                      )}

                      {yamlValidation.warnings?.length > 0 && (
                        <>
                          <h4>Warnings</h4>
                          <ul>
                            {yamlValidation.warnings.map((item) => (
                              <li key={item}>{item}</li>
                            ))}
                          </ul>
                        </>
                      )}
                    </>
                  )}
                </section>

                <section className="provider-panel">
                  <h3>Create Provider (API Test)</h3>
                  <p className="muted-text">Calls your platform provider API with generated YAML and reports status.</p>

                  <div className="grid two-col">
                    <label>
                      <span className="label-title-row">
                        <span>API Base URL</span>
                        <HelpTip helpId="provider_base_url" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
                      </span>
                      <input value={providerForm.base_url} onChange={(e) => setProviderField('base_url', e.target.value)} />
                    </label>
                    <label>
                      <span className="label-title-row">
                        <span>Provider name</span>
                        <HelpTip helpId="provider_name" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
                      </span>
                      <input value={providerForm.provider_name} onChange={(e) => setProviderField('provider_name', e.target.value)} />
                    </label>
                    <label>
                      <span className="label-title-row">
                        <span>Provider ID (for delete)</span>
                        <HelpTip helpId="provider_id" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
                      </span>
                      <input value={providerForm.provider_id} onChange={(e) => setProviderField('provider_id', e.target.value)} placeholder="019ddd8c-aa8e-70c6-b73d-2b6d5303393f" />
                    </label>
                    <label>
                      <span className="label-title-row">
                        <span>API token</span>
                        <HelpTip helpId="provider_api_token" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
                      </span>
                      <input
                        type="password"
                        value={providerForm.api_token}
                        onChange={(e) => setProviderField('api_token', e.target.value)}
                        placeholder="F5 API token"
                      />
                    </label>
                    <label className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={providerForm.run_test}
                        onChange={(e) => setProviderField('run_test', e.target.checked)}
                      />
                      Run provider test on create
                    </label>
                  </div>

                  <label>
                    <span className="label-title-row">
                      <span>Provider inputs (JSON or YAML)</span>
                      <HelpTip helpId="provider_inputs_json" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
                    </span>
                    <textarea
                      rows={5}
                      value={providerForm.inputs_json}
                      onChange={(e) => setProviderField('inputs_json', e.target.value)}
                    />
                  </label>

                  <div className="actions">
                    <button type="button" onClick={createProvider} disabled={creatingProvider}>
                      {creatingProvider ? 'Creating...' : 'Create Provider'}
                    </button>
                    <button type="button" className="button-danger" onClick={deleteProvider} disabled={deletingProvider}>
                      {deletingProvider ? 'Deleting...' : 'Delete Provider'}
                    </button>
                  </div>

                  {providerResult && (
                    <div className="provider-result">
                      <p className={providerResult.success ? 'ok-text' : 'error'}>{providerResult.message}</p>
                      {providerResult.status_code !== null && <p>Status: {providerResult.status_code}</p>}
                      {providerResult.provider_id && <p>Provider ID: {providerResult.provider_id}</p>}

                      {providerResult.errors?.length > 0 && (
                        <ul>
                          {providerResult.errors.map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ul>
                      )}

                      {providerResult.response_body && (
                        <pre>{JSON.stringify(providerResult.response_body, null, 2)}</pre>
                      )}
                    </div>
                  )}

                  {deleteResult && (
                    <div className="provider-result">
                      <p className={deleteResult.success ? 'ok-text' : 'error'}>{deleteResult.message}</p>
                      {deleteResult.status_code !== null && <p>Status: {deleteResult.status_code}</p>}
                      {deleteResult.provider_id && <p>Provider ID: {deleteResult.provider_id}</p>}

                      {deleteResult.errors?.length > 0 && (
                        <ul>
                          {deleteResult.errors.map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ul>
                      )}

                      {deleteResult.response_body && (
                        <pre>{JSON.stringify(deleteResult.response_body, null, 2)}</pre>
                      )}
                    </div>
                  )}

                  <section className="prompt-test-panel">
                    <h4>Quick Prompt Test</h4>
                    <p className="muted-text">Sends a prompt to Prompt API using your provider reference.</p>

                    <div className="grid two-col">
                      <label>
                        <span className="label-title-row">
                          <span>Provider ref (optional override)</span>
                          <HelpTip helpId="prompt_test_provider" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
                        </span>
                        <input
                          value={promptTestForm.provider}
                          onChange={(e) => setPromptTestField('provider', e.target.value)}
                          placeholder="Defaults to Provider ID, then Provider name"
                        />
                      </label>

                      <label className="checkbox-row">
                        <input
                          type="checkbox"
                          checked={promptTestForm.verbose}
                          onChange={(e) => setPromptTestField('verbose', e.target.checked)}
                        />
                        Verbose result
                        <HelpTip helpId="prompt_test_verbose" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
                      </label>
                    </div>

                    <label>
                      <span className="label-title-row">
                        <span>Prompt</span>
                        <HelpTip helpId="prompt_test_input" openHelpId={openHelpId} setOpenHelpId={setOpenHelpId} />
                      </span>
                      <textarea
                        rows={3}
                        value={promptTestForm.prompt}
                        onChange={(e) => setPromptTestField('prompt', e.target.value)}
                      />
                    </label>

                    <div className="actions">
                      <button type="button" onClick={testProviderPrompt} disabled={testingPrompt}>
                        {testingPrompt ? 'Testing...' : 'Send Prompt Test'}
                      </button>
                    </div>

                    {promptTestResult && (
                      <div className="provider-result">
                        <p className={promptTestResult.success ? 'ok-text' : 'error'}>{promptTestResult.message}</p>
                        {promptTestResult.status_code !== null && <p>Status: {promptTestResult.status_code}</p>}
                        {promptTestResult.provider && <p>Provider: {promptTestResult.provider}</p>}
                        {promptTestResult.outcome && <p>Outcome: {promptTestResult.outcome}</p>}

                        {promptTestResult.prompt_response && (
                          <>
                            <h4>Prompt Response</h4>
                            <pre>{typeof promptTestResult.prompt_response === 'string' ? promptTestResult.prompt_response : JSON.stringify(promptTestResult.prompt_response, null, 2)}</pre>
                          </>
                        )}

                        {promptTestResult.errors?.length > 0 && (
                          <ul>
                            {promptTestResult.errors.map((item) => (
                              <li key={item}>{item}</li>
                            ))}
                          </ul>
                        )}

                        {promptTestResult.response_body && (
                          <pre>{JSON.stringify(promptTestResult.response_body, null, 2)}</pre>
                        )}
                      </div>
                    )}
                  </section>
                </section>
              </>
            )}

            {!result.yaml && result.proxy_placeholder && (
              <>
                <h3>Generated YAML</h3>
                <p>{result.proxy_placeholder}</p>
              </>
            )}
          </>
        )}

        {activeTab === 'v2' && profileResult && (
          <section className="profile-v2-output">
            <h3>Generated Profile (V2)</h3>
            {profileResult.profile_name && <p>Profile name: {profileResult.profile_name}</p>}
            {profileResult.decision && <p>Source decision: {profileResult.decision}</p>}

            <h4>Reasons</h4>
            {profileResult.reasons?.length > 0 ? (
              <ul>
                {profileResult.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            ) : (
              <p>None</p>
            )}

            <h4>Warnings</h4>
            {profileResult.warnings?.length > 0 ? (
              <ul>
                {profileResult.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            ) : (
              <p>None</p>
            )}

            {profileResult.profile_yaml && (
              <>
                <h4>Profile YAML</h4>
                <pre>{profileResult.profile_yaml}</pre>
              </>
            )}

            {profileResult.profiles_json_fragment && (
              <>
                <h4>PROFILES_JSON Fragment</h4>
                <pre>{JSON.stringify(profileResult.profiles_json_fragment, null, 2)}</pre>
              </>
            )}

            <section className="validation-panel">
              <div className="section-row">
                <h4>Profile Validation</h4>
                <button
                  type="button"
                  onClick={() => validateProfileYaml(profileResult.profile_yaml || '')}
                  disabled={validatingProfile}
                >
                  {validatingProfile ? 'Validating...' : 'Re-validate Profile'}
                </button>
              </div>

              {!profileValidation && <p className="muted-text">Validation not run yet.</p>}

              {profileValidation && (
                <>
                  <p className={profileValidation.valid ? 'ok-text' : 'error'}>
                    {profileValidation.valid ? 'Profile looks valid.' : 'Profile has validation errors.'}
                  </p>
                  {profileValidation.profile_name && <p>Validated profile: {profileValidation.profile_name}</p>}
                  {profileValidation.step_count !== null && <p>Step count: {profileValidation.step_count}</p>}
                  {profileValidation.parser && <p>Parser: {profileValidation.parser}</p>}

                  {profileValidation.errors?.length > 0 && (
                    <>
                      <h4>Errors</h4>
                      <ul>
                        {profileValidation.errors.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </>
                  )}

                  {profileValidation.warnings?.length > 0 && (
                    <>
                      <h4>Warnings</h4>
                      <ul>
                        {profileValidation.warnings.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </>
                  )}
                </>
              )}
            </section>
          </section>
        )}
        </section>
      </div>
    </main>
  )
}

export default App
