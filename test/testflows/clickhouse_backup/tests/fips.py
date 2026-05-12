import os

from testflows.asserts import error
from testflows.core import *
import yaml

from clickhouse_backup.tests.common import config_modifier


def _repo_root():
    """Return repository root path resolved from this test file location."""
    here = os.path.abspath(os.path.dirname(__file__))
    return os.path.normpath(os.path.join(here, "../../../../"))


def _resolve_fips_binary():
    """Resolve host path to FIPS binary from env or known build locations."""
    env_bin = os.environ.get("CLICKHOUSE_BACKUP_FIPS_BINARY")
    candidates = [env_bin] if env_bin else []
    candidates.extend([
        os.path.join(_repo_root(), "build/linux/amd64/clickhouse-backup-fips"),
        os.path.join(_repo_root(), "clickhouse-backup/clickhouse-backup-race-fips"),
    ])
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)
    skip("FIPS binary not found, set CLICKHOUSE_BACKUP_FIPS_BINARY or build clickhouse-backup-fips artifact")


def _ensure_fips_binary_in_backup_container():
    """Copy resolved FIPS binary into backup container and return container path."""
    host_bin = _resolve_fips_binary()
    cluster = current().context.cluster
    backup = current().context.backup
    container_id = cluster.get_container_id("clickhouse_backup")
    cluster.command(None, f"docker cp \"{host_bin}\" {container_id}:/bin/clickhouse-backup-fips", exitcode=0)
    backup.cmd("chmod +x /bin/clickhouse-backup-fips")
    return "/bin/clickhouse-backup-fips"


def _clickhouse_server_version():
    """Return ClickHouse version string from clickhouse1 node in the cluster."""
    node = current().context.cluster.node("clickhouse1")
    result = node.query("select version()", exitcode=0, steps=False)
    return (result.output or "").strip()


@TestScenario
def fips_binary_connectivity_fips_clickhouse(self):
    """Test Case 1: automate FIPS binary connectivity on FIPS-compatible ClickHouse."""
    backup = self.context.backup
    fips_bin = _ensure_fips_binary_in_backup_container()

    with Given("I detect ClickHouse server version in the test cluster"):
        ch_version = _clickhouse_server_version()

    if "altinityfips" not in ch_version:
        skip(f"this scenario is only for FIPS-compatible ClickHouse, got version: {ch_version}")

    with When("I run FIPS binary version check"):
        version_out = backup.cmd(f"{fips_bin} --version", exitcode=0)

    with Then("version output should report FIPS 140-3 enabled"):
        assert "FIPS 140-3:\t true" in version_out.output, error(version_out.output)

    with When("I run tables command against FIPS-compatible ClickHouse"):
        tables_out = backup.cmd(
            f"GODEBUG=fips140=on {fips_bin} -c /etc/clickhouse-backup/config.yml tables",
            no_checks=True,
        )

    with Then("tables command should succeed"):
        assert tables_out.exitcode == 0, error(tables_out.output)


@TestScenario
def fips_binary_connectivity_nonfips_clickhouse(self):
    """Test Case 2: automate FIPS binary connectivity on non-FIPS ClickHouse."""
    backup = self.context.backup
    fips_bin = _ensure_fips_binary_in_backup_container()

    with Given("I detect ClickHouse server version in the test cluster"):
        ch_version = _clickhouse_server_version()

    if "altinitystable" not in ch_version:
        skip(f"this scenario is only for non-FIPS ClickHouse, got version: {ch_version}")

    with When("I run FIPS binary version check"):
        version_out = backup.cmd(f"{fips_bin} --version", exitcode=0)

    with Then("version output should still report FIPS 140-3 enabled"):
        assert "FIPS 140-3:\t true" in version_out.output, error(version_out.output)

    with When("I run tables command against non-FIPS ClickHouse"):
        tables_out = backup.cmd(
            f"GODEBUG=fips140=on {fips_bin} -c /etc/clickhouse-backup/config.yml tables",
            no_checks=True,
        )

    with Then("tables command should succeed"):
        assert tables_out.exitcode == 0, error(tables_out.output)


