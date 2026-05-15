# Test Plan for FIPS compatibility 

## Test Case 1a

### Manual: Local smoke workflow

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
docker exec ch-fips sh -c 'ss -ltn | grep -E ":8443|:9440|:8123|:9000" || true'
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

6. Verify connectivity:

```bash
./clickhouse-backup/clickhouse-backup-race-fips -c /tmp/ch-backup-fips.yml tables
```

Optional stricter runtime mode:

```bash
GODEBUG=fips140=only ./clickhouse-backup/clickhouse-backup-race-fips -c /tmp/ch-backup-fips.yml tables
```

Expected result:
- `tables` command succeeds over secure native port (`9440`) without authentication/connection errors.
- If step `4` is executed, output confirms secure ports are enabled via `/etc/clickhouse-server/config.d/fips.xml`.

## Test Case 1b

### Automation: FIPS-compatible `clickhouse-backup` vs FIPS-compatible ClickHouse Server

Goal: run automated scenarios in TestFlows using FIPS-compatible `clickhouse-backup` against FIPS-compatible ClickHouse image with explicit FIPS server config (`config.d/fips.xml`).

Steps:

Minimum reproducible path:

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

2. Build binaries and run TestFlows with FIPS binary + FIPS ClickHouse image:

```bash
source ~/venv/qa/bin/activate
make clean build-race-docker build-race-fips-docker
export CLICKHOUSE_TESTS_DIR="$(pwd)/test/testflows/clickhouse_backup"
export CLICKHOUSE_BACKUP_FIPS_BINARY="$(pwd)/clickhouse-backup/clickhouse-backup-race-fips"
CLICKHOUSE_IMAGE=altinity/clickhouse-server \
CLICKHOUSE_VERSION=25.3.8.30001.altinityfips \
GODEBUG=fips140=only \
python3 test/testflows/clickhouse_backup/regression.py \
  --only "/clickhouse backup/*" \
  --log "test/testflows/ch_backup25.3.8.30001.altinityfips.fips.raw.log" \
  |& tee "test/testflows/ch_backup25.3.8.30001.altinityfips.fips.console.log"
```

3. Optional verification (prove `fips.xml` was mounted in ClickHouse containers):

```bash
docker exec clickhouse1 sh -c 'test -f /etc/clickhouse-server/config.d/fips.xml && echo "fips.xml present on clickhouse1"'
docker exec clickhouse2 sh -c 'test -f /etc/clickhouse-server/config.d/fips.xml && echo "fips.xml present on clickhouse2"'
```

4. Remove temporary TestFlows FIPS override file after the run:

```bash
rm -f test/testflows/clickhouse_backup/configs/clickhouse/config.d/fips.xml
```

Expected result:
- All `/clickhouse backup/*` scenarios run with FIPS-compatible `clickhouse-backup` against FIPS-compatible ClickHouse image.
- Logs are available in `test/testflows/`.
- If optional step `3` is executed, both ClickHouse containers report `/etc/clickhouse-server/config.d/fips.xml` present.
- Run executes with `GODEBUG=fips140=only` (strict FIPS runtime mode).

## Test Case 2a

### Manual: FIPS-compatible `clickhouse-backup` vs FIPS-incompatible ClickHouse Server

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
docker run -d --name ch-nonfips \
  -p 9001:9000 -p 8124:8123 \
  -e CLICKHOUSE_USER=backup \
  -e CLICKHOUSE_PASSWORD=backup123 \
  -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 \
  altinity/clickhouse-server:25.8.16.10002.altinitystable
