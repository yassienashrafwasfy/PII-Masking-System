# Cloudflare R2 platform boundary

R2 is the durable hand-off boundary. NiFi trusts only `incoming/team-<n>/<request-id>/<filename>` as input routing evidence and writes API output to `masked/team=team-<n>/model=<model-name>/version=<immutable-version>/<request-id>/<filename>`. It stores terminal completion/failure manifests separately. Teams receive least-privilege prefix access and may not redirect jobs using uploaded metadata.
