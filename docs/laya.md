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

## Deployed needle-router contract
After investigating the `needle-router` Space source and `/v1/info` endpoint, we have established the following HTTP contract for `Laya`:
- **Endpoint**: `/v1/system-one`
- **Method**: `POST`
- **Authentication**: No authentication required for inference (API key parameter is optional).
- **Request Schema**:
```json
{
  "state": { "...": "..." },
  "questions": {
    "question_key": {
      "type": "noul",
      "instructions": "Question text here"
    }
  }
}
```
- **Response Schema**:
```json
{
  "results": {
    "question_key": {
      "noul": 0.85
    }
  }
}
```
- **Batching semantics**: Foreman natively batches multiple `noul` checks into a single request. `Laya` successfully evaluates these independent checks and returns probabilities for each.
- **Error behavior**: Returns `400` for validation issues, `429` for queue full, `503` for service not ready, and standard `500` for internal errors. `HttpLayaTransport` maps these correctly to `LayaTransportError`.
- **Response limits**: A `60,000` byte limit is enforced natively while streaming the response payload to prevent OOM / unbounded processing. Duplicate keys are also rejected as malformed output.

## Current status

Jev remains the default and uses the existing `TYPESAFE_API_KEY`. Laya is opt-in:

```bash
FOREMAN_DECISION_PROVIDER=laya \
FOREMAN_LAYA_BASE_URL=https://leon4gr45-needle-router.hf.space \
FOREMAN_WORKER_BACKEND=opencode \
foreman run --repo ./project --job "..."
```

Laya integration is fully implemented using the verified `HttpLayaTransport`. The transport validates requests and batched Noul checks safely against the real HTTP endpoint. `BLOCKED_PENDING_LAYA_CONTRACT` has been removed.

`FOREMAN_LAYA_TIMEOUT` sets a finite positive deadline in seconds (default 10).
`FOREMAN_LAYA_API_KEY` is a supervisor-only credential, supported but currently optional for `needle-router`. It is never placed in observations, logs,
repository files or worker subprocess environments. Configure credentials securely in
cloud environment settings. Laya mode does not require a TypeSafe key.