@TestScenario
def fips_only_mode_version(self):
    """Test Case 3: validate fips140=only mode reports FIPS-enabled binary."""
    cluster = self.context.cluster
    fips_bin = _resolve_fips_binary()

    with When("I run the binary in fips140=only mode"):
        result = cluster.command(None, f"GODEBUG=fips140=only \"{fips_bin}\" --version", exitcode=0)

    with Then("version output should report FIPS 140-3 enabled"):
        assert "FIPS 140-3:\t true" in result.output, error(result.output)


@TestScenario
def gofips140_build_flags_present(self):
    """Test Case 4: validate GOFIPS140 build flag in Makefile and Dockerfile."""
    cluster = self.context.cluster
    makefile = os.path.join(_repo_root(), "Makefile")
    dockerfile = os.path.join(_repo_root(), "Dockerfile")

    with When("I check build definitions for GOFIPS140=v1.0.0"):
        result = cluster.command(
            None,
            f"grep -n \"GOFIPS140=v1.0.0\" \"{makefile}\" \"{dockerfile}\"",
            exitcode=0,
        )

    with Then("both Makefile and Dockerfile should contain GOFIPS140 build flag"):
        assert "Makefile:" in result.output, error(result.output)
        assert "Dockerfile:" in result.output, error(result.output)


@TestScenario
def checksum_tamper_panics(self):
    """Test Case 5: tamper go.fipsinfo checksum and verify startup self-test fails."""
    cluster = self.context.cluster
    fips_bin = _resolve_fips_binary()
    script_candidates = [
        os.path.join(_repo_root(), "test/testflows/clickhouse_backup/scripts/tamper_go_fips_checksum.sh"),
        os.path.join(_repo_root(), "scripts/tamper_go_fips_checksum.sh"),
    ]
    tamper_script = next((path for path in script_candidates if os.path.isfile(path)), None)
    if not tamper_script:
        skip("tamper_go_fips_checksum.sh is missing")

    with When("I run the checksum tamper script"):
        result = cluster.command(None, f"bash \"{tamper_script}\" \"{fips_bin}\"", exitcode=0)

    with Then("tampered binary should fail integrity verification"):
        assert "fips140: verification mismatch" in result.output, error(result.output)


@TestScenario
def failfipscast_known_answer_tests(self):
    """Test Case 6: force CAST failures via GODEBUG and verify process fails."""
    cluster = self.context.cluster
    fips_bin = _resolve_fips_binary()
    casts = ("SHA2-256", "TLSv1.2-SHA2-256")

    for cast_name in casts:
        with When(f"I force CAST failure for {cast_name}"):
            result = cluster.command(
                None,
                f"GODEBUG=failfipscast={cast_name},fips140=on \"{fips_bin}\" --version",
                steps=False,
            )

        with Then("startup should fail and indicate FIPS self-test failure"):
            assert result.exitcode != 0, error(result.output)
            out_l = result.output.lower()
            assert "fips" in out_l or "cast" in out_l or "panic" in out_l, error(result.output)


