# Test Plan for FIPS compatibility

This document describes **manual** procedures and **automated** TestFlows scenarios for validating FIPS-compatible `clickhouse-backup` builds (`clickhouse-backup-fips`, `clickhouse-backup-race-fips`).

## Execution model

**Manual test** — Steps are run by an operator on a host (shell, Docker, `openssl`). They are documented here for local smoke and debugging. TestFlows does **not** run them unless you execute the commands yourself.

**Automated test** — Steps are executed by TestFlows scenarios plus the regular regression suites. In the current repository state, `regression.py` runs `/clickhouse backup/*` suites by default and the dedicated FIPS feature is optional (not enabled by default in `regression.py`).

**Manual + automated** — The same behavior is documented as a manual procedure **and** implemented as a TestFlows scenario. Manual steps are optional for operators; automated runs are required for regression.

| Test Case | Manual | Automated | TestFlows source |
|-----------|:------:|:---------:|--------------------------------|
| 1a | Yes | No | — |
| 1b | No | Yes | Full `/clickhouse backup/*` regression (smoke, cloud_storage, api, cli, generic, …); optional FIPS connectivity subset: `fips_binary_connectivity_fips_clickhouse` (from `fips_old.py`, when enabled) |
| 2a | Yes | No | — |
| 2b | No | Yes | `fips_binary_connectivity_nonfips_clickhouse` (optional: full regression) |
| 2c | Yes | No | — |
| 3 | Yes | Yes | `fips_only_mode_version` |
| 4 | Yes | Yes | `gofips140_build_flags_present` |
| 5 | Yes | Yes | `checksum_tamper_panics` |
| 6 | Yes | Yes | `failfipscast_known_answer_tests` |
| 7 | Yes | Yes | `inbound_tls_cipher_negotiation` |
| 8 | No | Yes | `outbound_tls_cipher_negotiation` |

Supporting automated checks (not numbered): `readelf_go_fipsinfo_offset`, `acvp_wrapper` (optional, `RUN_ACVP_TESTS=1`).

Related manual TLS/S3 procedures: [`openssl_tests.md`](openssl_tests.md).

Current wiring note:
- `/clickhouse backup/*` coverage is always available through `regression.py`.
- Dedicated FIPS scenarios (TC 2b, 3-8) are defined in `fips_old.py` and require explicit enablement in the regression entrypoint.

## FIPS runtime modes (`GOFIPS140` and `GODEBUG`)

