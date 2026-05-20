import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager

import yaml
from testflows.asserts import error
from testflows.core import *

append_path(sys.path, os.path.normpath(os.path.join(os.path.dirname(__file__), "../..")))
from helpers.cluster import Cluster

FIPS_VERSION_LABEL = "FIPS 140-3:"
FIPS_VERSION_TRUE = "true"
RUNTIME_MODES = [("unset", None), ("on", "fips140=on"), ("only", "fips140=only")]
GOFIPS_REQUIRED_FLAG = "GOFIPS140=v1.0.0"
MAKEFILE_REQUIRED_CHECKS = ["build-fips:", "build-race-fips:", "$(GO_BUILD)", "$(GO_BUILD_STATIC)"]
DOCKERFILE_REQUIRED_CHECKS = [
    "clickhouse-backup-race-fips",
    "FROM image_short AS image_fips",
    "COPY build/${TARGETPLATFORM}/clickhouse-backup-fips /bin/clickhouse-backup",
]
FIPS_TLS13_ALLOWED_CIPHERSUITES = [
    "TLS_AES_128_GCM_SHA256",
    "TLS_AES_256_GCM_SHA384",
]
NON_FIPS_TLS13_CIPHERSUITES = [
    "TLS_CHACHA20_POLY1305_SHA256",
]
NON_FIPS_TLS12_CIPHERLIST = [
    "ECDHE-RSA-CHACHA20-POLY1305",
    "DHE-RSA-AES128-GCM-SHA256",
    "DHE-RSA-AES256-GCM-SHA384",
    "DHE-RSA-CHACHA20-POLY1305",
]
NON_FIPS_CLICKHOUSE_PROFILE = {
    "port": 9000,
    "secure": False,
    "skip_verify": False,
}
FIPS_CLICKHOUSE_PROFILE = {
    "port": 9440,
    "secure": True,
    "skip_verify": True,
}
CLICKHOUSE_FIPS_PORTS_XML = """<?xml version="1.0"?>
<yandex>
  <https_port>8443</https_port>
  <tcp_port_secure>9440</tcp_port_secure>
  <openSSL>
    <server>
      <certificateFile>/etc/clickhouse-server/ssl/server.crt</certificateFile>
      <privateKeyFile>/etc/clickhouse-server/ssl/server.key</privateKeyFile>
      <dhParamsFile>/etc/clickhouse-server/ssl/dhparam.pem</dhParamsFile>
      <verificationMode>none</verificationMode>
    </server>
  </openSSL>
</yandex>
"""


def repo_root():
    """Resolve repository root by locating Makefile and Dockerfile.

    The function does not rely on a single hard-coded relative path.
    It tries known anchors and walks up parent directories.
    """
    anchors = []
    tests_dir = os.environ.get("CLICKHOUSE_TESTS_DIR")
    if tests_dir:
        anchors.append(os.path.abspath(tests_dir))
    anchors.append(os.path.abspath(os.path.dirname(__file__)))
    anchors.append(os.path.abspath(os.getcwd()))

    visited = set()
    for anchor in anchors:
        current = anchor
        while True:
            if current in visited:
                break
            visited.add(current)

            makefile = os.path.join(current, "Makefile")
            dockerfile = os.path.join(current, "Dockerfile")
            if os.path.isfile(makefile) and os.path.isfile(dockerfile):
                return current

            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

    fail("unable to resolve repository root with Makefile and Dockerfile")