@TestScenario
def inbound_tls_cipher_negotiation(self):
    """Test Case 7: validate inbound TLS accepts only FIPS-compatible ciphers."""
    backup = self.context.backup
    fips_bin = _ensure_fips_binary_in_backup_container()
    cert_dir = "/tmp/fips-api"

    try:
        with Given("openssl is installed in backup container"):
            backup.cmd("apt-get update && apt-get install -y openssl", timeout=300)

        with And("I generate temporary CA and server certificates"):
            backup.cmd(f"mkdir -p {cert_dir}")
            backup.cmd(f"openssl genrsa -out {cert_dir}/ca-key.pem 4096")
            backup.cmd(
                f"openssl req -subj '/O=altinity' -x509 -new -nodes -key {cert_dir}/ca-key.pem -sha256 -days 365000 -out {cert_dir}/ca-cert.pem"
            )
            backup.cmd(f"openssl genrsa -out {cert_dir}/server-key.pem 4096")
            backup.cmd(
                f"openssl req -subj '/CN=localhost' -addext 'subjectAltName = DNS:localhost' -new -key {cert_dir}/server-key.pem -out {cert_dir}/server-req.csr"
            )
            backup.cmd(
                f"openssl x509 -req -days 365 -extensions SAN -extfile <(printf \"\\n[SAN]\\nsubjectAltName=DNS:localhost\") -in {cert_dir}/server-req.csr -out {cert_dir}/server-cert.pem -CA {cert_dir}/ca-cert.pem -CAkey {cert_dir}/ca-key.pem -CAcreateserial"
            )

        with And("I start clickhouse-backup-fips server on TLS endpoint"):
            backup.cmd("pkill -f 'clickhouse-backup-fips server' || true")
            backup.cmd(
                f"API_SECURE=true API_LISTEN=0.0.0.0:7172 API_PRIVATE_KEY_FILE={cert_dir}/server-key.pem API_CERTIFICATE_FILE={cert_dir}/server-cert.pem GODEBUG=fips140=only {fips_bin} server >/tmp/fips-api/server.log 2>&1 &"
            )
            backup.cmd(
                "ready=1; for i in $(seq 1 30); do if timeout 1 bash -c '</dev/tcp/localhost/7172'; then ready=0; break; fi; sleep 1; done; test ${ready} -eq 0",
                timeout=40,
            )

        with When("I connect with a FIPS-compatible TLSv1.3 ciphersuite"):
            allowed = backup.cmd(
                f"openssl s_client -connect localhost:7172 -brief -tls1_3 -ciphersuites TLS_AES_128_GCM_SHA256 -CAfile {cert_dir}/ca-cert.pem < /dev/null",
                no_checks=True,
            )

        with Then("the handshake should succeed"):
            assert allowed.exitcode == 0, error(allowed.output)
            assert "TLS_AES_128_GCM_SHA256" in allowed.output, error(allowed.output)

        with When("I connect with a non-FIPS TLSv1.3 ciphersuite"):
            denied = backup.cmd(
                f"openssl s_client -connect localhost:7172 -brief -tls1_3 -ciphersuites TLS_CHACHA20_POLY1305_SHA256 -CAfile {cert_dir}/ca-cert.pem < /dev/null",
                no_checks=True,
            )

        with Then("the handshake should be rejected during negotiation"):
            denied_out = denied.output.lower()
            assert denied.exitcode != 0 or "handshake failure" in denied_out or "no shared cipher" in denied_out or "alert" in denied_out, error(denied.output)
    finally:
        with Finally("I stop temporary clickhouse-backup-fips server"):
            backup.cmd("pkill -f 'clickhouse-backup-fips server' || true")


