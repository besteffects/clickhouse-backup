# FIPS TLS Manual Checks (openssl)


- Test Case 1 (outbound): `clickhouse-backup-fips` tries to connect to `:9440` and must reject non-FIPS server cipher (`openssl s_server`).
- Test Case 2 (inbound): `openssl s_client` tries to connect to `clickhouse-backup-fips` REST server and validates allow/deny cipher behavior.

## Pre-setup (shared)

```bash
source ~/venv/qa/bin/activate
make clean build-race-fips-docker
```

---

## Test Case 1

Validate outbound TLS behavior when `clickhouse-backup-fips` connects to a TLS endpoint on `:9440` with compatible vs non-compatible cipher profiles.

### Setup 1.1 Prepare certs and non-FIPS TLS client profile

```bash
rm -rf /tmp/ch-fips-certs
mkdir -p /tmp/ch-fips-certs
openssl req -x509 -nodes -newkey rsa:4096 -days 365 \
  -keyout /tmp/ch-fips-certs/server.key \
  -out /tmp/ch-fips-certs/server.crt \
  -subj "/CN=localhost"
chmod 755 /tmp/ch-fips-certs
chmod 644 /tmp/ch-fips-certs/server.crt /tmp/ch-fips-certs/server.key

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

Expected:
- `/tmp/ch-fips-certs/server.crt` and `/tmp/ch-fips-certs/server.key` are created.
- `/tmp/ch-backup-fips.yml` is created with `secure: true` and port `9440`.

Setup is complete. Execute the following tests.

### Test 1.1 Positive test: client is not rejected by TLS policy

Run in terminal #1:

```bash
pkill -f "clickhouse-backup.*server" 2>/dev/null || true
pkill -f "openssl s_server -accept 9440" 2>/dev/null || true
docker rm -f ch-fips 2>/dev/null || true
openssl s_server -accept 9440 \
  -cert /tmp/ch-fips-certs/server.crt \
  -key /tmp/ch-fips-certs/server.key \
  -tls1_2 \
  -cipher 'ECDHE-RSA-AES256-GCM-SHA384:ECDHE-RSA-AES128-GCM-SHA256' \
  -state -msg -tlsextdebug
```

Expected:
- `openssl s_server` starts and waits for incoming TLS connections on port `9440`.
- Terminal #1 typical startup output includes `Using default temp DH parameters` and `ACCEPT`.
- After terminal #2 runs the client command, terminal #1 shows successful TLS handshake details (for example `SSL_accept...finished`, `Shared ciphers...`, `CIPHER is ECDHE-RSA-AES128-GCM-SHA256`).
- Representative successful trace in terminal #1 can look like:
  - `SSL_accept:SSLv3/TLS read client key exchange`
  - `SSL_accept:SSLv3/TLS read finished`
  - `SSL_accept:SSLv3/TLS write change cipher spec`
  - `SSL_accept:SSLv3/TLS write finished`
  - `-----BEGIN SSL SESSION PARAMETERS----- ... -----END SSL SESSION PARAMETERS-----`
  - `Shared ciphers:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384:TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384`
  - `CIPHER is ECDHE-RSA-AES128-GCM-SHA256`
  - encrypted application-data record line similar to `<<< TLS 1.2, RecordHeader ... 17 03 03 ...`
- `Terminated` is expected when this process is stopped before the next setup/test step.

Run test in terminal #2:

```bash
GODEBUG=fips140=only ./clickhouse-backup/clickhouse-backup-race-fips -c /tmp/ch-backup-fips.yml tables
```

Expected result:
- TLS-policy rejection should not occur (`remote error: tls: handshake failure` / `no shared cipher` should not be the failure reason).
- Terminal #2 may print `warning: GOCOVERDIR not set, no coverage data emitted`; this warning is acceptable and not a test failure.


### Setup 1.2 Setup negative TLS endpoint on `:9440` (non-FIPS cipher profile)

Run setup in terminal #1 (stop previous `openssl s_server` and restart):

```bash
pkill -f "openssl s_server -accept 9440" 2>/dev/null || true
docker rm -f ch-fips 2>/dev/null || true
openssl s_server -accept 9440 \
  -cert /tmp/ch-fips-certs/server.crt \
  -key /tmp/ch-fips-certs/server.key \
  -tls1_2 \
  -cipher 'ECDHE-RSA-CHACHA20-POLY1305' \
  -state -msg -tlsextdebug