FIPS artifacts are built with **`GOFIPS140=v1.0.0`** ([`Makefile`](../../../../Makefile), [`Dockerfile`](../../../../Dockerfile)). Per [Go FIPS 140-3 compliance](https://go.dev/doc/security/fips140), that selects the frozen Go Cryptographic Module and **enables FIPS 140-3 mode by default at runtime** (integrity self-check, CASTs, approved TLS negotiation). Setting `GODEBUG=fips140=on` is not required for FIPS mode on these binaries.

The runtime [`fips140` GODEBUG option](https://go.dev/doc/godebug) is documented in [FIPS 140-3 compliance — The `fips140` GODEBUG option](https://go.dev/doc/security/fips140#the-fips140-godebug-option). API reference: [`crypto/fips140`](https://pkg.go.dev/crypto/fips140) (`Enabled()`, `Enforced()`).

| Runtime setting | Used in this plan for | Official behavior | `crypto/fips140` |
|-----------------|----------------------|-------------------|------------------|
| *(unset)* on a `GOFIPS140=v1.0.0` binary | Manual TC 1a/2a connectivity (step 6) | FIPS 140-3 mode on (build-time default) | `Enabled()` true, `Enforced()` false |
| `GODEBUG=fips140=on` | TC 6 CAST failures; automated TC 2b connectivity (plain TCP) | FIPS mode on; required together with `GODEBUG=failfipscast=...` | `Enabled()` true, `Enforced()` false |
| `GODEBUG=fips140=only` | TC 1b (automated 1a strict path); TC 2c/7/8 TLS policy; TC 3 version check | FIPS mode on **and** strict enforcement (non-approved crypto errors/panics). Go states this is for [*testing, assessment, and debugging*](https://go.dev/doc/security/fips140), *not intended for production*, and *not required by the Security Policy*. | `Enabled()` true, `Enforced()` true |

**Alignment with what we test:**

- **Production-like FIPS operation** (module active): run the FIPS binary with **no** `GODEBUG` (manual TC 1a/2a step 6), or with `GODEBUG=fips140=on` where noted (TC 6, TC 2b).
- **Strict runtime** (`GODEBUG=fips140=only`): automated TC 1b (counterpart to manual 1a optional step), TLS policy cases (2c, 7, 8), and TC 3.
- **`fips140=only` is not mandatory for FIPS 140-3 compliance** on a `GOFIPS140=v1.0.0` binary; it is an extra strict layer used here for policy and negative-path checks.

**Repository note:** the FIPS Docker image target `image_fips` sets `ENV GODEBUG=fips140=only` in [`Dockerfile`](../../../../Dockerfile) (stricter than Go’s production guidance). Manual steps below state the exact `GODEBUG` per command.

---

## Test Case 1a

**Execution:** Manual only (not automated in TestFlows).

### Local smoke workflow

Goal: verify local `clickhouse-backup` FIPS binary can connect to FIPS-compatible ClickHouse server.

Steps:

Minimal path: run steps `1 -> 2 -> 3 -> 5 -> 6`.  
Step `4` is diagnostic-only (use it when you need to prove effective merged ClickHouse config).

1. Build FIPS-compatible `clickhouse-backup`:

```bash
source ~/venv/qa/bin/activate
make clean build-race-fips-docker
```

2. Create TLS cert files and FIPS ClickHouse config XML (`/etc/clickhouse-server/config.d/fips.xml`):

```bash
rm -rf /tmp/ch-fips-certs
mkdir -p /tmp/ch-fips-certs
openssl req -x509 -nodes -newkey rsa:4096 -days 365 \
  -keyout /tmp/ch-fips-certs/server.key \
  -out /tmp/ch-fips-certs/server.crt \
  -subj "/CN=localhost"
chmod 755 /tmp/ch-fips-certs
chmod 644 /tmp/ch-fips-certs/server.crt /tmp/ch-fips-certs/server.key

rm -f /tmp/ch-fips.xml
cat > /tmp/ch-fips.xml <<'EOF'
<clickhouse>
  <!-- disable insecure listeners -->
  <http_port remove="1"/>
  <tcp_port remove="1"/>
  <mysql_port remove="1"/>
  <postgresql_port remove="1"/>
  <grpc_port remove="1"/>

  <!-- enable secure listeners -->
  <https_port>8443</https_port>
  <tcp_port_secure>9440</tcp_port_secure>

  <openSSL>
    <server>
      <certificateFile>/etc/clickhouse-server/certs/server.crt</certificateFile>
      <privateKeyFile>/etc/clickhouse-server/certs/server.key</privateKeyFile>
      <cipherList>ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:AES128-GCM-SHA256:AES256-GCM-SHA384</cipherList>
      <cipherSuites>TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384</cipherSuites>
      <loadDefaultCAFile>true</loadDefaultCAFile>
      <cacheSessions>true</cacheSessions>
      <preferServerCiphers>true</preferServerCiphers>
      <disableProtocols>sslv2,sslv3,tlsv1,tlsv1_1</disableProtocols>
      <!-- use 'strict' with trusted CA in production -->
      <verificationMode>relaxed</verificationMode>
    </server>
  </openSSL>
</clickhouse>
EOF
```

3. Start FIPS-compatible ClickHouse server container with mounted config:

```bash
docker rm -f ch-fips 2>/dev/null || true
docker run -d --name ch-fips \
  -p 8443:8443 -p 9440:9440 \
  -e CLICKHOUSE_USER=backup \
  -e CLICKHOUSE_PASSWORD=backup123 \
  -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 \
  -v /tmp/ch-fips.xml:/etc/clickhouse-server/config.d/fips.xml:ro \
  -v /tmp/ch-fips-certs:/etc/clickhouse-server/certs:ro \
  altinity/clickhouse-server:25.3.8.30001.altinityfips
```

4. Optional diagnostics: print effective ClickHouse server configuration:

```bash
docker exec ch-fips sh -c 'echo "===== /etc/clickhouse-server/config.xml ====="; sed -n "1,220p" /etc/clickhouse-server/config.xml'
docker exec ch-fips sh -c 'for f in /etc/clickhouse-server/config.d/*.xml; do [ -f "$f" ] && echo "===== $f =====" && sed -n "1,220p" "$f"; done'
docker exec ch-fips sh -c 'grep -R -n "<http_port>\\|<tcp_port>\\|<https_port>\\|<tcp_port_secure>\\|<mysql_port>\\|<postgresql_port>\\|<grpc_port>" /etc/clickhouse-server'
docker exec ch-fips sh -c 'echo "===== /var/lib/clickhouse/preprocessed_configs/config.xml ====="; grep -n "<http_port>\\|<tcp_port>\\|<https_port>\\|<tcp_port_secure>\\|<mysql_port>\\|<postgresql_port>\\|<grpc_port>" /var/lib/clickhouse/preprocessed_configs/config.xml'
docker exec ch-fips sh -c '(ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null || true) | grep -E ":8443|:9440|:8123|:9000" || true'
```

5. Create minimal local config for `clickhouse-backup-fips` (native TLS):

```bash
rm -f /tmp/ch-backup-fips.yml
cat > /tmp/ch-backup-fips.yml <<'EOF'
general:
  remote_storage: none
clickhouse:
  host: 127.0.0.1
  port: 9440
  username: backup
  password: "backup123"
  secure: true
  skip_verify: true
EOF
```

6. Verify connectivity (production-like FIPS runtime — no `GODEBUG`):

The binary is built with `GOFIPS140=v1.0.0`, so FIPS 140-3 mode is already active ([build-time default](https://go.dev/doc/security/fips140#the-gofips140-environment-variable)). This step intentionally does **not** set `GODEBUG`.

```bash
./clickhouse-backup/clickhouse-backup-race-fips -c /tmp/ch-backup-fips.yml tables
```

Optional — strict enforcement (`GODEBUG=fips140=only`; **same runtime as automated Test Case 1b**):

```bash
GODEBUG=fips140=only ./clickhouse-backup/clickhouse-backup-race-fips -c /tmp/ch-backup-fips.yml tables
```

Go documents `only` as a [testing/debug mode](https://go.dev/doc/security/fips140#the-fips140-godebug-option). This repository uses it for FIPS regression (TC 1b, 2c, 7, 8) and matches the FIPS Docker image default (`ENV GODEBUG=fips140=only`).

Expected result:
- Step 6: `tables` succeeds over secure native port (`9440`) without authentication/connection errors; `clickhouse-backup-race-fips --version` reports `FIPS 140-3: true` even without `GODEBUG`.
- Optional `only` step: same success on this positive path (strict mode does not block approved FIPS TLS to FIPS-compatible ClickHouse).
- If step `4` is executed, output confirms secure ports are enabled via `/etc/clickhouse-server/config.d/fips.xml`.

## Test Case 1b

**Execution:** Automated only.

**Scope:** full TestFlows regression — verify `clickhouse-backup` functionality (backup/restore, API, CLI, cloud storage, engines, etc.) against a FIPS-compatible ClickHouse image.

**Relationship to Test Case 1a:** 1a is a **manual connectivity smoke**; 1b is the **automated functional regression** on the same stack (FIPS ClickHouse + strict `GODEBUG=fips140=only` harness). The narrow scenario `fips_binary_connectivity_fips_clickhouse` in [`fips_old.py`](../tests/fips_old.py) is only a quick subset, not the whole of 1b.

**Runtime:** `GODEBUG=fips140=only` on the regression process (strict harness). Dedicated FIPS module scenarios (TC 2b, 3-8 in `fips_old.py`) set per-command `GODEBUG` where needed when that module is enabled.

**Binary note:** set `CLICKHOUSE_BACKUP_FIPS_BINARY` to the FIPS build for FIPS-specific scenarios. In current cluster wiring, [`cluster.py`](../../helpers/cluster.py) mounts `clickhouse-backup-race` as `/bin/clickhouse-backup` by default; interpret `/clickhouse backup/*` results as functional compatibility against FIPS ClickHouse unless the container binary is explicitly switched to `clickhouse-backup-race-fips`.

### FIPS-compatible `clickhouse-backup` vs FIPS-compatible ClickHouse Server

Goal: run `clickhouse-backup` regression against FIPS-compatible ClickHouse (`25.3.8.30001.altinityfips`) under `GODEBUG=fips140=only`, keeping explicit FIPS server override (`config.d/fips.xml`) enabled.

Steps:

1. Create TestFlows ClickHouse FIPS override file (`test/testflows/clickhouse_backup/configs/clickhouse/config.d/fips.xml`):

```bash
rm -f test/testflows/clickhouse_backup/configs/clickhouse/config.d/fips.xml
cat > test/testflows/clickhouse_backup/configs/clickhouse/config.d/fips.xml <<'EOF'
<clickhouse>
  <!-- disable insecure listeners -->
  <http_port remove="1"/>
  <tcp_port remove="1"/>
  <mysql_port remove="1"/>
  <postgresql_port remove="1"/>
  <grpc_port remove="1"/>

  <https_port>8443</https_port>
  <tcp_port_secure>9440</tcp_port_secure>
  <openSSL>
    <server>
      <cipherList>ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:AES128-GCM-SHA256:AES256-GCM-SHA384</cipherList>
      <cipherSuites>TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384</cipherSuites>
      <preferServerCiphers>true</preferServerCiphers>
      <disableProtocols>sslv2,sslv3,tlsv1,tlsv1_1</disableProtocols>
      <verificationMode>relaxed</verificationMode>
    </server>
  </openSSL>
</clickhouse>
EOF
```

2. Build binaries and run full regression with FIPS ClickHouse image:

```bash
source ~/venv/qa/bin/activate
make clean build-race-docker build-race-fips-docker
export CLICKHOUSE_TESTS_DIR="$(pwd)/test/testflows/clickhouse_backup"
export CLICKHOUSE_BACKUP_FIPS_BINARY="$(pwd)/clickhouse-backup/clickhouse-backup-race-fips"
GODEBUG=fips140=only \
CLICKHOUSE_IMAGE=altinity/clickhouse-server \
CLICKHOUSE_VERSION=25.3.8.30001.altinityfips \
python3 test/testflows/clickhouse_backup/regression.py \
  --only "/clickhouse backup/*" \
  --skip "/clickhouse backup/config rbac/*" \
  --log "test/testflows/ch_backup25.3.8.30001.altinityfips.fips.raw.log" \
  |& tee "test/testflows/ch_backup25.3.8.30001.altinityfips.fips.console.log"
```

`GODEBUG=fips140=only` sets strict mode for the whole TestFlows process (same as the FIPS Docker image).

Optional narrow connectivity-only check (subset of 1b; requires dedicated FIPS feature enabled in `regression.py`):

```bash
GODEBUG=fips140=only \
CLICKHOUSE_IMAGE=altinity/clickhouse-server \
CLICKHOUSE_VERSION=25.3.8.30001.altinityfips \
python3 test/testflows/clickhouse_backup/regression.py \
  --only "/clickhouse backup/fips/fips binary connectivity fips clickhouse" \
  --log "test/testflows/ch_backup25.3.8.30001.altinityfips.fips.connectivity-only.log" \
  |& tee "test/testflows/ch_backup25.3.8.30001.altinityfips.fips.connectivity-only.console.log"
```

Note: FIPS scenarios define required `GODEBUG` per command (for example TC 6 uses `GODEBUG=failfipscast=...,fips140=on`), so runtime behavior for those checks is self-contained and does not depend on the outer process-level `GODEBUG` value.

3. Optional verification (prove `fips.xml` was mounted in ClickHouse containers).

Run this in a second terminal **while step 2 is still running**. Containers are created with random names and removed when regression exits, so `docker exec clickhouse1 ...`/`clickhouse2` will not work after the run.

```bash
docker ps --filter "ancestor=altinity/clickhouse-server:25.3.8.30001.altinityfips" --format 'ID={{.ID}} NAME={{.Names}}'
docker exec "$(docker ps --filter "ancestor=altinity/clickhouse-server:25.3.8.30001.altinityfips" --format '{{.ID}}' | sed -n '1p')" sh -c 'test -f /etc/clickhouse-server/config.d/fips.xml && echo "fips.xml present on $HOSTNAME"'
docker exec "$(docker ps --filter "ancestor=altinity/clickhouse-server:25.3.8.30001.altinityfips" --format '{{.ID}}' | sed -n '2p')" sh -c 'test -f /etc/clickhouse-server/config.d/fips.xml && echo "fips.xml present on $HOSTNAME"'
```

4. Remove temporary TestFlows FIPS override file after the run:

```bash
test -f test/testflows/clickhouse_backup/configs/clickhouse/config.d/fips.xml && echo "fips.xml present before cleanup"
rm -f test/testflows/clickhouse_backup/configs/clickhouse/config.d/fips.xml
```

Expected result:
- Regression is launched with `GODEBUG=fips140=only` against FIPS-compatible ClickHouse.
- Stable suite set (`smoke`, `cloud storage`, `other engines`, `api`, `cli`, `generic`, `views`) runs against FIPS-compatible ClickHouse.
- Core backup workflows (create/restore/list, API, remote storage paths exercised by this lane) work under FIPS ClickHouse TLS settings.
- Logs are available in `test/testflows/`.
- If optional step `3` is executed, both ClickHouse containers report `/etc/clickhouse-server/config.d/fips.xml` present.
- Test case `config_rbac/*` is skipped only in this 1b command (temporary 1b-scoped xfail equivalent), so it does not affect default project regression behavior.
- Known limitation: `config_rbac/configs backup restore` may fail in this lane due test harness file-mirroring assumption around `fips.xml` (`cat /etc/clickhouse-server/config.d/fips.xml`); treat that as test setup issue, not product regression.
- Deferred improvement for complete test/CI-CD coverage: revise cluster/test wiring so full `/clickhouse backup/*` can run with `fips.xml` enabled and `config_rbac` passes without exclusions/workarounds.
- Optional narrow run: `fips_binary_connectivity_fips_clickhouse` passes (`tables` with `GODEBUG=fips140=only`).

## Test Case 2a

**Execution:** Manual only (not automated in TestFlows).

### FIPS-compatible `clickhouse-backup` vs FIPS-incompatible ClickHouse Server

Goal: verify local FIPS-compatible `clickhouse-backup` binary can connect to FIPS-incompatible ClickHouse server.

Steps:

1. Build FIPS-compatible `clickhouse-backup`:

```bash
source ~/venv/qa/bin/activate
make clean build-race-fips-docker
```

2. Start non-FIPS ClickHouse server container:

```bash
docker rm -f ch-nonfips 2>/dev/null || true
ss -ltn | grep -E ':9000\b' >/dev/null && {
  echo "port 9000 is already in use on host; stop conflicting service/container first"
}
docker run -d --name ch-nonfips \
  -p 9000:9000 -p 8123:8123 \
  -e CLICKHOUSE_USER=backup \
  -e CLICKHOUSE_PASSWORD=backup123 \
  -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 \
  altinity/clickhouse-server:25.8.16.10002.altinitystable
```

3. Optional. Print ClickHouse server configuration files used in the container:

```bash
docker exec ch-nonfips sh -c 'echo "===== /etc/clickhouse-server/config.xml ====="; sed -n "1,220p" /etc/clickhouse-server/config.xml'
docker exec ch-nonfips sh -c 'for f in /etc/clickhouse-server/config.d/*.xml; do [ -f "$f" ] && echo "===== $f =====" && sed -n "1,220p" "$f"; done'
```

4. Create local config for `clickhouse-backup-fips`:

```bash
rm -f /tmp/ch-backup-nonfips.yml
cat > /tmp/ch-backup-nonfips.yml <<'EOF'
general:
  remote_storage: none
clickhouse:
  host: 127.0.0.1
  port: 9000
  username: backup
  password: "backup123"
  secure: false
EOF
```

Note: `secure: false` is intentional in this local connectivity smoke check. TLS/cipher policy enforcement is validated in Test Case 2c (native TLS negative), Test Case 7 (inbound API), and Test Case 8 (outbound).  
This case follows the official default native protocol port (`9000`) from `ReadMe.md`.

5. Verify connectivity (production-like FIPS runtime — no `GODEBUG`):

```bash
./clickhouse-backup/clickhouse-backup-race-fips -c /tmp/ch-backup-nonfips.yml tables
```

Expected result:
- `tables` command succeeds against non-FIPS ClickHouse server (plain TCP via host `127.0.0.1:9000`, `secure: false`).

## Test Case 2b

**Execution:** Automated only.

**TestFlows scenario:** `fips_binary_connectivity_nonfips_clickhouse` in [`fips_old.py`](../tests/fips_old.py) (requires dedicated FIPS feature enabled in `regression.py`).

**Runtime in automation:** `GODEBUG=fips140=on` on the `tables` command (not `only`).

### FIPS-compatible `clickhouse-backup` vs FIPS-incompatible ClickHouse

Goal: make a practical, repeatable check that the FIPS-built `clickhouse-backup` can connect to a non-FIPS ClickHouse server.

Steps:

1. Build binaries required by TestFlows:

```bash
source ~/venv/qa/bin/activate
make clean build-race-docker build-race-fips-docker
ls -l clickhouse-backup/clickhouse-backup-race clickhouse-backup/clickhouse-backup-race-fips
```

2. Set TestFlows context:

```bash
export CLICKHOUSE_TESTS_DIR="$(pwd)/test/testflows/clickhouse_backup"
export CLICKHOUSE_BACKUP_FIPS_BINARY="$(pwd)/clickhouse-backup/clickhouse-backup-race-fips"
```

3. Run the check manually (works in current branch):

This is the direct equivalent of the scenario logic (`--version` + `tables` with `GODEBUG=fips140=on`) and does not depend on FIPS suite wiring in `regression.py`.

```bash
./clickhouse-backup/clickhouse-backup-race-fips --version
GODEBUG=fips140=on ./clickhouse-backup/clickhouse-backup-race-fips -c /tmp/ch-backup-nonfips.yml tables
```

4. Optional: run the same check through TestFlows automation.

Reference: scenario is defined in [`tests/fips_old.py`](../tests/fips_old.py) as `fips_binary_connectivity_nonfips_clickhouse`.

```bash
CLICKHOUSE_IMAGE=altinity/clickhouse-server \
CLICKHOUSE_VERSION=25.8.16.10002.altinitystable \
python3 test/testflows/clickhouse_backup/regression.py \
  --only "/clickhouse backup/fips/fips binary connectivity nonfips clickhouse" \
  --log "test/testflows/ch_backup25.8.16.10002.altinitystable.fips.raw.log" \
  |& tee "test/testflows/ch_backup25.8.16.10002.altinitystable.fips.console.log"
```

Reference scenario (from `tests/fips_old.py`) for automation path:

```python
@TestScenario
def fips_binary_connectivity_nonfips_clickhouse(self):
    backup = self.context.backup
    fips_bin = _ensure_fips_binary_in_backup_container()
    ch_version = _clickhouse_server_version()
    if "altinitystable" not in ch_version:
        skip(f"this scenario is only for non-FIPS ClickHouse, got version: {ch_version}")

    version_out = backup.cmd(f"{fips_bin} --version", exitcode=0)
    assert "FIPS 140-3:\t true" in version_out.output, error(version_out.output)

    tables_out = backup.cmd(
        f"GODEBUG=fips140=on {fips_bin} -c /etc/clickhouse-backup/config.yml tables",
        no_checks=True,
    )
    assert tables_out.exitcode == 0, error(tables_out.output)
```

Run only this scenario (automation path; requires FIPS suite wiring enabled):

```bash
CLICKHOUSE_IMAGE=altinity/clickhouse-server \
CLICKHOUSE_VERSION=25.8.16.10002.altinitystable \
python3 test/testflows/clickhouse_backup/regression.py \
  --only "/clickhouse backup/fips/fips binary connectivity nonfips clickhouse"
```

Expected result:
- This is a positive compatibility check and is expected to pass.
- Manual path: `--version` reports `FIPS 140-3: true`, and `tables` with `GODEBUG=fips140=on` exits with code 0 against non-FIPS ClickHouse.
- Optional automation path: scenario `fips_binary_connectivity_nonfips_clickhouse` passes with the same behavior.
- Failure indicates a regression in interoperability of the FIPS-built binary with non-FIPS ClickHouse (in `fips140=on` mode).

## Test Case 2c

**Execution:** Manual only (not automated in TestFlows).

**Runtime:** `GODEBUG=fips140=only` (strict enforcement) — see [FIPS runtime modes](#fips-runtime-modes-gofips140-and-godebug).

### Manual negative TLS (native port) with `openssl s_server`

Goal: verify strict FIPS runtime in `clickhouse-backup-fips` rejects a non-FIPS TLS profile when connecting to native port `9440`.

Reference: equivalent manual TLS negative flow is documented in [`openssl_tests.md`](openssl_tests.md) (Test Case 1, setup/test 1.2).

Prerequisite: FIPS binary built (same as Test Case 2a step 1).

Steps:

1. Create TLS certs used by ClickHouse in container:

```bash
rm -rf /tmp/ch-fips-certs
mkdir -p /tmp/ch-fips-certs
openssl req -x509 -nodes -newkey rsa:4096 -days 365 \
  -keyout /tmp/ch-fips-certs/server.key \
  -out /tmp/ch-fips-certs/server.crt \
  -subj "/CN=localhost"
chmod 755 /tmp/ch-fips-certs
chmod 644 /tmp/ch-fips-certs/server.crt /tmp/ch-fips-certs/server.key
```

2. Create non-FIPS TLS ClickHouse config (`/tmp/ch-fips.xml`):

```bash
rm -f /tmp/ch-fips.xml
cat > /tmp/ch-fips.xml <<'EOF'
<clickhouse>
  <https_port>8443</https_port>
  <tcp_port_secure>9440</tcp_port_secure>
  <openSSL>
    <server>
      <certificateFile>/etc/clickhouse-server/certs/server.crt</certificateFile>
      <privateKeyFile>/etc/clickhouse-server/certs/server.key</privateKeyFile>
      <cipherList>ECDHE-RSA-CHACHA20-POLY1305:ECDHE-RSA-AES128-SHA:AES128-SHA</cipherList>
      <cipherSuites>TLS_CHACHA20_POLY1305_SHA256</cipherSuites>
      <disableProtocols>sslv2,sslv3</disableProtocols>
      <verificationMode>none</verificationMode>
    </server>
  </openSSL>
</clickhouse>
EOF
```

3. Run non-FIPS ClickHouse with this TLS profile:

```bash
docker rm -f ch-fips 2>/dev/null || true
docker run -d --name ch-fips \
  -p 8443:8443 -p 9440:9440 \
  -e CLICKHOUSE_USER=backup \
  -e CLICKHOUSE_PASSWORD=backup123 \
  -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 \
  -v /tmp/ch-fips.xml:/etc/clickhouse-server/config.d/fips.xml:ro \
  -v /tmp/ch-fips-certs:/etc/clickhouse-server/certs:ro \
  altinity/clickhouse-server:25.8.16.10002.altinitystable
```

4. Create client config that uses secure native port (`9440`):

```bash
rm -f /tmp/ch-backup-fips.yml
cat > /tmp/ch-backup-fips.yml <<'EOF'
general:
  remote_storage: none
clickhouse:
  host: 127.0.0.1
  port: 9440
  username: backup
  password: "backup123"
  secure: true
  skip_verify: true
EOF
```

5. Start manual negative probe with `openssl s_server` (mandatory for this check):

```bash
docker rm -f ch-fips 2>/dev/null || true
openssl s_server -accept 9440 \
  -cert /tmp/ch-fips-certs/server.crt \
  -key /tmp/ch-fips-certs/server.key \
  -tls1_2 \
  -cipher 'ECDHE-RSA-CHACHA20-POLY1305' \
  -state -msg -tlsextdebug
```

6. In a separate terminal, use strict FIPS runtime and verify TLS connection is rejected:

```bash
GODEBUG=fips140=only ./clickhouse-backup/clickhouse-backup-race-fips -c /tmp/ch-backup-fips.yml tables
```

Note: this probe must run without ClickHouse bound to `9440` because `openssl s_server` occupies this port.

Expected result:
- This server profile is intentionally non-FIPS and is for negative testing only.
- In strict mode, `clickhouse-backup-fips` should fail to negotiate TLS, not return normal `tables` output, and exit with non-zero code.
- The failure is expected specifically because the server offers non-FIPS cipher `ECDHE-RSA-CHACHA20-POLY1305`.
- Typical `openssl s_server` output includes `fatal handshake_failure` and `no shared cipher`.
- Typical `clickhouse-backup-fips` output includes `remote error: tls: handshake failure` during ping/retry loop.
- Because `openssl s_server` is started with `-state -msg -tlsextdebug`, large TLS ClientHello hex dumps in output are expected and are not a failure by themselves.

## Test Case 3

**Execution:** Manual + automated.

**TestFlows scenario:** `fips_only_mode_version` — runs with `GODEBUG=fips140=only` (strict enforcement probe).

### Run clickhouse-backup-fips `--version` and check that FIPS 140-3 is true

Goal: verify FIPS-compatible `clickhouse-backup` reports FIPS mode in version output under strict runtime (`fips140=only`).

Steps:

1. Build FIPS-compatible binary:

```bash
source ~/venv/qa/bin/activate
make clean build-race-fips-docker
```

Expected result: build completes and `./clickhouse-backup/clickhouse-backup-race-fips` is available.

2. Manual — production-like runtime (no `GODEBUG`, build default):

```bash
./clickhouse-backup/clickhouse-backup-race-fips --version
```

3. Manual — strict runtime (matches automated scenario):

```bash
GODEBUG=fips140=only ./clickhouse-backup/clickhouse-backup-race-fips --version
```

Optional one-line check:

```bash
GODEBUG=fips140=only ./clickhouse-backup/clickhouse-backup-race-fips --version | grep "FIPS 140-3"
```

Expected result:
- Step 2 and step 3: output contains `FIPS 140-3: true` ([`crypto/fips140.Enabled()`](https://pkg.go.dev/crypto/fips140#Enabled)).
- `warning: GOCOVERDIR not set, no coverage data emitted` may appear in manual runs and is informational (not a failure for this test case).
- Automated `fips_only_mode_version` runs step 3 only (`GODEBUG=fips140=only`); process must not panic on `--version`.

## Test Case 4

**Execution:** Manual + automated.

**TestFlows scenario:** `gofips140_build_flags_present`.

### Check that GOFIPS140 is set in CI/CD build code (`GOFIPS140=v1.0.0`)

Goal: verify FIPS build paths in repository code explicitly set `GOFIPS140=v1.0.0`.

Steps:

1. Search build definitions for GOFIPS140 in `clickhouse-backup/Makefile` and `clickhouse-backup/Dockerfile`:

```bash
grep -n "GOFIPS140=v1.0.0" Makefile Dockerfile
```

2. Review matches and confirm they are in FIPS build targets:
- `clickhouse-backup/Makefile` target for FIPS build output.
- `clickhouse-backup/Dockerfile` command that builds `clickhouse-backup-race-fips`.

Expected result:
- `GOFIPS140=v1.0.0` is present in FIPS build definitions ([`Makefile`](../../../../Makefile), [`Dockerfile`](../../../../Dockerfile)).
- Per [Go docs](https://go.dev/doc/security/fips140#the-gofips140-environment-variable), `GOFIPS140=v1.0.0` enables FIPS 140-3 mode by default at runtime.
- No missing `GOFIPS140` setting in paths used to produce FIPS artifacts.

## Test Case 5

**Execution:** Manual + automated.

**TestFlows scenario:** `checksum_tamper_panics`.

**Runtime:** tamper script runs the binary with `GODEBUG=fips140=on` (FIPS module must be active to verify integrity).

### Corrupt `.go.fipsinfo` checksum and verify FIPS integrity self-check fails

Goal: verify startup integrity self-check rejects a tampered FIPS binary with expected `fips140: verification mismatch` failure.

Steps:

1. Build FIPS-compatible binary:

```bash
source ~/venv/qa/bin/activate
make clean build-race-fips-docker
```

2. Run checksum tamper script against FIPS binary:

```bash
./test/testflows/clickhouse_backup/scripts/tamper_go_fips_checksum.sh ./clickhouse-backup/clickhouse-backup-race-fips
```

3. Validate output:
- Script prints `.go.fipsinfo` section details and an XOR tamper step.
- Running the tampered copy panics with `fips140: verification mismatch`.
- Script ends with `OK: FIPS integrity check failed as expected`.

Expected result:
- Integrity self-check fails on startup for the tampered binary.
- Failure is explicit (`panic: fips140: verification mismatch`) and process exits non-zero.
- Covered by automated scenario `checksum_tamper_panics`.

## Test Case 6

**Execution:** Manual + automated.

**TestFlows scenario:** `failfipscast_known_answer_tests`.

**Runtime:** `GODEBUG=failfipscast=...,fips140=on` — FIPS mode must be on for CAST self-tests to run ([integrity/CAST behavior](https://go.dev/doc/security/fips140#fips-140-3-mode)).

### Simulate CAST self-test failures using `GODEBUG=failfipscast=...`

Goal: verify FIPS startup self-tests (CAST) are enforced by forcing selected CAST checks to fail and confirming `clickhouse-backup-fips` does not start successfully.

Scope note: local process startup self-test only. No network or TLS client/server role.

What is CAST?
- CAST means **Cryptographic Algorithm Self-Test** (Known Answer Test, KAT) executed at process startup in Go FIPS mode.
- Each CAST validates one cryptographic primitive/path against a known expected value before normal execution continues.
- If any CAST fails, startup aborts with a fatal FIPS self-test failure.

What is `failfipscast`?
- `GODEBUG=failfipscast=<CAST_NAME>,fips140=on` is a Go FIPS testing hook that intentionally fails one named CAST from Go's internal `allCASTs` list.
- It is for negative testing of startup self-test enforcement (not for production runtime settings).

References:
- Valid `failfipscast` values: Go FIPS test list `allCASTs` in [`crypto/internal/fips140test/cast_test.go`](https://cs.opensource.google/go/go/+/refs/tags/go1.24.0:src/crypto/internal/fips140test/cast_test.go).
- CAST/self-test behavior: [Go FIPS 140-3 mode](https://go.dev/doc/security/fips140#fips-140-3-mode).
- Optional local path: `$(go env GOROOT)/src/crypto/internal/fips140test/cast_test.go`.

Steps:

1. Build FIPS-compatible binary:

```bash
source ~/venv/qa/bin/activate
make clean build-race-fips-docker
```

2. Force failure of CAST `SHA2-256`:

```bash
GODEBUG=failfipscast=SHA2-256,fips140=on ./clickhouse-backup/clickhouse-backup-race-fips --version
```

3. Force failure of CAST `TLSv1.2-SHA2-256`:

```bash
GODEBUG=failfipscast=TLSv1.2-SHA2-256,fips140=on ./clickhouse-backup/clickhouse-backup-race-fips --version
```

4. Optional: run additional CAST examples (same command, substitute value after `failfipscast=`):

```bash
# Substitution pattern
CAST_NAME="SHA2-512"
GODEBUG="failfipscast=${CAST_NAME},fips140=on" ./clickhouse-backup/clickhouse-backup-race-fips --version

# Additional examples from Go allCASTs:
GODEBUG=failfipscast=AES-CBC,fips140=on ./clickhouse-backup/clickhouse-backup-race-fips --version
GODEBUG=failfipscast=HMAC-SHA2-256,fips140=on ./clickhouse-backup/clickhouse-backup-race-fips --version
GODEBUG=failfipscast=PBKDF2,fips140=on ./clickhouse-backup/clickhouse-backup-race-fips --version
GODEBUG=failfipscast=TLSv1.3-SHA2-256,fips140=on ./clickhouse-backup/clickhouse-backup-race-fips --version
```

5. Optional batch mode for multiple values:

```bash
for cast in "SHA2-256" "SHA2-512" "AES-CBC" "HMAC-SHA2-256" "PBKDF2" "TLSv1.2-SHA2-256" "TLSv1.3-SHA2-256"; do
  echo "=== Testing ${cast} ==="
  GODEBUG="failfipscast=${cast},fips140=on" ./clickhouse-backup/clickhouse-backup-race-fips --version && echo "UNEXPECTED PASS"
done
```

Expected result:
- Each command exits with non-zero code.
- Output contains explicit startup self-test failure text:
  - `fatal error: FIPS 140-3 self-test failed: SHA2-256: simulated CAST failure` for step 2.
  - `fatal error: FIPS 140-3 self-test failed: TLSv1.2-SHA2-256: simulated CAST failure` for step 3.
- For optional substitutions, failure text should include the selected CAST name, e.g. `... self-test failed: <CAST_NAME>: simulated CAST failure`.
- TLSv1.2 CAST failure output also includes stack-trace marker `crypto/internal/fips140/.../tls12/cast.go`.
- A zero exit code for either command is a test failure.
- Covered by automated scenario `failfipscast_known_answer_tests` (same `GODEBUG=fips140=on`).

## Test Case 7

**Execution:** Manual + automated.

**TestFlows scenario:** `inbound_tls_cipher_negotiation`.

**Runtime:** `GODEBUG=fips140=only` when starting `clickhouse-backup-fips server` (strict TLS policy enforcement).

### Validate inbound TLS cipher policy with `openssl s_client` (FIPS-compatible vs non-compatible)

Goal: verify inbound TLS policy of `clickhouse-backup-fips` API server using `openssl s_client`: FIPS-compatible TLSv1.3 cipher handshakes are allowed and non-compatible cipher handshakes are rejected. TLSv1.2 checks are optional and environment-dependent.
Role mapping:
- TLS server under test: `clickhouse-backup-fips server` (API endpoint on `:7172`).
- TLS client used to probe policy: `openssl s_client`.
Tool choice: use `s_client` here because this is an inbound policy check. Test Case 8 uses `openssl s_server` for outbound checks where `clickhouse-backup-fips` is the TLS client.

Cipher list source:
- Policy baseline: Altinity FIPS-compatible ClickHouse guidance (TLSv1.2/TLSv1.3 and FIPS-approved ciphers).
- Test baseline used by Altinity regression tests:
  - `ssl_server/tests/common.py` (`fips_140_3_compatible_tlsv1_2_cipher_suites`, `fips_140_3_compatible_tlsv1_3_cipher_suites`, `all_ciphers`, `all_tlsv1_3_ciphers`)
  - `ssl_keeper/tests/fips_ssl.py` (`fips_compatible_tlsv1_2_cipher_suites`, `all_ciphers`)
- This plan uses that same baseline style: explicit positive allowlist checks and explicit negative denylist checks.

Steps:

1. Build FIPS-compatible binary:

```bash
source ~/venv/qa/bin/activate
make clean build-race-fips-docker
```

2. Start `clickhouse-backup-fips` API server with temporary TLS certificate and explicit config.

Use `GODEBUG=fips140=only` here because this case validates **strict TLS cipher enforcement** on the API listener (same as automated `inbound_tls_cipher_negotiation`). This is a testing/debug mode per [Go FIPS docs](https://go.dev/doc/security/fips140#the-fips140-godebug-option), not the production compliance baseline for connectivity smoke tests (TC 1a/2a).

Important: even though this test validates inbound TLS on `clickhouse-backup` API server, `clickhouse-backup ... server` still requires a reachable ClickHouse backend. If ClickHouse is not reachable at startup, API listener `:7172` will not start.
Prerequisite: ClickHouse must be reachable at `127.0.0.1:9000` with user `backup` / password `backup123`.
Documentation reference:
- `ReadMe.md` API config section (`api.listen`, `api.secure`, `api.private_key_file`, `api.certificate_file`).
- `ReadMe.md` CLI section: `clickhouse-backup server - Run API server`.

If ClickHouse is not running on `127.0.0.1:9000`, start local ClickHouse first (example):

```bash
docker rm -f ch-for-api 2>/dev/null || true
docker run -d --name ch-for-api \
  -p 9000:9000 -p 8123:8123 \
  -e CLICKHOUSE_USER=backup \
  -e CLICKHOUSE_PASSWORD=backup123 \
  -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 \
  altinity/clickhouse-server:25.8.16.10002.altinitystable
```

Mandatory pre-check (run before starting API server):

```bash
ss -ltn | grep -E ':9000\b' >/dev/null || {
  echo "ClickHouse is not listening on 127.0.0.1:9000"
  echo "Start it first (example command is shown above) and retry."
}
```

2.1 Generate local CA and server TLS certificate:

```bash
TMP_TLS_DIR=/tmp/chb-fips-tls
rm -rf "$TMP_TLS_DIR" && mkdir -p "$TMP_TLS_DIR"
openssl genrsa -out "$TMP_TLS_DIR/ca-key.pem" 2048
openssl req -x509 -new -sha256 -days 1 \
  -key "$TMP_TLS_DIR/ca-key.pem" \
  -out "$TMP_TLS_DIR/ca-cert.pem" \
  -subj "/CN=chb-fips-test-ca"
openssl genrsa -out "$TMP_TLS_DIR/server-key.pem" 2048
openssl req -new \
  -key "$TMP_TLS_DIR/server-key.pem" \
  -out "$TMP_TLS_DIR/server.csr" \
  -subj "/CN=localhost"
cat > "$TMP_TLS_DIR/server-ext.cnf" <<'EOF'
subjectAltName=DNS:localhost,IP:127.0.0.1
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
authorityKeyIdentifier=keyid,issuer
EOF
openssl x509 -req -sha256 -days 1 \
  -in "$TMP_TLS_DIR/server.csr" \
  -CA "$TMP_TLS_DIR/ca-cert.pem" \
  -CAkey "$TMP_TLS_DIR/ca-key.pem" \
  -CAcreateserial \
  -out "$TMP_TLS_DIR/server-cert.pem" \
  -extfile "$TMP_TLS_DIR/server-ext.cnf"
openssl verify -CAfile "$TMP_TLS_DIR/ca-cert.pem" "$TMP_TLS_DIR/server-cert.pem"
rm -f "$TMP_TLS_DIR/server.csr" "$TMP_TLS_DIR/server-ext.cnf" "$TMP_TLS_DIR/ca-cert.srl"
```

Expected result: certificate verification command reports `server-cert.pem: OK`.

2.2 Start `clickhouse-backup-fips` API server with generated certs:

```bash
TMP_TLS_DIR=/tmp/chb-fips-tls
test -f "$TMP_TLS_DIR/ca-cert.pem" \
  && test -f "$TMP_TLS_DIR/server-cert.pem" \
  && test -f "$TMP_TLS_DIR/server-key.pem" \
  || { echo "missing TLS files from Step 2.1, rerun Step 2.1 first"; }
pkill -f "clickhouse-backup.*server" 2>/dev/null || true
sleep 1
ss -ltn | grep -E ':7172\b' >/dev/null && {
  echo "port 7172 still occupied after graceful stop, trying force stop..."
  pkill -9 -f "clickhouse-backup.*server" 2>/dev/null || true
  sleep 1
}
ss -ltn | grep -E ':7172\b' >/dev/null && { echo "port 7172 is still in use; stop conflicting process before continuing"; }
rm -f /tmp/ch-backup-fips.yml
cat > /tmp/ch-backup-fips.yml <<'EOF'
general:
  remote_storage: none
clickhouse:
  host: 127.0.0.1
  port: 9000
  username: backup
  password: "backup123"
  secure: false
EOF
pkill -x clickhouse-backup-race-fips 2>/dev/null || true
API_SECURE=true API_LISTEN=0.0.0.0:7172 \
API_PRIVATE_KEY_FILE="$TMP_TLS_DIR/server-key.pem" \
API_CERTIFICATE_FILE="$TMP_TLS_DIR/server-cert.pem" \
GODEBUG=fips140=only \
./clickhouse-backup/clickhouse-backup-race-fips -c /tmp/ch-backup-fips.yml server >"$TMP_TLS_DIR/server.log" 2>&1 &
sleep 2
ss -ltn | grep -E ':7172\b' >/dev/null || { echo "server did not start"; grep -n "." "$TMP_TLS_DIR/server.log" || true; }
```

If the startup check fails, review `"$TMP_TLS_DIR/server.log"` and fix the environment before continuing.

Expected result:
- API server listens on `:7172` with TLS enabled.
- Shell may print a background job marker such as `[1] <pid>` because the server is started with `&` (this is expected).
- On reruns in the same shell, a line like `[1]+ Done ...` may appear for a prior background job after `pkill`; this is benign job-control output.

Pre-setup requirement for Steps 3+: run this check and continue only if all files exist:

```bash
test -f /tmp/chb-fips-tls/ca-cert.pem \
  && test -f /tmp/chb-fips-tls/server-cert.pem \
  && test -f /tmp/chb-fips-tls/server-key.pem \
  || { echo "missing TLS files, rerun Step 2"; }
```

3. Run `openssl s_client` with FIPS-compatible TLSv1.3 ciphersuite (`cipherSuites` equivalent):

```bash
openssl s_client -connect localhost:7172 -brief -tls1_3 \
  -ciphersuites TLS_AES_128_GCM_SHA256 \
  -CAfile /tmp/chb-fips-tls/ca-cert.pem < /dev/null
```

Expected result (Positive): success (`CONNECTION ESTABLISHED`, TLSv1.3 handshake completed).

4. Run `openssl s_client` with non-compatible TLSv1.3 ciphersuite:

```bash
openssl s_client -connect localhost:7172 -brief -tls1_3 \
  -ciphersuites TLS_CHACHA20_POLY1305_SHA256 \
  -CAfile /tmp/chb-fips-tls/ca-cert.pem < /dev/null
```

Expected result (Negative): failure (`alert handshake failure`, or equivalent), because `TLS_CHACHA20_POLY1305_SHA256` is intentionally outside the FIPS-compatible allowlist used by this inbound TLS policy check.

5. Optional TLSv1.2 negative test using `-cipher` (`cipherList` equivalent):

```bash
openssl s_client -connect localhost:7172 -brief -tls1_2 \
  -cipher AES128-GCM-SHA256 \
  -CAfile /tmp/chb-fips-tls/ca-cert.pem < /dev/null
```

Expected result (Negative): failure (`alert handshake failure`, or equivalent), because `AES128-GCM-SHA256` maps to a TLSv1.2 RSA key-exchange suite that is not offered by this server policy.

5.1 Optional TLSv1.2 negative probe B using `-cipher`:

```bash
openssl s_client -connect localhost:7172 -brief -tls1_2 \
  -cipher ECDHE-RSA-CHACHA20-POLY1305 \
  -CAfile /tmp/chb-fips-tls/ca-cert.pem < /dev/null
```

Expected result (Negative): failure (`handshake failure`, `alert handshake failure`, or equivalent), because `ECDHE-RSA-CHACHA20-POLY1305` is intentionally outside the allowed cipher policy.

6. Use `openssl s_client` to perform a real HTTPS request against the REST API (`/health`):

```bash
printf 'GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n' | \
openssl s_client -connect localhost:7172 -servername localhost -quiet -tls1_3 \
  -ciphersuites TLS_AES_128_GCM_SHA256 \
  -CAfile /tmp/chb-fips-tls/ca-cert.pem
```

Expected result (Positive): HTTP response is returned over TLS (for example `HTTP/1.1 200 OK` and health payload).

7. Optional: use REST client (`curl`) over TLS to verify API endpoint reachability:

```bash
curl --fail --silent --show-error \
  --cacert /tmp/chb-fips-tls/ca-cert.pem \
  https://localhost:7172/health

curl --fail --silent --show-error \
  --cacert /tmp/chb-fips-tls/ca-cert.pem \
  https://localhost:7172/backup/list || true
```

Expected result:
- Positive check: `/health` succeeds.
- Environment-dependent check: `/backup/list` returns JSON when backend/storage is ready; non-zero exit is acceptable in minimal local smoke setup.

If `curl` fails with `SSL certificate problem: self-signed certificate`, rerun Step 2 and confirm cert chain validation line `server-cert.pem: OK` appears from `openssl verify`.

8. Stop test server:

```bash
pkill -f "clickhouse-backup-race-fips server" 2>/dev/null || true
```

Expected result: background `clickhouse-backup-fips server` process stops.

Notes:
- The same inbound TLS policy applies to endpoints on this secure listener, including `/metrics` when metrics are enabled on the API server.
- Covered by automated scenario `inbound_tls_cipher_negotiation` (TLSv1.3 allow/deny, `GODEBUG=fips140=only`).

## Test Case 8

**Execution:** Manual + automated.

**TestFlows scenario:** `outbound_tls_cipher_negotiation`.

**Runtime:** `GODEBUG=fips140=only` on `list remote` (strict outbound TLS enforcement).

### Validate outbound TLS policy with `openssl s_server` (clickhouse-backup as TLS client)

Goal: verify `clickhouse-backup-fips` rejects a remote TLS server that offers only non-FIPS ciphers.

Scope difference from Test Case 7: TC 7 validates **inbound** policy on `clickhouse-backup-fips server`; TC 8 validates **outbound** policy when `clickhouse-backup-fips` connects to remote storage over TLS.
Role mapping:
- TLS client under test: `clickhouse-backup-fips` (`list remote` against HTTPS S3 endpoint).
- TLS server used to control offered cipher policy: `openssl s_server`.

Steps:

1. Build binaries required by TestFlows environment:

```bash
source ~/venv/qa/bin/activate
make clean build-race-docker build-race-fips-docker
```

2. Prepare local TLS cert/key for `openssl s_server`:

```bash
TMP_S3_TLS_DIR=/tmp/ch-s3-fips-certs
rm -rf "$TMP_S3_TLS_DIR"
mkdir -p "$TMP_S3_TLS_DIR"
openssl req -x509 -nodes -newkey rsa:4096 -days 365 \
  -keyout "$TMP_S3_TLS_DIR/server.key" \
  -out "$TMP_S3_TLS_DIR/server.crt" \
  -subj "/CN=localhost"
chmod 755 "$TMP_S3_TLS_DIR"
chmod 644 "$TMP_S3_TLS_DIR/server.crt" "$TMP_S3_TLS_DIR/server.key"
```

3. Create local S3-style config used for both positive and negative checks:

```bash
cat > /tmp/ch-backup-s3-openssl.yml <<'EOF'
general:
  remote_storage: s3
clickhouse:
  host: 127.0.0.1
  port: 9000
  username: backup
  password: "backup123"
  secure: false
s3:
  access_key: TESTACCESSKEY1234
  secret_key: TESTSECRETKEY1234567890
  bucket: test
  region: us-east-1
  endpoint: https://127.0.0.1:9443
  force_path_style: true
  disable_ssl: false
  disable_cert_verification: true
EOF
```

4. Positive check: compatible cipher should not be rejected by TLS policy.

Terminal #1:

```bash
pkill -f "openssl s_server -accept 9443" 2>/dev/null || true
openssl s_server -accept 9443 \
  -cert /tmp/ch-s3-fips-certs/server.crt \
  -key /tmp/ch-s3-fips-certs/server.key \
  -tls1_2 \
  -cipher 'ECDHE-RSA-AES256-GCM-SHA384:ECDHE-RSA-AES128-GCM-SHA256' \
  -state -msg -tlsextdebug \
  -www
```

Terminal #2:

```bash
GODEBUG=fips140=only ./clickhouse-backup/clickhouse-backup-race -c /tmp/ch-backup-s3-openssl.yml list remote
```

5. Negative check: non-compatible cipher should be rejected by TLS policy.

Terminal #1:

```bash
pkill -f "openssl s_server -accept 9443" 2>/dev/null || true
openssl s_server -accept 9443 \
  -cert /tmp/ch-s3-fips-certs/server.crt \
  -key /tmp/ch-s3-fips-certs/server.key \
  -tls1_2 \
  -cipher 'ECDHE-RSA-CHACHA20-POLY1305' \
  -state -msg -tlsextdebug \
  -www
```

Terminal #2:

```bash
GODEBUG=fips140=only ./clickhouse-backup/clickhouse-backup-race -c /tmp/ch-backup-s3-openssl.yml list remote
```

Expected result:
- Positive check (step 4): outbound connection attempt is not rejected by TLS policy; failures may still occur later because `openssl s_server -www` is not a real S3 API.
- With `openssl s_server -state -msg -tlsextdebug`, terminal #1 output is very verbose (hex dumps, `SSL_accept`, `ClientHello`, `ServerHello`, `close_notify`); this is expected and indicates handshake trace logging is enabled.
- Negative check (step 5): TLS negotiation fails (`handshake failure`, `no shared cipher`, or equivalent TLS error).
- `openssl s_server` output in terminal #1 shows handshake details; negative check typically includes markers such as:
  - `TLS Alert ... fatal handshake_failure`
  - `SSL3 alert write:fatal:handshake failure`
  - `tls_post_process_client_hello:no shared cipher`
- Automated scenario `outbound_tls_cipher_negotiation` remains the regression signal; manual checks are execution-equivalent diagnostics.

## Final cleanup (local)

Goal: avoid docker garbage and prevent leftover test containers from starting later.

Steps:

1. Remove manual smoke container:

```bash
docker update --restart=no ch-fips 2>/dev/null || true
docker rm -f ch-fips 2>/dev/null || true
docker update --restart=no ch-nonfips 2>/dev/null || true
docker rm -f ch-nonfips 2>/dev/null || true
```

2. Remove containers that may remain after interrupted TestFlows runs:

```bash
docker rm -f clickhouse_backup clickhouse1 clickhouse2 kafka zookeeper mysql postgres rabbitmq ftp_server sftp_server minio 2>/dev/null || true
```

3. Optional: remove unused containers/networks from old runs:

```bash
docker container prune -f
docker network prune -f
```

Use `docker volume prune -f` only if you are sure you do not need any Docker volume data.

4. Optional: remove temporary local config created in manual workflow:

```bash
rm -f /tmp/ch-backup-fips.yml
rm -f /tmp/ch-backup-nonfips.yml
rm -f /tmp/ch-fips.xml
rm -rf /tmp/ch-fips-certs
```
