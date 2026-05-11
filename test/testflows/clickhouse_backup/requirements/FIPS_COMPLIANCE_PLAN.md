# ClickHouse Backup FIPS Compliance Plan (TestFlows)

## Goals

- Validate FIPS 140-3 behavior for `clickhouse-backup-fips` in repeatable tests.
- Cover binary integrity, Go CAST failures, TLS negotiation policy, and ACVP execution path.
- Keep tests runnable in TestFlows with clear skip conditions for optional tooling.

## Scope

- In scope:
  - FIPS binary checks (`--version`, `GODEBUG=fips140=on|only`)
  - Integrity self-test break simulation via `.go.fipsinfo` tampering
  - Known-answer test (CAST) forced failures (`failfipscast`)
  - Inbound TLS cipher negotiation checks using `openssl s_client`
  - Outbound TLS cipher negotiation checks using `openssl s_server`
  - ACVP wrapper invocation (when available and explicitly enabled)
- Out of scope for this PR:
  - Full outbound endpoint matrix (all remote providers and all ports)
  - Kubernetes e2e operator deployment validation
  - Continuous ACVP in default CI (expensive and environment-dependent)

## Preconditions

- Build artifacts available:
  - Preferred: `build/linux/amd64/clickhouse-backup-fips`
  - TestFlows CI fallback: `clickhouse-backup/clickhouse-backup-race-fips`
- Host tools:
  - `readelf`, `python3`
- Container tools for TLS tests:
  - `openssl` (installed dynamically in the backup test container)
- Optional:
  - `RUN_ACVP_TESTS=1` plus `pkg/acvpwrapper/run.sh`

## Core Procedures and Commands

### 1) clickhouse-backup FIPS vs non-FIPS comparison

Purpose:
- Prove that the same checks behave differently for `clickhouse-backup-race-fips` and `clickhouse-backup-race`.
- Prevent false confidence from running only the FIPS binary.

Suggested implementation in TestFlows:
- Add a scenario like `fips_vs_non_fips_binary_comparison`.
- Inputs:
  - FIPS binary: `CLICKHOUSE_BACKUP_FIPS_BINARY` (or `clickhouse-backup-race-fips`)
  - non-FIPS binary: `CLICKHOUSE_BACKUP_NON_FIPS_BINARY` (default `clickhouse-backup-race`)
- Run a minimal comparison matrix:
  - `--version` output check:
    - FIPS binary should contain `FIPS 140-3: true`.
    - non-FIPS binary should contain `FIPS 140-3: false` (or not true).
  - `GODEBUG=fips140=only ... --version` behavior:
    - FIPS binary should run successfully.
    - non-FIPS binary should fail to initialize in FIPS-only mode.
  - `failfipscast` behavior:
    - FIPS binary should fail with CAST/self-test related message.
    - non-FIPS binary should not expose FIPS CAST path in the same way (no false positive match).

Pass criteria:
- All FIPS expectations pass for the FIPS binary.
- Non-FIPS binary shows expected non-compliant behavior in strict FIPS checks.
- Any inversion (for example non-FIPS reports `FIPS 140-3: true`) is a hard fail.

Execution examples:
- FIPS comparison lane:
  - `CLICKHOUSE_BACKUP_FIPS_BINARY=./clickhouse-backup/clickhouse-backup-race-fips`
  - `CLICKHOUSE_BACKUP_NON_FIPS_BINARY=./clickhouse-backup/clickhouse-backup-race`
  - `python3 test/testflows/clickhouse_backup/regression.py --only "/clickhouse backup/fips/fips vs non fips binary comparison"`

### 2) ELF section discovery and offset extraction

- Primary command used by procedure and tests:
  - `readelf -S -W ./build/linux/amd64/clickhouse-backup-fips`
- Expected:
  - `.go.fipsinfo` section exists.
  - File offset is parseable and non-zero.

### 3) Code integrity self-check (tamper)

- Script:
  - `scripts/tamper_go_fips_checksum.sh`
- Command:
  - `scripts/tamper_go_fips_checksum.sh ./build/linux/amd64/clickhouse-backup-fips`
- Expected:
  - startup fails with `fips140: verification mismatch`.

### 4) CAST self-tests failure injection (`failfipscast`)

