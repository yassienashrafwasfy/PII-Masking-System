# Masking API v1.4

[PII Masking API Contract v1.4](PII_Masking_API_Contract_v1.4.md) is authoritative. The platform-owned gateway implements it while retaining trusted `X-Team-ID` routing to internal team adapters.

The OpenAPI document and examples are machine-readable companions. Contract changes require a new compatible version or a coordinated migration.

v1.4 narrows one enum and changes nothing else: `X-Model-Format` and `supported_model_formats` accept `onnx` alone, because no team serves anything else. A v1.3 caller that sent `safetensors` now gets `400`; a v1.3 caller that sent nothing, or sent `onnx`, is a valid v1.4 caller and sees no change at all -- `onnx` was already the default, and the request bytes, the masked bytes and every response header keep their meaning.