def read_required_file(path):
    """Read file content or fail with a clear assertion message."""
    assert os.path.isfile(path), error(f"required file does not exist: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def assert_contains(content, needle, where):
    """Assert file contains a required substring."""
    assert needle in content, error(f"missing required text in {where}: {needle}")


def resolve_fips_binary():
    """Resolve host path to FIPS binary from env or known locations."""
    root = repo_root()
    candidates = []
    env_bin = os.environ.get("CLICKHOUSE_BACKUP_FIPS_BINARY")
    if env_bin:
        candidates.append(env_bin)
    candidates.extend(
        [
            os.path.join(root, "clickhouse-backup", "clickhouse-backup-race-fips"),
            os.path.join(root, "build", "linux", "amd64", "clickhouse-backup-fips"),
        ]
    )
    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    fail(
        "unable to find FIPS binary; set CLICKHOUSE_BACKUP_FIPS_BINARY or provide "
        "clickhouse-backup/clickhouse-backup-race-fips"
    )


def command_exists(name):
    """Return True if command is available in PATH."""
    return shutil.which(name) is not None


def build_runtime_env(mode_value, with_gofips=False):
    """Build process environment for a given FIPS runtime mode."""
    env = os.environ.copy()
    env.pop("GODEBUG", None)
    if with_gofips:
        env["GOFIPS140"] = "v1.0.0"
    if mode_value:
        env["GODEBUG"] = mode_value
    return env


def resolve_fips_binary_step():
    """Locate FIPS binary for version/runtime checks."""
    return resolve_fips_binary()


@TestStep(Check)
def check_fips_version_for_mode(self, fips_bin, mode_name, mode_value):
    """Validate `--version` output for one runtime mode."""
    result = subprocess.run(
        [fips_bin, "--version"],
        env=build_runtime_env(mode_value),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, error(result.stderr or result.stdout)
    assert FIPS_VERSION_LABEL in result.stdout, error(result.stdout)
    assert FIPS_VERSION_TRUE in result.stdout.lower(), error(result.stdout)


def tests_root_dir():
    """Return absolute path to test/testflows/clickhouse_backup."""
    return os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))


def backup_tests_nodes():
    """Node set compatible with existing backup regression harness."""
    return {
        "clickhouse": ("clickhouse1", "clickhouse2"),
        "clickhouse_backup": ("clickhouse_backup",),
        "kafka": ("kafka",),
        "mysql": ("mysql",),
        "postgres": ("postgres",),
    }


@contextmanager
def temporary_backup_config_dir():
    """Create isolated backup config dir copied from baseline."""
    base = os.path.join(tests_root_dir(), "configs", "backup")
    temp_dir = tempfile.mkdtemp(prefix="fips-backup-config-")
    try:
        shutil.copytree(base, temp_dir, dirs_exist_ok=True)
        yield temp_dir
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@contextmanager
def temporary_clickhouse_fips_config(enable_fips_ports):
    """Create temporary fips.xml and return its path when enabled."""
    if not enable_fips_ports:
        yield None
        return

    temp_dir = tempfile.mkdtemp(prefix="fips-clickhouse-config-")
    try:
        fips_config_path = os.path.join(temp_dir, "fips.xml")
        with open(fips_config_path, "w", encoding="utf-8") as f:
            f.write(CLICKHOUSE_FIPS_PORTS_XML)
        yield fips_config_path
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def patch_backup_clickhouse_config(config_path, profile):
    """Patch clickhouse client connection settings in backup config."""
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    clickhouse_config = config.setdefault("clickhouse", {})
    clickhouse_config["port"] = profile["port"]
    clickhouse_config["secure"] = profile["secure"]
    clickhouse_config["skip_verify"] = profile["skip_verify"]

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)


@TestStep(Check)
def check_backup_clickhouse_profile_in_container(self, backup, profile):
    """Assert patched ClickHouse settings are visible inside backup container."""
    secure_text = str(profile["secure"]).lower()
    skip_verify_text = str(profile["skip_verify"]).lower()
    backup.cmd(
        f"grep -E '^  port: {profile['port']}$' /etc/clickhouse-backup/config.yml",
        exitcode=0,
    )
    backup.cmd(
        f"grep -E '^  secure: {secure_text}$' /etc/clickhouse-backup/config.yml",
        exitcode=0,
    )
    backup.cmd(
        f"grep -E '^  skip_verify: {skip_verify_text}$' /etc/clickhouse-backup/config.yml",
        exitcode=0,
    )