- Commands:
  - `GODEBUG=failfipscast=SHA2-256,fips140=on ./build/linux/amd64/clickhouse-backup-fips --version`
  - `GODEBUG=failfipscast=TLSv1.2-SHA2-256,fips140=on ./build/linux/amd64/clickhouse-backup-fips --version`
- Expected:
  - non-zero exit.
  - output indicates FIPS/CAST failure.

Reference list of CAST names can be aligned with Go source (`crypto/internal/fips140test/cast_test.go`), for example:
- `AES-CBC`, `CTR_DRBG`, `HKDF-SHA2-256`, `HMAC-SHA2-256`, `SHA2-256`, `TLSv1.2-SHA2-256`, `TLSv1.3-SHA2-256`.

### 5) `fips140=only` mode sanity

- Command:
  - `GODEBUG=fips140=only ./build/linux/amd64/clickhouse-backup-fips --version`
- Expected:
  - command succeeds.
  - output contains `FIPS 140-3: true`.

### 6) Inbound TLS policy using OpenSSL

- Launch server with TLS enabled and strict FIPS mode:
  - `API_SECURE=true API_LISTEN=0.0.0.0:7172 API_PRIVATE_KEY_FILE=... API_CERTIFICATE_FILE=... GODEBUG=fips140=only clickhouse-backup-fips server`
- Test compatible suite:
  - `openssl s_client -connect localhost:7172 -brief -tls1_3 -ciphersuites TLS_AES_128_GCM_SHA256 ...`
  - Expected: handshake success.
- Test incompatible suite:
  - `openssl s_client -connect localhost:7172 -brief -tls1_3 -ciphersuites TLS_CHACHA20_POLY1305_SHA256 ...`
  - Expected: handshake rejected.

### 7) ACVP wrapper (optional CI lane)

- Command:
  - `bash pkg/acvpwrapper/run.sh`
- Expected:
  - successful completion and pass/success markers in output.

### 8) Outbound TLS policy using OpenSSL

- Launch temporary outbound target as TLS server:
  - `openssl s_server -accept 9443 ... -tls1_3 -ciphersuites TLS_AES_128_GCM_SHA256`
- Configure `clickhouse-backup-fips` to use HTTPS S3 endpoint:
  - `s3.endpoint: https://localhost:9443`, `s3.disable_ssl: false`, `general.remote_storage: s3`
- Execute outbound call:
  - `GODEBUG=fips140=only clickhouse-backup-fips -c /etc/clickhouse-backup/config.yml list remote`
- Expected:
  - with `TLS_AES_128_GCM_SHA256`, no handshake-failure cipher error.
  - with `TLS_CHACHA20_POLY1305_SHA256`, handshake is rejected.

## TestFlows Implementation

- New feature module:
  - `test/testflows/clickhouse_backup/tests/fips.py`
- Integrated into regression:
  - `test/testflows/clickhouse_backup/regression.py`

Implemented scenarios:
- `readelf_go_fipsinfo_offset`
- `checksum_tamper_panics`
- `failfipscast_known_answer_tests`
- `fips_only_mode_version`
- `inbound_tls_cipher_negotiation`
- `outbound_tls_cipher_negotiation`
- `acvp_wrapper` (guarded by `RUN_ACVP_TESTS=1`)

Suggested next scenario:
- `fips_vs_non_fips_binary_comparison` (cross-check expected behavior differences)

## CI Strategy

- Default TestFlows run:
  - Run all FIPS scenarios except ACVP when `RUN_ACVP_TESTS` is not set.
- Optional nightly or dedicated workflow:
  - Enable `RUN_ACVP_TESTS=1` and collect ACVP logs as artifacts.
- Keep FIPS tests deterministic:
  - Explicit skip messages for missing binary/script/tooling.
  - Avoid flaky outbound network dependencies in default lane.

## Best Practices Applied

- Assert both positive and negative paths for cryptographic policy.
- Keep tampering to temp copies only; never mutate build artifact in place.
- Gate heavyweight checks (ACVP) behind explicit env toggles.
- Reuse TestFlows cluster lifecycle and cleanup hooks.
- Use command-level checks and output validation to prove behavior, not assumptions.
