# Laya semantic supervision

Foreman owns worker supervision. Its semantic providers estimate the probability of each
responsibility's assertion; Python responsibilities and deterministic `FactoryPolicy`
select permitted actions. Neither Laya nor Jev chooses worker actions.

## Configuration and current status

Jev remains the default and uses the existing `TYPESAFE_API_KEY`. Laya is opt-in:

```bash
FOREMAN_DECISION_PROVIDER=laya \
FOREMAN_LAYA_BASE_URL=https://leon4gr45-needle-router.hf.space \
FOREMAN_WORKER_BACKEND=opencode \
foreman run --repo ./project --job "..."
```

This command currently reports `BLOCKED_PENDING_LAYA_CONTRACT` before launching a worker.
The internal Laya provider and injectable transport are implemented and tested; production
HTTP is deliberately disabled until the deployed service contract can be verified.
A base URL alone does not establish an endpoint or schema.

`FOREMAN_LAYA_TIMEOUT` sets a finite positive deadline in seconds (default 10).
`FOREMAN_LAYA_API_KEY` is a supervisor-only credential, required only when the verified
transport declares authenticated access. It is never placed in observations, logs,
repository files or worker subprocess environments. Configure credentials securely in
cloud environment settings. Laya mode does not require a TypeSafe key.

## Sources inspected

Foreman base: `thruwire/foreman`, commit
`381ca2de90af9efc2781a58055cc3028c7cabbb9`.

Decision semantics reference: [zhangcy122/OpenJev](https://github.com/zhangcy122/OpenJev),
commit `8e8f8a95c40d37c20aa8faf3e82a33a1dde0b76b`, especially
`openjevpro/client.py::decide_noul`, `_decide_choice_laya` and `openjevpro/harness.py::LayaEngine`.
OpenJev treats each assertion as an independent binary TRUE/FALSE judgment and exposes
`probability_true`. Foreman consumes that probability, preserving its own thresholds.
A categorical distribution over all Foreman checks would make independent responsibilities
compete and is unsuitable. OpenJev's calibration/choice examples are references, not a
verified contract for this particular deployment. Foreman does not invent uniform fallback
probabilities or import OpenJev's action/gateway policy.

The OpenJev Laya client posts a single categorical choice to `/decision/choice`, with
`model`, `state`, `candidates` and `criteria`; its implementation does not prove that
`https://leon4gr45-needle-router.hf.space` implements that route, accepts multiple independent
questions or uses the same authentication. It is insufficient evidence to invent a
`/v1/system-one` payload or to replace a batched assessment with one request per check.

`Leon4gr45/needle-router` was inaccessible through GitHub. Attempts to read the deployed
service's `/openapi.json` and its Hugging Face Space source were blocked by the cloud
network proxy (CONNECT 403). Live inference and token validity are unverified.

## Internal transport contract

`DecisionProvider.assess(AssessmentRequest)` receives an already-budgeted observation
state, question key/instruction mapping and assessment/routing purpose. It returns
`AssessmentResult.probabilities`. `ProviderForemanModel` and
`ProviderResponsibilityRouter` bridge this to the existing runtime interfaces.

`LayaTransport.assess(request, settings=...)` returns a `LayaTransportResult` containing
key/probability pairs and the raw response byte count. Pairs preserve duplicates for
validation. This is a Python adapter contract, not an invented service wire schema.
A future adapter must enforce the response limit while streaming, retain duplicate JSON
keys until validation, reject invalid JSON/schema, and report HTTP failures using
`LayaTransportError`. It must not log headers, tokens or response bodies.

Checks sharing an evidence selection stay in one batched provider call. Different
selections retain separate filtered calls, matching Jev's existing behavior. Routing
similarly batches the conditional candidates at each existing routing level. The existing
conservative byte budget applies to every provider, with protected state fields preserved.

The provider rejects missing, extra, duplicate, nonnumeric, nonfinite and out-of-range
probabilities and oversized responses. It translates timeouts, connection failures,
authentication errors, 429 and 5xx into assessment failures. Existing consecutive-failure
limits and escalation remain unchanged; failures never become `worker_stuck=1`.

## Completing live integration

1. Allow `leon4gr45-needle-router.hf.space` and `huggingface.co` in cloud network settings.
2. Read the deployed OpenAPI and/or exact Space source version. Establish endpoint, method,
   payload, independent-question batching, response probability semantics, errors and auth.
3. Implement a bounded asynchronous HTTP adapter for that verified contract. If only
   categorical choices are supported, independently batched binary judgments require a
   service capability change; do not normalize all Foreman checks together.
4. Add fixtures captured from that contract and HTTP adapter tests, including streaming
   byte limits and duplicate JSON keys. The existing injected-transport tests are offline
   internal-contract tests, not evidence that a service payload works.
5. Validate a multi-check assessment and conditional responsibility routing against live
   Laya, then validate an OpenCode worker separately without changing its configuration.

No OpenCode model, provider, configuration, ACP or desktop-session behavior is modified.
