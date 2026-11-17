# Masking API v1.3

[PII Masking API Contract v1.3](PII_Masking_API_Contract_v1.3.md) is authoritative. The platform-owned gateway implements it while retaining trusted `X-Team-ID` routing to internal team adapters.

The OpenAPI document and examples are machine-readable companions. Contract changes require a new compatible version or a coordinated migration.

v1.3 adds one optional request header, `X-Document-Locale`, and changes nothing else. A v1.2 caller is a valid v1.3 caller: the header is absent, the surrogate language is detected from the document as before, and every response header keeps its meaning.
