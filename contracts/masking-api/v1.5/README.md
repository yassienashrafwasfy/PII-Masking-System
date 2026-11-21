# Masking API v1.5

[PII Masking API Contract v1.5](PII_Masking_API_Contract_v1.5.md) is authoritative. The platform-owned gateway implements it while retaining trusted `X-Team-ID` routing to internal team adapters.

The OpenAPI document and examples are machine-readable companions. Contract changes require a new compatible version or a coordinated migration.

v1.5 widens one enum and changes nothing else: the `X-Team-ID` header accepts `mdeberta` besides `octo`, `synthify` and `berts` (ADR 007). A v1.4 caller is a valid v1.5 caller and sees no change at all; the request, the masked bytes and every response header keep their meaning. v1.4 narrowed `X-Model-Format` to `onnx` alone and that still holds.
