# Publishing and release guide

How TractorBeeam365 MCP is built, published, and deployed. This is the canonical
runbook for cutting a release. It is an independent project and is not affiliated
with Veeam Software or Microsoft.

## What gets published, and where

One version is published to three places from a single GitHub Release. All three
are driven by [.github/workflows/publish.yml](.github/workflows/publish.yml).

| Target | Identifier | Auth | Public |
|--------|------------|------|--------|
| GitHub Container Registry (GHCR) | `ghcr.io/ringosystems/tractorbeeam-mcp` | `GITHUB_TOKEN` (automatic) | Yes |
| Docker Hub | `docker.io/ringosystems/tractorbeeam365-mcp` | `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` repo secrets | Yes |
| MCP Registry | `io.github.Ringosystems/tractorbeeam-mcp` | GitHub OIDC (automatic) | Yes |

Note the two image names differ on purpose: GHCR uses `tractorbeeam-mcp` (no
`365`) to match the MCP Registry server name `io.github.Ringosystems/tractorbeeam-mcp`,
and Docker Hub uses `tractorbeeam365-mcp`. Keep them as-is; the MCP Registry
verifies image ownership against the `io.modelcontextprotocol.server.name` label
in the [Dockerfile](Dockerfile), which must equal the `name` in
[server.json](server.json).

Every image is **multi-arch**: `linux/amd64` and `linux/arm64` (arm64 built under
QEMU emulation). Tags pushed per release are the exact version (e.g. `2.2.0`) and
`latest`.

## The two workflows

### CI ([.github/workflows/ci.yml](.github/workflows/ci.yml))

Runs on every push to `main`, every pull request, and on demand. It does **not**
push anything. It gates dependency and Dockerfile changes by:

1. Installing deps and running `pytest -q tests/`.
2. Building the image single-arch (`amd64`, `load: true`) and running an import
   smoke test inside it (`import httpx, boto3, cryptography, pydantic_core,
   server, ...`). This catches missing wheels or a broken base before a release.

CI stays single-arch on purpose: `load: true` cannot load a multi-arch manifest,
and PR feedback should be fast. Only the release build is multi-arch.

### Publish ([.github/workflows/publish.yml](.github/workflows/publish.yml))

Triggers: a published GitHub Release, or a manual `workflow_dispatch`. Two jobs:

1. **build-push**
   - Resolves the version by grep of `__version__` in
     [tractorbeeam365/\_\_init\_\_.py](tractorbeeam365/__init__.py). This is the
     single source of truth for the tag, so a re-run produces identical tags.
   - Sets up QEMU + Buildx, then builds `linux/amd64,linux/arm64` and pushes to
     GHCR (always) and Docker Hub (only when the Docker Hub secrets are present).
   - A shared Buildx layer cache (`type=gha`) is written by the GHCR step and
     reused by the Docker Hub step, so the slow emulated arm64 build is paid once.
   - The Docker Hub steps are **best-effort** (`continue-on-error: true`): a bad
     or missing token cannot block GHCR or the MCP Registry publish.

2. **publish-mcp-registry** (needs build-push)
   - Rewrites [server.json](server.json) so `version` and the OCI package
     `identifier` tag match the release version.
   - Installs `mcp-publisher`, authenticates with GitHub OIDC (`id-token: write`,
     no stored secret), and publishes.

## One-time setup (already done for this repo)

You only need these once per repository. They are recorded here for reproducing
the setup elsewhere.

1. **GHCR is public.** The first release publishes a private package by default.
   Make it public once: GitHub → the org/user Packages → `tractorbeeam-mcp` →
   Package settings → Change visibility → Public. Verify with an anonymous pull:
   `docker pull ghcr.io/ringosystems/tractorbeeam-mcp:latest` from a logged-out
   Docker, or `docker buildx imagetools inspect ghcr.io/ringosystems/tractorbeeam-mcp:latest`.
2. **Docker Hub secrets (optional).** To also publish to Docker Hub, add repo
   secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` (a Docker Hub access token
   with Read/Write). Without them the Docker Hub steps are skipped cleanly. The
   Docker Hub repository description is **not** synced by CI (that API rejects
   access tokens); set the short description and overview once in the Docker Hub
   UI, pasting from [DOCKERHUB.md](DOCKERHUB.md).
3. **MCP Registry.** No secret needed. It authenticates with GitHub OIDC. The
   server name must be `io.github.<Owner>/<repo-slug>` with the owner cased
   exactly as the GitHub org/user (`Ringosystems`), matching the OIDC claim, and
   the Dockerfile `io.modelcontextprotocol.server.name` label must equal it.

## Cutting a release (the normal path)

1. **Bump the version** in [tractorbeeam365/\_\_init\_\_.py](tractorbeeam365/__init__.py)
   (`__version__`). This is the only value the pipeline reads for the tag;
   [server.json](server.json) is rewritten from it at publish time, but keep
   `server.json` in sync in the commit for local correctness.
2. **Update [CHANGELOG.md](CHANGELOG.md)** with a new `## [x.y.z] - YYYY-MM-DD`
   section.