```

3. Print ClickHouse server configuration files used in the container:

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

Note: `secure: false` is intentional in this local connectivity smoke check. TLS/cipher policy enforcement is validated separately in Test Case 7 (inbound) and Test Case 8 (outbound).

5. Verify connectivity:

```bash
./clickhouse-backup/clickhouse-backup-race-fips -c /tmp/ch-backup-nonfips.yml tables
```

Expected result:
- `tables` command succeeds against non-FIPS ClickHouse server.

#### Optional extension for Test Case 2a (manual negative TLS)

Goal: verify strict FIPS runtime in `clickhouse-backup-fips` rejects non-FIPS TLS profile on non-FIPS ClickHouse image (`25.8.16.10002.altinitystable`).

Extension steps:

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

Expected extension result:
- This server profile is intentionally non-FIPS and is for negative testing only.
- In strict mode, `clickhouse-backup-fips` should fail to negotiate TLS and not return normal `tables` output.
- The failure is expected specifically because the server offers non-FIPS cipher `ECDHE-RSA-CHACHA20-POLY1305`.
- Typical `openssl s_server` output includes `fatal handshake_failure` and `no shared cipher`.
- Typical `clickhouse-backup-fips` output includes `remote error: tls: handshake failure` during ping/retry loop.

## Test Case 2b

### Automation: FIPS-compatible `clickhouse-backup` vs FIPS-incompatible ClickHouse

Goal: run all `clickhouse-backup` automated scenarios in TestFlows using FIPS-compatible `clickhouse-backup` while ClickHouse image is non-FIPS.

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

3. Run FIPS TestFlows against non-FIPS ClickHouse image:

```bash
CLICKHOUSE_IMAGE=altinity/clickhouse-server \
CLICKHOUSE_VERSION=25.8.16.10002.altinitystable \
GODEBUG=fips140=only \
python3 test/testflows/clickhouse_backup/regression.py \
  --only "/clickhouse backup/*" \
  --log "test/testflows/ch_backup25.8.16.10002.altinitystable.fips.raw.log" \
  |& tee "test/testflows/ch_backup25.8.16.10002.altinitystable.fips.console.log"