@TestScenario
def outbound_tls_cipher_negotiation(self):
    """Test Case 8: validate outbound TLS rejects non-FIPS ciphers."""
    backup = self.context.backup
    fips_bin = _ensure_fips_binary_in_backup_container()
    cert_dir = "/tmp/fips-api"
    config_path = self.context.backup_config_file

    with Given("I save current backup config"):
        with open(config_path, "r", encoding="utf-8") as f:
            original_config = yaml.safe_load(f)

    try:
        with And("openssl is installed in backup container"):
            backup.cmd("apt-get update && apt-get install -y openssl", timeout=300)
        with And("I disable bash job notifications for stable command parsing"):
            backup.cmd("set +m", no_checks=True)

        with And("I generate temporary TLS certs for outbound target server"):
            backup.cmd(f"mkdir -p {cert_dir}")
            backup.cmd(f"openssl genrsa -out {cert_dir}/ca-key.pem 4096")
            backup.cmd(
                f"openssl req -subj '/O=altinity' -x509 -new -nodes -key {cert_dir}/ca-key.pem -sha256 -days 365000 -out {cert_dir}/ca-cert.pem"
            )
            backup.cmd(f"openssl genrsa -out {cert_dir}/server-key.pem 4096")
            backup.cmd(
                f"openssl req -subj '/CN=localhost' -addext 'subjectAltName = DNS:localhost' -new -key {cert_dir}/server-key.pem -out {cert_dir}/server-req.csr"
            )
            backup.cmd(
                f"openssl x509 -req -days 365 -extensions SAN -extfile <(printf \"\\n[SAN]\\nsubjectAltName=DNS:localhost\") -in {cert_dir}/server-req.csr -out {cert_dir}/server-cert.pem -CA {cert_dir}/ca-cert.pem -CAkey {cert_dir}/ca-key.pem -CAcreateserial"
            )

        with And("I configure clickhouse-backup to use HTTPS S3 endpoint on localhost"):
            config_modifier(
                fields={
                    "general": {"remote_storage": "s3"},
                    "s3": {
                        "access_key": "access_key",
                        "secret_key": "it_is_my_super_secret_key",
                        "bucket": "clickhouse",
                        "region": "us-east-1",
                        "endpoint": "https://localhost:9443",
                        "force_path_style": True,
                        "disable_ssl": False,
                    },
                }
            )

        with When("remote TLS server allows only a FIPS-compatible TLSv1.3 ciphersuite"):
            backup.cmd("pkill -f 'openssl s_server -accept 9443' >/dev/null 2>&1 || true")
            backup.cmd(
                f"nohup openssl s_server -accept 9443 -cert {cert_dir}/server-cert.pem -key {cert_dir}/server-key.pem -tls1_3 -ciphersuites TLS_AES_128_GCM_SHA256 -quiet -naccept 1 > {cert_dir}/s_server_allowed.log 2>&1 </dev/null &"
            )
            allowed = backup.cmd(
                f"GODEBUG=fips140=only {fips_bin} -c /etc/clickhouse-backup/config.yml list remote",
                no_checks=True,
                timeout=40,
            )
            allowed_log = backup.cmd(f"cat {cert_dir}/s_server_allowed.log || true", no_checks=True).output

        with Then("outbound handshake should not fail due to TLS cipher negotiation"):
            allowed_out = (allowed.output or "").lower()
            if "custom endpoint cannot be combined with fips" in allowed_out:
                skip(
                    "AWS SDK endpoint rules reject custom S3 endpoint when FIPS is enabled; "
                    "cannot validate outbound cipher negotiation against local openssl s_server in this mode"
                )
            assert "handshake failure" not in allowed_out and "no shared cipher" not in allowed_out, error(allowed.output)
            assert "alert handshake failure" not in allowed_log.lower(), error(allowed_log)

        with When("remote TLS server allows only a non-FIPS TLSv1.3 ciphersuite"):
            backup.cmd("pkill -f 'openssl s_server -accept 9443' >/dev/null 2>&1 || true")
            backup.cmd(
                f"nohup openssl s_server -accept 9443 -cert {cert_dir}/server-cert.pem -key {cert_dir}/server-key.pem -tls1_3 -ciphersuites TLS_CHACHA20_POLY1305_SHA256 -quiet -naccept 1 > {cert_dir}/s_server_denied.log 2>&1 </dev/null &"
            )
            denied = backup.cmd(
                f"GODEBUG=fips140=only {fips_bin} -c /etc/clickhouse-backup/config.yml list remote",
                no_checks=True,
                timeout=40,
            )
            denied_log = backup.cmd(f"cat {cert_dir}/s_server_denied.log || true", no_checks=True).output

        with Then("outbound handshake should be rejected during negotiation"):
            denied_out = (denied.output or "").lower()
            denied_srv = (denied_log or "").lower()
            if "custom endpoint cannot be combined with fips" in denied_out:
                skip(
                    "AWS SDK endpoint rules reject custom S3 endpoint when FIPS is enabled; "
                    "cannot validate outbound cipher negotiation against local openssl s_server in this mode"
                )
            assert (
                "handshake failure" in denied_out
                or "no shared cipher" in denied_out
                or "tls:" in denied_out
                or "alert handshake failure" in denied_srv
                or "no shared cipher" in denied_srv
            ), error(f"client_out={denied.output}\nserver_log={denied_log}")
    finally:
        with Finally("I restore backup config"):
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(original_config, f, default_flow_style=False)
        with And("I stop temporary outbound TLS server"):
            backup.cmd("pkill -f 'openssl s_server -accept 9443' >/dev/null 2>&1 || true")