@TestStep(Check)
def check_clickhouse_secure_ports_listening(self, cluster):
    """Ensure ClickHouse FIPS config is applied in the target node."""
    node = cluster.node("clickhouse1")
    node.cmd("test -f /etc/clickhouse-server/config.d/fips.xml", exitcode=0)
    node.cmd("grep -E '<https_port>8443</https_port>' /etc/clickhouse-server/config.d/fips.xml", exitcode=0)
    node.cmd("grep -E '<tcp_port_secure>9440</tcp_port_secure>' /etc/clickhouse-server/config.d/fips.xml", exitcode=0)


def run_connectivity_tables_smoke(
    clickhouse_version, mode_name, mode_value, clickhouse_profile, enable_fips_ports=False
):
    """Run `tables` smoke check in a temporary cluster."""
    if not command_exists("docker"):
        skip("docker is not available in PATH")

    with temporary_backup_config_dir() as config_dir:
        config_path = os.path.join(config_dir, "config.yml")
        patch_backup_clickhouse_config(config_path=config_path, profile=clickhouse_profile)

        with temporary_clickhouse_fips_config(enable_fips_ports=enable_fips_ports) as fips_config_path:
            cluster_env = {
                "CLICKHOUSE_IMAGE": "altinity/clickhouse-server",
                "CLICKHOUSE_VERSION": clickhouse_version,
                "CLICKHOUSE_TESTS_DIR": tests_root_dir(),
                "CLICKHOUSE_BACKUP_AUTOSTART_SERVER": "0",
            }
            if fips_config_path:
                cluster_env["CLICKHOUSE_EXTRA_CONFIG_PATH"] = fips_config_path

            with Cluster(
                local=False,
                configs_dir=tests_root_dir(),
                docker_dir=os.path.join(tests_root_dir(), "docker"),
                nodes=backup_tests_nodes(),
                backup_config_dir=config_dir,
                environ=cluster_env,
            ) as cluster:
                backup = cluster.node("clickhouse_backup")
                check_backup_clickhouse_profile_in_container(
                    backup=backup, profile=clickhouse_profile
                )
                if enable_fips_ports:
                    check_clickhouse_secure_ports_listening(cluster=cluster)

                env_prefix = f"GODEBUG={mode_value} " if mode_value else ""
                tables = backup.cmd(
                    f"{env_prefix}clickhouse-backup -c /etc/clickhouse-backup/config.yml tables",
                    no_checks=True,
                )
                assert tables.exitcode == 0, error(
                    f"mode={mode_name}, clickhouse_version={clickhouse_version}\n{tables.output}"
                )


def copy_fips_binary_to_backup_container(cluster, backup):
    """Copy resolved host FIPS binary into backup container."""
    fips_bin = resolve_fips_binary_step()
    container_id = cluster.get_container_id("clickhouse_backup")
    cluster.command(None, f"docker cp \"{fips_bin}\" {container_id}:/bin/clickhouse-backup-fips", exitcode=0)
    backup.cmd("chmod +x /bin/clickhouse-backup-fips")
    return "/bin/clickhouse-backup-fips"