```

Expected result:
- All `/clickhouse backup/*` scenarios execute with FIPS-compatible `clickhouse-backup` against non-FIPS ClickHouse image.
- Any skips/failures are explicit and can be compared against Test Case 1b.
- Run executes with `GODEBUG=fips140=only` (strict FIPS runtime mode).

## Test Case 3

### Run clickhouse-backup-fips -version and check that FIPS 140-3 is true

Goal: verify FIPS-compatible `clickhouse-backup` binary reports FIPS mode in version output.

Steps:

1. Build FIPS-compatible binary:

```bash
source ~/venv/qa/bin/activate
make clean build-race-fips-docker
```

Expected result: build completes and `./clickhouse-backup/clickhouse-backup-race-fips` is available.

Expected result: build completes and `./clickhouse-backup/clickhouse-backup-race-fips` is available.

2. Check binary version output:

```bash
./clickhouse-backup/clickhouse-backup-race-fips --version
```

Optional one-line check:

```bash
./clickhouse-backup/clickhouse-backup-race-fips --version | grep "FIPS 140-3"
```

Expected result:
- Output contains `FIPS 140-3: true`.


## Test Case 4

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
- `GOFIPS140=v1.0.0` is present in FIPS build definitions (`clickhouse-backup/Makefile` and `clickhouse-backup/Dockerfile`).
- No missing GOFIPS140 setting in the paths used to produce FIPS artifacts.



## Test Case 5

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
- Note: Behavior matches automated TestFlows scenario `checksum_tamper_panics`.

## Test Case 6

### Simulate CAST self-test failures using `GODEBUG=failfipscast=...`

Goal: verify FIPS startup self-tests (CAST) are enforced by forcing CAST checks `SHA2-256` and `TLSv1.2-SHA2-256` to fail, and confirming `clickhouse-backup-fips` does not start successfully.
Scope note: this is a local process startup self-test of the `clickhouse-backup-fips` binary. No network handshake is involved, so there is no TLS client/server role in this case.

Reference:
- Source of valid `failfipscast` values is Go FIPS test list `allCASTs` in `crypto/internal/fips140test/cast_test.go`.
- Primary reference: `https://tip.golang.org/src/crypto/internal/fips140test/cast_test.go` (`allCASTs`, lines 39-65).
- Optional local path (if Go is installed): `$(go env GOROOT)/src/crypto/internal/fips140test/cast_test.go`.

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

Expected result:
- Each command exits with non-zero code.
- Output contains explicit startup self-test failure text:
  - `fatal error: FIPS 140-3 self-test failed: SHA2-256: simulated CAST failure` for step 2.
  - `fatal error: FIPS 140-3 self-test failed: TLSv1.2-SHA2-256: simulated CAST failure` for step 3.
- TLSv1.2 CAST failure output also includes stack-trace marker `crypto/internal/fips140/.../tls12/cast.go`.
- A zero exit code for either command is a test failure.
This manual check is covered by automated scenario `failfipscast_known_answer_tests`.

## Test Case 7

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

2. Start `clickhouse-backup-fips` API server in strict FIPS mode with temporary TLS certificate and explicit config:

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

Expected result: API server listens on `:7172` with TLS enabled.

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

Expected result (Negative): failure (`handshake failure`, `alert handshake failure`, or equivalent), because `TLS_CHACHA20_POLY1305_SHA256` is intentionally outside the FIPS-compatible allowlist used by this inbound TLS policy check.

5. Optional TLSv1.2 negative probe A using `-cipher` (`cipherList` equivalent):

```bash
openssl s_client -connect localhost:7172 -brief -tls1_2 \
  -cipher AES128-GCM-SHA256 \
  -CAfile /tmp/chb-fips-tls/ca-cert.pem < /dev/null
```

Expected result (Negative): failure (`handshake failure`, `alert handshake failure`, or equivalent), because `AES128-GCM-SHA256` maps to a TLSv1.2 RSA key-exchange suite that is not offered by this server policy.

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
- This manual check is covered by automated scenario `inbound_tls_cipher_negotiation` (TLSv1.3 allow/deny path).

## Test Case 8

### Validate outbound TLS policy with `openssl s_server` (clickhouse-backup as TLS client)

Goal: verify `clickhouse-backup-fips` rejects a remote TLS server that offers only non-FIPS ciphers.  
Scope difference from Test Case 7: Test Case 7 validates inbound policy on `clickhouse-backup-fips server`; this test validates outbound policy when `clickhouse-backup-fips` connects to remote storage over TLS.
Role mapping:
- TLS client under test: `clickhouse-backup-fips` (`list remote` against HTTPS S3 endpoint).
- TLS server used to control offered cipher policy: `openssl s_server`.

Steps:

1. Build binaries required by TestFlows environment:

```bash
source ~/venv/qa/bin/activate
make clean build-race-docker build-race-fips-docker
```

2. Run outbound TLS check against non-compatible server ciphers:

```bash
export CLICKHOUSE_TESTS_DIR="$(pwd)/test/testflows/clickhouse_backup"
export CLICKHOUSE_BACKUP_FIPS_BINARY="$(pwd)/clickhouse-backup/clickhouse-backup-race-fips"
export RUN_TESTS="/clickhouse backup/fips/outbound tls cipher negotiation"
GITHUB_ACTIONS=1 \
CLICKHOUSE_IMAGE=altinity/clickhouse-server \
CLICKHOUSE_VERSION=25.3.8.30001.altinityfips \
./test/testflows/run.sh
```

3. Review scenario output:
- The scenario starts local `openssl s_server` with FIPS-compatible cipher (`TLS_AES_128_GCM_SHA256`) and then with non-compatible cipher (`TLS_CHACHA20_POLY1305_SHA256`).
- It verifies that outbound `clickhouse-backup-fips ... list remote` handshake is accepted for the compatible server and rejected for the non-compatible server.

Expected result:
- Connection to compatible `openssl s_server` cipher is not rejected by TLS negotiation.
- Connection to non-compatible `openssl s_server` cipher fails at TLS negotiation (`handshake failure`, `no shared cipher`, or equivalent TLS error).
- Test is covered by automated scenario `outbound_tls_cipher_negotiation`.
- If AWS SDK endpoint rules block custom FIPS endpoint usage (`custom endpoint cannot be combined with fips`), scenario is skipped with explicit reason; this is an environment/platform limitation, not a cipher-policy failure.

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
