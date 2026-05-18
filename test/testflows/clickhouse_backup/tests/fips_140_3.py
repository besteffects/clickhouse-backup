import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager

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


def run_connectivity_tables_smoke(clickhouse_version, mode_name, mode_value):
    """Run `tables` smoke check in a temporary cluster."""
    if not command_exists("docker"):
        skip("docker is not available in PATH")

    with temporary_backup_config_dir() as config_dir:
        cluster_env = {
            "CLICKHOUSE_IMAGE": "altinity/clickhouse-server",
            "CLICKHOUSE_VERSION": clickhouse_version,
            "CLICKHOUSE_TESTS_DIR": tests_root_dir(),
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
            env_prefix = f"GODEBUG={mode_value} " if mode_value else ""
            tables = backup.cmd(
                f"{env_prefix}clickhouse-backup -c /etc/clickhouse-backup/config.yml tables",
                no_checks=True,
            )
            assert tables.exitcode == 0, error(
                f"mode={mode_name}, clickhouse_version={clickhouse_version}\n{tables.output}"
            )


@TestStep
def check_clickhouse_tables_connectivity(self, clickhouse_kind, clickhouse_version, mode_name, mode_value):
    """Run connectivity smoke against a selected ClickHouse target."""
    with By(f"targeting {clickhouse_kind} ClickHouse {clickhouse_version} in mode {mode_name}"):
        run_connectivity_tables_smoke(
            clickhouse_version=clickhouse_version,
            mode_name=mode_name,
            mode_value=mode_value,
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


if main():
    fips_ssl_140_3()