@TestStep(Given)
def setup_inbound_tls_server(self, cluster, backup):
    """Prepare and start clickhouse-backup FIPS API TLS server on :7172."""
    cert_dir = "/tmp/chb-fips-tls"
    fips_bin_container = copy_fips_binary_to_backup_container(cluster=cluster, backup=backup)

    with By("generating local CA and server certificate on host"):
        with tempfile.TemporaryDirectory(prefix="chb-fips-host-tls-") as host_tls_dir:
            ca_key = os.path.join(host_tls_dir, "ca-key.pem")
            ca_cert = os.path.join(host_tls_dir, "ca-cert.pem")
            server_key = os.path.join(host_tls_dir, "server-key.pem")
            server_csr = os.path.join(host_tls_dir, "server.csr")
            server_ext = os.path.join(host_tls_dir, "server-ext.cnf")
            server_cert = os.path.join(host_tls_dir, "server-cert.pem")

            subprocess.run(["openssl", "genrsa", "-out", ca_key, "2048"], check=True)
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-new",
                    "-sha256",
                    "-days",
                    "1",
                    "-key",
                    ca_key,
                    "-out",
                    ca_cert,
                    "-subj",
                    "/CN=chb-fips-test-ca",
                ],
                check=True,
            )
            subprocess.run(["openssl", "genrsa", "-out", server_key, "2048"], check=True)
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-new",
                    "-key",
                    server_key,
                    "-out",
                    server_csr,
                    "-subj",
                    "/CN=localhost",
                ],
                check=True,
            )
            with open(server_ext, "w", encoding="utf-8") as f:
                f.write(
                    "subjectAltName=DNS:localhost,IP:127.0.0.1\n"
                    "keyUsage=critical,digitalSignature,keyEncipherment\n"
                    "extendedKeyUsage=serverAuth\n"
                    "authorityKeyIdentifier=keyid,issuer\n"
                )
            subprocess.run(
                [
                    "openssl",
                    "x509",
                    "-req",
                    "-sha256",
                    "-days",
                    "1",
                    "-in",
                    server_csr,
                    "-CA",
                    ca_cert,
                    "-CAkey",
                    ca_key,
                    "-CAcreateserial",
                    "-out",
                    server_cert,
                    "-extfile",
                    server_ext,
                ],
                check=True,
            )

            backup_container_id = cluster.get_container_id("clickhouse_backup")
            clickhouse1_container_id = cluster.get_container_id("clickhouse1")
            cluster.command(None, f"docker exec {backup_container_id} bash -lc 'mkdir -p {cert_dir}'", exitcode=0)
            cluster.command(None, f"docker exec {clickhouse1_container_id} bash -lc 'mkdir -p {cert_dir}'", exitcode=0)
            cluster.command(None, f"docker cp \"{server_key}\" {backup_container_id}:{cert_dir}/server-key.pem", exitcode=0)
            cluster.command(None, f"docker cp \"{server_cert}\" {backup_container_id}:{cert_dir}/server-cert.pem", exitcode=0)
            cluster.command(None, f"docker cp \"{ca_cert}\" {clickhouse1_container_id}:{cert_dir}/ca-cert.pem", exitcode=0)

    with By("starting API TLS server in strict FIPS mode"):
        backup.cmd("pkill -f 'clickhouse-backup-fips server' >/dev/null 2>&1 || true")
        backup.cmd(
            f"API_SECURE=true API_LISTEN=0.0.0.0:7172 "
            f"API_PRIVATE_KEY_FILE={cert_dir}/server-key.pem "
            f"API_CERTIFICATE_FILE={cert_dir}/server-cert.pem "
            f"GODEBUG=fips140=only {fips_bin_container} -c /etc/clickhouse-backup/config.yml server "
            f">{cert_dir}/server.log 2>&1 &"
        )
        backup.cmd(
            "ready=1; for i in $(seq 1 30); do "
            "if timeout 1 bash -c '</dev/tcp/localhost/7172'; then ready=0; break; fi; "
            "sleep 1; done; test ${ready} -eq 0",
            timeout=40,
        )

    return cert_dir


@TestStep(Check)
def check_inbound_tls13_cipher(self, client, cert_dir, ciphersuite, expected_success):
    """Probe API TLS listener with chosen TLSv1.3 ciphersuite."""
    out = client.cmd(
        f"openssl s_client -connect backup:7172 -brief -tls1_3 "
        f"-ciphersuites {ciphersuite} -CAfile {cert_dir}/ca-cert.pem < /dev/null",
        no_checks=True,
    )
    output_lower = (out.output or "").lower()

    if expected_success:
        assert out.exitcode == 0, error(out.output)
        assert "handshake failure" not in output_lower, error(out.output)
        assert "no shared cipher" not in output_lower, error(out.output)
    else:
        assert (
            out.exitcode != 0
            or "handshake failure" in output_lower
            or "no shared cipher" in output_lower
            or "alert" in output_lower
        ), error(out.output)