```

Expected:
- If port `9440` is free, `openssl s_server` starts and waits for incoming TLS connections (typically `Using default temp DH parameters` and `ACCEPT`).
- If output still shows `Address already in use` after the cleanup commands, another process is bound to `9440`; stop that conflicting process and rerun this setup step.

### Test 1.2 Negative test: strict `clickhouse-backup-fips` rejection

Run in terminal #2:

```bash
GODEBUG=fips140=only ./clickhouse-backup/clickhouse-backup-race-fips -c /tmp/ch-backup-fips.yml tables
```

Expected result:
- Terminal #2: command fails TLS negotiation and typically shows repeating ping/retry warnings, for example `remote error: tls: handshake failure, will wait 5 second to reconnect`.
- Terminal #2: `warning: GOCOVERDIR not set, no coverage data emitted` is acceptable and not a test failure signal.
- Terminal #1: `openssl s_server` output typically includes `fatal handshake_failure`, `no shared cipher`, `SSL_accept:error in error`, and `CONNECTION CLOSED`.

---


## Test Case 2

Validate inbound TLS behavior of `clickhouse-backup-fips` REST server on `:7172` using `openssl s_client` allow/deny checks.

### Setup 2.1 Ensure local ClickHouse backend exists for API server startup

```bash
docker rm -f ch-for-api 2>/dev/null || true
docker run -d --name ch-for-api \
  -p 9000:9000 -p 8123:8123 \
  -e CLICKHOUSE_USER=backup \
  -e CLICKHOUSE_PASSWORD=backup123 \
  -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 \
  altinity/clickhouse-server:25.8.16.10002.altinitystable

ss -ltn | grep -E ':9000\b' >/dev/null || {
  echo "ClickHouse is not listening on 127.0.0.1:9000"
}
```

### Setup 2.2 Generate CA + server cert, start `clickhouse-backup-fips server`

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

pkill -f "clickhouse-backup.*server" 2>/dev/null || true
sleep 1
ss -ltn | grep -E ':7172\b' >/dev/null && {
  echo "port 7172 is still in use; stop conflicting process"
}

API_SECURE=true API_LISTEN=0.0.0.0:7172 \
API_PRIVATE_KEY_FILE="$TMP_TLS_DIR/server-key.pem" \
API_CERTIFICATE_FILE="$TMP_TLS_DIR/server-cert.pem" \
GODEBUG=fips140=only \
./clickhouse-backup/clickhouse-backup-race-fips -c /tmp/ch-backup-fips.yml server >"$TMP_TLS_DIR/server.log" 2>&1 &
sleep 2
ss -ltn | grep -E ':7172\b' >/dev/null || {
  echo "server did not start"
  grep -n "." "$TMP_TLS_DIR/server.log" || true
}
```

Setup is complete. Execute the following tests:

### Test 2.1 Positive TLSv1.3 test (allowed cipher)

```bash
openssl s_client -connect localhost:7172 -brief -tls1_3 \
  -ciphersuites TLS_AES_128_GCM_SHA256 \
  -CAfile /tmp/chb-fips-tls/ca-cert.pem < /dev/null
```

Expected result:
- Success (`CONNECTION ESTABLISHED`).

### Test 2.2 Negative TLSv1.3 test (denied cipher)

```bash
openssl s_client -connect localhost:7172 -brief -tls1_3 \
  -ciphersuites TLS_CHACHA20_POLY1305_SHA256 \
  -CAfile /tmp/chb-fips-tls/ca-cert.pem < /dev/null
```

Expected result:
- Failure (`alert handshake failure` or equivalent).

### Test 2.3 Optional TLSv1.2 negative test

```bash
openssl s_client -connect localhost:7172 -brief -tls1_2 \
  -cipher AES128-GCM-SHA256 \
  -CAfile /tmp/chb-fips-tls/ca-cert.pem < /dev/null
```

Expected result:
- Failure (`alert handshake failure` or equivalent).

