# Security and secrets

Credentials belong in CI protected variables, a secret manager, or local ignored files. Example files must use values such as `REPLACE_WITH_R2_ACCESS_KEY`; URLs must use non-routable documentation domains. Avoid logging request content. R2 access should be scoped to the minimum bucket prefixes, and the masking gateway should authenticate service-to-service requests.