@TestStep(Check)
def check_inbound_tls12_cipher(self, client, cert_dir, cipher, expected_success):
    """Probe API TLS listener with chosen TLSv1.2 cipher."""
    out = client.cmd(
        f"openssl s_client -connect backup:7172 -brief -tls1_2 "
        f"-cipher {cipher} -CAfile {cert_dir}/ca-cert.pem < /dev/null",
        no_checks=True,
    )
    output_lower = (out.output or "").lower()

    if expected_success:
        assert out.exitcode == 0, error(out.output)
        assert "handshake failure" not in output_lower, error(out.output)
        assert "no shared cipher" not in output_lower, error(out.output)
    else:
        assert (
            out.exitcode != 0
            or "handshake failure" in output_lower
            or "no shared cipher" in output_lower
            or "alert" in output_lower
        ), error(out.output)


@TestStep
def check_clickhouse_tables_connectivity(
    self,
    clickhouse_kind,
    clickhouse_version,
    mode_name,
    mode_value,
    clickhouse_profile,
    enable_fips_ports=False,
):
    """Run connectivity smoke against a selected ClickHouse target."""
    with By(f"targeting {clickhouse_kind} ClickHouse {clickhouse_version} in mode {mode_name}"):
        run_connectivity_tables_smoke(
            clickhouse_version=clickhouse_version,
            mode_name=mode_name,
            mode_value=mode_value,
            clickhouse_profile=clickhouse_profile,
            enable_fips_ports=enable_fips_ports,
        )


def load_fips_build_definitions():
    """Load Makefile and Dockerfile content for TC4 checks."""
    root = repo_root()
    makefile_path = os.path.join(root, "Makefile")
    dockerfile_path = os.path.join(root, "Dockerfile")
    makefile_content = read_required_file(makefile_path)
    dockerfile_content = read_required_file(dockerfile_path)
    return {
        "makefile_content": makefile_content,
        "dockerfile_content": dockerfile_content,
        "makefile_lines": makefile_content.splitlines(),
        "dockerfile_lines": dockerfile_content.splitlines(),
    }


@TestStep(When)
def check_makefile_fips_build_flags(self, makefile_content, makefile_lines):
    """Validate Makefile contains required FIPS build flags and targets."""
    for makefile_required_text in MAKEFILE_REQUIRED_CHECKS:
        assert_contains(makefile_content, makefile_required_text, "Makefile")
    assert any(
        GOFIPS_REQUIRED_FLAG in line and "$(GO_BUILD)" in line for line in makefile_lines
    ), error("missing GOFIPS140 regular FIPS build command in Makefile")
    assert any(
        GOFIPS_REQUIRED_FLAG in line and "$(GO_BUILD_STATIC)" in line and "-race" in line
        for line in makefile_lines
    ), error("missing GOFIPS140 build-race-fips command in Makefile")


@TestStep(And)
def check_dockerfile_fips_build_flags(self, dockerfile_content, dockerfile_lines):
    """Validate Dockerfile contains required FIPS image build path and flags."""
    for dockerfile_required_text in DOCKERFILE_REQUIRED_CHECKS:
        assert_contains(dockerfile_content, dockerfile_required_text, "Dockerfile")
    assert any(
        GOFIPS_REQUIRED_FLAG in line and "go build" in line and "clickhouse-backup-race-fips" in line
        for line in dockerfile_lines
    ), error("missing GOFIPS140 race-fips go build command in Dockerfile")


@TestScenario
def checksum_tamper_panics(self):
    """TC5: tamper checksum and verify integrity self-check fails."""
    root = repo_root()
    fips_bin = resolve_fips_binary_step()
    tamper_script = os.path.join(
        root, "test/testflows/clickhouse_backup/scripts/tamper_go_fips_checksum.sh"
    )

    with Given("I locate checksum tamper script used by the plan"):
        assert os.path.isfile(tamper_script), error(f"missing script: {tamper_script}")

    with When("I run checksum tamper script against the FIPS binary"):
        run = subprocess.run(
            ["bash", tamper_script, fips_bin],
            capture_output=True,
            text=True,
            env=build_runtime_env(None),
        )
        combined_output = (run.stdout or "") + ("\n" + run.stderr if run.stderr else "")

    with Then("the script should report expected integrity verification failure"):
        assert run.returncode == 0, error(combined_output)
        assert "fips140: verification mismatch" in combined_output, error(combined_output)
        assert "OK: FIPS integrity check failed as expected" in combined_output, error(combined_output)