### Test 2.4 Optional HTTPS `/health` check through `s_client`

```bash
printf 'GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n' | \
openssl s_client -connect localhost:7172 -servername localhost -quiet -tls1_3 \
  -ciphersuites TLS_AES_128_GCM_SHA256 \
  -CAfile /tmp/chb-fips-tls/ca-cert.pem
```

Expected result:
- HTTP response includes `200 OK` and `{"status":"OK"}`.

---

## Test Case 3

Validate outbound S3 connectivity behavior in strict FIPS mode.

Official config note:
- `clickhouse-backup` uses configurable `s3.endpoint` (see `ReadMe.md`, `s3.endpoint`).
- Standard HTTPS endpoint uses port `443` by default.
- In FIPS build, custom S3 endpoint + FIPS mode can be rejected by AWS endpoint rules before TLS handshake.

### Setup 3.1 Shared prerequisites

```bash
docker rm -f ch-for-api 2>/dev/null || true
docker run -d --name ch-for-api \
  -p 9000:9000 -p 8123:8123 \
  -e CLICKHOUSE_USER=backup \
  -e CLICKHOUSE_PASSWORD=backup123 \
  -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 \
  altinity/clickhouse-server:25.8.16.10002.altinitystable
ss -ltn | grep -E ':9000\b' >/dev/null || {
  echo "ClickHouse is not listening on 127.0.0.1:9000"
}
```

Expected:
- ClickHouse backend is listening on `127.0.0.1:9000`.

Setup is complete. Execute the following tests:

### Test 3.1 Positive test: real S3 endpoint path in strict FIPS mode

Create config (terminal #2):

```bash
cat > /tmp/ch-backup-s3-real.yml <<'EOF'
general:
  remote_storage: s3
clickhouse:
  host: 127.0.0.1
  port: 9000
  username: backup
  password: "backup123"
  secure: false
s3:
  access_key: "<AWS_ACCESS_KEY_ID>"
  secret_key: "<AWS_SECRET_ACCESS_KEY>"
  bucket: "<AWS_BUCKET>"
  region: "us-east-1"
  force_path_style: false
  disable_ssl: false
  disable_cert_verification: false
EOF

GODEBUG=fips140=only ./clickhouse-backup/clickhouse-backup-race-fips -c /tmp/ch-backup-s3-real.yml list remote
```

Expected result:
- ClickHouse ping succeeds (`tcp://127.0.0.1:9000`).
- No `S3 ResolveEndpoint ... custom endpoint cannot be combined with FIPS` error.
- Command reaches remote-list stage (actual list/auth errors depend on bucket credentials and permissions).

### Test 3.2 Negative test: custom S3 endpoint rejected in strict FIPS mode

Create config and run (terminal #2):

```bash
cat > /tmp/ch-backup-s3-custom.yml <<'EOF'
general:
  remote_storage: s3
clickhouse:
  host: 127.0.0.1
  port: 9000
  username: backup
  password: "backup123"
  secure: false
s3:
  access_key: test
  secret_key: test
  bucket: test
  endpoint: https://localhost:9443
  region: us-east-1
  force_path_style: true
  disable_ssl: false
  disable_cert_verification: true
EOF

GODEBUG=fips140=only ./clickhouse-backup/clickhouse-backup-race-fips -c /tmp/ch-backup-s3-custom.yml list remote
```

Expected result:
- ClickHouse ping succeeds (`tcp://127.0.0.1:9000`).
- Command fails before TLS handshake with endpoint-rule error:
  - `S3 ResolveEndpoint: endpoint rule error, A custom endpoint cannot be combined with FIPS`.

---

## Cleanup

```bash
pkill -f "clickhouse-backup.*server" 2>/dev/null || true
pkill -f "openssl s_server -accept 9440" 2>/dev/null || true
docker rm -f ch-for-api 2>/dev/null || true
docker rm -f ch-fips 2>/dev/null || true
rm -f /tmp/ch-backup-fips.yml
rm -f /tmp/ch-backup-s3-real.yml
rm -f /tmp/ch-backup-s3-custom.yml
rm -rf /tmp/chb-fips-tls
rm -rf /tmp/ch-fips-certs
```
