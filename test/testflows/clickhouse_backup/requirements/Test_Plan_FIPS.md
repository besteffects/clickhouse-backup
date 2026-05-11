# Test Plan for FIPS compatibility 

## Test Case 1a (Manual): Local smoke workflow

Goal: verify local `clickhouse-backup` FIPS binary can connect to FIPS-compatible ClickHouse server.

Steps:

1. Build FIPS-compatible `clickhouse-backup`:

```bash
source ~/venv/qa/bin/activate
make clean build-race-fips-docker
```

2. Start FIPS-compatible ClickHouse server container:

```bash
docker rm -f ch-fips 2>/dev/null || true
docker run -d --name ch-fips \
  -p 9000:9000 -p 8123:8123 \
  -e CLICKHOUSE_USER=backup \
  -e CLICKHOUSE_PASSWORD=backup123 \
  -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 \
  altinity/clickhouse-server:25.3.8.30001.altinityfips
```

3. Create minimal local config for `clickhouse-backup-fips`:

```bash
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
```

4. Verify binary and connectivity:

```bash
./clickhouse-backup/clickhouse-backup-race-fips --version
./clickhouse-backup/clickhouse-backup-race-fips -c /tmp/ch-backup-fips.yml tables
```

Optional stricter runtime mode:

```bash
GODEBUG=fips140=on ./clickhouse-backup/clickhouse-backup-race-fips --version
GODEBUG=fips140=on ./clickhouse-backup/clickhouse-backup-race-fips -c /tmp/ch-backup-fips.yml tables
```

Expected result:
- `--version` prints `FIPS 140-3: true`.
- `tables` command succeeds without authentication/connection errors.

## Test Case 1b (Automation): FIPS-compatible `clickhouse-backup` vs FIPS-compatible ClickHouse

Goal: run automated scenarios in TestFlows using FIPS-compatible `clickhouse-backup` against FIPS-compatible ClickHouse image.

Steps:

1. Build binaries required by TestFlows:

```bash
source ~/venv/qa/bin/activate
make clean build-race-docker build-race-fips-docker
ls -l clickhouse-backup/clickhouse-backup-race clickhouse-backup/clickhouse-backup-race-fips
```

TestFlows starts the backup container from `clickhouse-backup/clickhouse-backup-race`,
while FIPS tests additionally use `clickhouse-backup/clickhouse-backup-race-fips`.

2. Set TestFlows context:

```bash
export CLICKHOUSE_TESTS_DIR="$(pwd)/test/testflows/clickhouse_backup"
export CLICKHOUSE_BACKUP_FIPS_BINARY="$(pwd)/clickhouse-backup/clickhouse-backup-race-fips"
export RUN_TESTS="/clickhouse backup/*"
```

3. Run FIPS TestFlows against FIPS-compatible ClickHouse image:

```bash
CLICKHOUSE_IMAGE=altinity/clickhouse-server \
CLICKHOUSE_VERSION=25.3.8.30001.altinityfips \
python3 test/testflows/clickhouse_backup/regression.py \
  --only "/clickhouse backup/*" \
  --log "test/testflows/ch_backup25.3.8.30001.altinityfips.fips.raw.log" \
  |& tee "test/testflows/ch_backup25.3.8.30001.altinityfips.fips.console.log"
```

Expected result:
- FIPS suite runs; optional scenarios can be skipped with explicit reasons.
- Logs are available in `test/testflows/`.

## Test Case 2a (Manual): FIPS-compatible `clickhouse-backup` vs FIPS-incompatible ClickHouse

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

3. Create local config for `clickhouse-backup-fips`:

```bash
cat > /tmp/ch-backup-nonfips.yml <<'EOF'
general:
  remote_storage: none
clickhouse:
  host: 127.0.0.1
  port: 9001
  username: backup
  password: "backup123"
  secure: false
EOF
```

4. Verify binary and connectivity:

```bash
./clickhouse-backup/clickhouse-backup-race-fips --version
./clickhouse-backup/clickhouse-backup-race-fips -c /tmp/ch-backup-nonfips.yml tables
```

Expected result:
- `--version` prints `FIPS 140-3: true`.
- `tables` command succeeds against non-FIPS ClickHouse server.

## Test Case 2b (Automation): FIPS-compatible `clickhouse-backup` vs FIPS-incompatible ClickHouse

Goal: run automated scenarios in TestFlows using FIPS-compatible `clickhouse-backup` while ClickHouse image is non-FIPS.

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
export GODEBUG=fips140=on
```

3. Run FIPS TestFlows against non-FIPS ClickHouse image:

```bash
CLICKHOUSE_IMAGE=altinity/clickhouse-server \
CLICKHOUSE_VERSION=25.8.16.10002.altinitystable \
python3 test/testflows/clickhouse_backup/regression.py \
  --only "/clickhouse backup/*" \
  --log "test/testflows/ch_backup25.8.16.10002.altinitystable.fips.raw.log" \
  |& tee "test/testflows/ch_backup25.8.16.10002.altinitystable.fips.console.log"
```

Expected result:
- FIPS suite executes on non-FIPS ClickHouse image.
- Any skips/failures are explicit and can be compared against Test Case 1b.

## Final cleanup

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
```