@TestScenario
def inbound_tls_cipher_negotiation(self):
    """TC7: validate inbound TLS policy of REST API with openssl s_client."""
    if not command_exists("docker"):
        skip("docker is not available in PATH")

    with temporary_backup_config_dir() as config_dir:
        cluster_env = {
            "CLICKHOUSE_IMAGE": "altinity/clickhouse-server",
            "CLICKHOUSE_VERSION": "25.8.16.10002.altinitystable",
            "CLICKHOUSE_TESTS_DIR": tests_root_dir(),
            "CLICKHOUSE_BACKUP_AUTOSTART_SERVER": "0",
        }
        with Cluster(
            local=False,
            configs_dir=tests_root_dir(),
            docker_dir=os.path.join(tests_root_dir(), "docker"),
            nodes=backup_tests_nodes(),
            backup_config_dir=config_dir,
            environ=cluster_env,
        ) as cluster:
            backup = cluster.node("clickhouse_backup")
            clickhouse_client = cluster.node("clickhouse1")
            cert_dir = None
            try:
                with Given("I prepare clickhouse-backup-fips API TLS server endpoint"):
                    cert_dir = setup_inbound_tls_server(cluster=cluster, backup=backup)

                with When("I probe API with allowed TLSv1.3 ciphersuite"):
                    for ciphersuite in FIPS_TLS13_ALLOWED_CIPHERSUITES:
                        with Check(f"TLSv1.3 cipher {ciphersuite} should be accepted"):
                            check_inbound_tls13_cipher(
                                client=clickhouse_client,
                                cert_dir=cert_dir,
                                ciphersuite=ciphersuite,
                                expected_success=True,
                            )

                with And("I probe API with non-FIPS TLSv1.3 ciphersuite"):
                    for ciphersuite in NON_FIPS_TLS13_CIPHERSUITES:
                        with Check(f"TLSv1.3 cipher {ciphersuite} should be rejected"):
                            check_inbound_tls13_cipher(
                                client=clickhouse_client,
                                cert_dir=cert_dir,
                                ciphersuite=ciphersuite,
                                expected_success=False,
                            )

                with And("I probe API with non-FIPS TLSv1.2 ciphers from cipherList"):
                    for cipher in NON_FIPS_TLS12_CIPHERLIST:
                        with Check(f"TLSv1.2 cipher {cipher} should be rejected"):
                            check_inbound_tls12_cipher(
                                client=clickhouse_client,
                                cert_dir=cert_dir,
                                cipher=cipher,
                                expected_success=False,
                            )
            finally:
                with Finally("I stop temporary API TLS server"):
                    container_id = cluster.get_container_id("clickhouse_backup")
                    cluster.command(
                        None,
                        f"docker exec {container_id} bash -lc \"pkill -f 'clickhouse-backup-fips server' >/dev/null 2>&1 || true\"",
                    )


@TestScenario
def fips_version_output(self):
    """TC3 `--version` output check."""
    fips_bin = resolve_fips_binary_step()

    with When("I run clickhouse-backup-race-fips --version with default runtime"):
        check_fips_version_for_mode(fips_bin=fips_bin, mode_name="default", mode_value=None)