3. Commit on a branch, open a PR, let **CI** go green, and merge to `main`.
4. **Create the GitHub Release** with a tag `vX.Y.Z` (the `v` prefix is the tag
   convention; the image tag itself is the bare `X.Y.Z` from `__init__.py`):

   ```bash
   gh release create v2.2.0 --title "v2.2.0 - <summary>" --notes-file <(sed -n '/## \[2.2.0\]/,/## \[/p' CHANGELOG.md)
   ```

   Publishing the release fires the **Publish** workflow automatically.
5. **Watch it:** `gh run watch` or `gh run list -L 3`. The multi-arch build takes
   longer than the amd64-only CI because of arm64 emulation.

### Re-running without a new release

`workflow_dispatch` re-runs Publish against the current `main`. Because the tag
comes from `__init__.py`, it produces the same tags as the matching release.
Useful to retry a transient MCP Registry or Docker Hub failure.

```bash
gh workflow run Publish
```

## Verifying a release

```bash
# GHCR tag list (anonymous):
curl -s "https://ghcr.io/token?scope=repository:ringosystems/tractorbeeam-mcp:pull&service=ghcr.io" \
  | python -c "import sys,json;print(json.load(sys.stdin)['token'])" \
  | { read T; curl -s -H "Authorization: Bearer $T" https://ghcr.io/v2/ringosystems/tractorbeeam-mcp/tags/list; }

# Confirm the image is a multi-arch index with amd64 + arm64:
docker buildx imagetools inspect ghcr.io/ringosystems/tractorbeeam-mcp:2.2.0

# MCP Registry entry:
curl -s "https://registry.modelcontextprotocol.io/v0/servers?search=tractorbeeam" | python -m json.tool
```

A correct multi-arch result shows `application/vnd.oci.image.index.v1+json` with
child manifests for `linux/amd64` and `linux/arm64`. A single
`application/vnd.docker.distribution.manifest.v2+json` means the build was not
multi-arch.

## How consumers deploy

Full copy-paste instructions live in the [README](README.md#quick-deploy-prebuilt-image).
In short:

- **Persistent HTTP service:** [docker-compose.deploy.yml](docker-compose.deploy.yml)
  pulls the published GHCR image, no clone or build. `docker compose -f
  docker-compose.deploy.yml up -d`. The HTTP transport fails closed without
  `MCP_AUTH_TOKEN`.
- **Unraid:** add the template
  [deploy/unraid/tractorbeeam365.xml](deploy/unraid/tractorbeeam365.xml) by raw
  URL, or submit it to Community Applications.
- **Single MCP client (stdio):** `docker run -i --rm -e VB365_* ghcr.io/ringosystems/tractorbeeam-mcp:latest`.

## Unraid Community Applications submission

The Unraid template lives at
[deploy/unraid/tractorbeeam365.xml](deploy/unraid/tractorbeeam365.xml) and the
repository profile at [ca_profile.xml](ca_profile.xml).

- **Immediate (no approval):** users add the template by raw URL in Unraid, Docker,
  Add Container, Template field. This works as soon as the file is on `main`.
- **Get it into the CA store (searchable in the Apps tab):** submit the public repo
  at **https://ca.unraid.net/submit**. The portal live-scans the repo, parses the
  template XML, validates `ca_profile.xml`, checks for duplicates, and shows a
  preview before you submit. It is the source of truth for the current
  requirements. After listing, keep the template working and answer support in the
  forum thread. If the portal cannot find the template in `deploy/unraid/`, move it
  to the repo root or a `/templates` folder and resubmit.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Publish fails in **publish-mcp-registry** with a namespace/ownership error | The `server.json` `name` owner casing must match the GitHub owner exactly (`Ringosystems`) and equal the Dockerfile `io.modelcontextprotocol.server.name` label. |
| MCP Registry rejects the manifest on validation | `description` must be <= 100 chars; the OCI package carries the version in the `identifier` tag, not a separate `version` field. |
| Docker Hub step fails but GHCR succeeded | Expected when the token is missing or expired. The Docker Hub steps are best-effort by design and do not fail the run. Rotate `DOCKERHUB_TOKEN` if you want Docker Hub images. |
| arm64 build fails during `pip install` (tries to compile) | A dependency lacks a `musllinux` aarch64 wheel and falls back to source on the toolchain-free Alpine base. Confirm the wheel exists on PyPI, or pin a version that ships one. All current deps (cryptography, pydantic-core, etc.) ship aarch64 musllinux wheels. |
| `:latest` runs on amd64 but not on an ARM host | The ARM host pulled a release published before multi-arch (through 2.1.3). Pin `:2.2.0` or later, or re-pull once the multi-arch `:latest` is live. |
| GHCR pull asks for auth | The package is still private. Make it public (see one-time setup). |

## Release checklist

- [ ] `__version__` bumped in `tractorbeeam365/__init__.py`
- [ ] `server.json` `version` and package `identifier` tag match
- [ ] `CHANGELOG.md` has the new dated section
- [ ] CI green on `main`
- [ ] GitHub Release created with tag `vX.Y.Z`
- [ ] Publish workflow succeeded (both jobs)
- [ ] `docker buildx imagetools inspect` shows amd64 + arm64
- [ ] MCP Registry lists the new version
