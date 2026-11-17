# Postman Demo Requests

Import `pii-masking-demo.postman_collection.json`. In the collection variables,
set `service_token` to the same value supplied to Compose as `SERVICE_TOKEN`.
Keep `base_url` as `http://localhost:8080` for local Compose.
Set `model_format` to `onnx`, the only accepted value, to select the runtime
per request.

Start the stack with:

```powershell
$env:SERVICE_TOKEN = "<token>"
docker compose up --build
```

Run the readiness requests before the masking requests because each model loads
on first use. The collection includes text masking for Octo and Synthify, PDF
in/PDF out for both models, and an Octo JSON request. Berts can be called by
changing `X-Team-ID` to `berts` and keeping `model_format` at `onnx`. The PDF requests default to
`Nova_samples_filled/Nova_sample_4.pdf`; select that file manually in Postman if
your client does not resolve the relative path. Save the PDF response from
Postman if you want to inspect the masked output locally.