@TestScenario
def fips_runtime_mode_matrix(self):
    """TC3 `GODEBUG` runtime matrix (`unset`/`on`/`only`)."""
    fips_bin = resolve_fips_binary_step()

    with Given("I prepare runtime mode checks for GODEBUG values"):
        pass

    with When("I run clickhouse-backup-race-fips --version in each GODEBUG mode"):
        for mode_name, mode_value in RUNTIME_MODES:
            check_fips_version_for_mode(fips_bin=fips_bin, mode_name=mode_name, mode_value=mode_value)

    with And("I validate observable Enabled/Enforced runtime state"):
        if not command_exists("go"):
            note("Skipping Enabled/Enforced probe: `go` command not found in PATH")
            return

        with tempfile.TemporaryDirectory(prefix="fips-runtime-matrix-") as temp_dir:
            probe_file = os.path.join(temp_dir, "probe.go")
            with open(probe_file, "w", encoding="utf-8") as f:
                f.write(
                    'package main\n'
                    'import (\n'
                    '  "fmt"\n'
                    '  "crypto/fips140"\n'
                    ')\n'
                    'func main() {\n'
                    '  fmt.Printf("enabled=%v enforced=%v\\n", fips140.Enabled(), fips140.Enforced())\n'
                    '}\n'
                )

            expected = {
                "unset": "enabled=true enforced=false",
                "on": "enabled=true enforced=false",
                "only": "enabled=true enforced=true",
            }

            for mode_name, mode_value in RUNTIME_MODES:
                with Check(f"mode {mode_name} exposes expected Enabled/Enforced"):
                    out = subprocess.run(
                        ["go", "run", probe_file],
                        env=build_runtime_env(mode_value, with_gofips=True),
                        capture_output=True,
                        text=True,
                    )
                    assert out.returncode == 0, error(out.stderr or out.stdout)
                    assert expected[mode_name] in out.stdout.strip(), error(
                        f"mode={mode_name}, got={out.stdout.strip()}"
                    )


@TestScenario
def fips_binary_connectivity_nonfips_clickhouse(self):
    """TC2b smoke: non-FIPS ClickHouse with `GODEBUG=fips140=on`."""
    with When("I run clickhouse-backup tables against non-fips ClickHouse"):
        check_clickhouse_tables_connectivity(
            clickhouse_kind="non-fips",
            clickhouse_version="25.8.16.10002.altinitystable",
            mode_name="on",
            mode_value="fips140=on",
            clickhouse_profile=NON_FIPS_CLICKHOUSE_PROFILE,
        )


@TestScenario
def fips_binary_connectivity_fips_clickhouse(self):
    """TC1b subset smoke: FIPS ClickHouse with `GODEBUG=fips140=only`."""
    with When("I run clickhouse-backup tables against fips ClickHouse"):
        check_clickhouse_tables_connectivity(
            clickhouse_kind="fips",
            clickhouse_version="25.3.8.30001.altinityfips",
            mode_name="only",
            mode_value="fips140=only",
            clickhouse_profile=FIPS_CLICKHOUSE_PROFILE,
            enable_fips_ports=True,
        )


@TestScenario
def gofips140_build_flags_present(self):
    """TC4 GOFIPS140 build flag checks"""
    definitions = load_fips_build_definitions()
    check_makefile_fips_build_flags(
        makefile_content=definitions["makefile_content"],
        makefile_lines=definitions["makefile_lines"],
    )
    check_dockerfile_fips_build_flags(
        dockerfile_content=definitions["dockerfile_content"],
        dockerfile_lines=definitions["dockerfile_lines"],
    )

    with Then("FIPS build definitions should be explicitly pinned and present"):
        note(
            "TC4 passed: Makefile and Dockerfile contain explicit GOFIPS140=v1.0.0 "
            "in FIPS artifact build paths."
        )


@TestFeature
@Name("FIPS SSL 140-3")
def fips_ssl_140_3(self):
    """FIPS 140-3 automation entrypoint for clickhouse-backup."""
    Scenario(run=gofips140_build_flags_present, flags=TE)
    Scenario(run=fips_version_output, flags=TE)
    Scenario(run=fips_runtime_mode_matrix, flags=TE)
    Scenario(run=fips_binary_connectivity_nonfips_clickhouse, flags=TE)
    Scenario(run=fips_binary_connectivity_fips_clickhouse, flags=TE)
    Scenario(run=checksum_tamper_panics, flags=TE)
    Scenario(run=inbound_tls_cipher_negotiation, flags=TE)


if main():
    fips_ssl_140_3()