@TestScenario
def readelf_go_fipsinfo_offset(self):
    """Support check (non-numbered): validate readelf can find .go.fipsinfo section."""
    cluster = self.context.cluster
    fips_bin = _resolve_fips_binary()

    with When("I inspect ELF sections using the same readelf command used in procedures"):
        relf = cluster.command(None, f"readelf -S -W \"{fips_bin}\"", exitcode=0)

    with Then("I should see the .go.fipsinfo section and parse its file offset"):
        assert ".go.fipsinfo" in relf.output, error(relf.output)
        offset_hex = None
        for line in relf.output.splitlines():
            if ".go.fipsinfo" not in line:
                continue
            parts = line.split()
            for i, token in enumerate(parts):
                if token == ".go.fipsinfo" and i + 3 < len(parts):
                    offset_hex = parts[i + 3]
                    break
            if offset_hex:
                break
        assert offset_hex is not None, error("unable to parse .go.fipsinfo file offset from readelf output")
        assert int(offset_hex, 16) > 0, error(f"unexpected .go.fipsinfo file offset: {offset_hex}")


@TestScenario
def acvp_wrapper(self):
    """Support check (optional): run ACVP wrapper when explicitly enabled."""
    cluster = self.context.cluster
    if os.environ.get("RUN_ACVP_TESTS") != "1":
        skip("RUN_ACVP_TESTS is not enabled")

    run_sh = os.path.join(_repo_root(), "pkg/acvpwrapper/run.sh")
    if not os.path.isfile(run_sh):
        skip("pkg/acvpwrapper/run.sh is missing in this branch")

    with When("I run ACVP wrapper against the binary"):
        result = cluster.command(None, f"bash \"{run_sh}\"", timeout=1800, exitcode=0)

    with Then("the ACVP run should complete successfully"):
        out_l = result.output.lower()
        assert "pass" in out_l or "success" in out_l, error(result.output)


@TestFeature
def fips(self):
    """FIPS compliance tests for clickhouse-backup in TestFlows."""
    # Keep execution order aligned with scenario-level checks in Test_Plan_FIPS.md:
    # TC1 -> TC2 -> TC3 -> TC4 -> TC5 -> TC6 -> TC7 -> TC8.
    # Manual workflows 1a/2a are intentionally not automated here.

    plan_order_scenarios = [
        fips_binary_connectivity_fips_clickhouse,
        fips_binary_connectivity_nonfips_clickhouse,
        fips_only_mode_version,
        gofips140_build_flags_present,
        checksum_tamper_panics,
        failfipscast_known_answer_tests,
        inbound_tls_cipher_negotiation,
        outbound_tls_cipher_negotiation,
    ]
    for scenario in plan_order_scenarios:
        Scenario(run=scenario)

    # Additional/supporting scenarios not mapped to numbered test cases.
    optional_additional = [
        readelf_go_fipsinfo_offset,
        acvp_wrapper,
    ]
    for scenario in optional_additional:
        Scenario(run=scenario)
