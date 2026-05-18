import os
import shutil
import subprocess
import tempfile

from testflows.asserts import error
from testflows.core import *


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


def run_fips_version_check(fips_bin, mode_name, mode_value, required_version_marker):
    """Run binary --version for one runtime mode and validate output."""
    with Check(f"mode {mode_name} reports FIPS-enabled version"):
        result = subprocess.run(
            [fips_bin, "--version"],
            env=build_runtime_env(mode_value),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, error(result.stderr or result.stdout)
        assert required_version_marker in result.stdout, error(result.stdout)


@TestScenario
def fips_version_output(self):
    """TC3 `--version` output check."""
    required_version_marker = "FIPS 140-3:\t true"
    fips_bin = resolve_fips_binary()

    with When("I run clickhouse-backup-race-fips --version with default runtime"):
        run_fips_version_check(
            fips_bin=fips_bin,
            mode_name="default",
            mode_value=None,
            required_version_marker=required_version_marker,
        )


@TestScenario
def fips_runtime_mode_matrix(self):
    """TC3 `GODEBUG` runtime matrix (`unset`/`on`/`only`)."""
    required_version_marker = "FIPS 140-3:\t true"
    fips_bin = resolve_fips_binary()
    runtime_modes = [("unset", None), ("on", "fips140=on"), ("only", "fips140=only")]

    with Given("I prepare runtime mode checks for GODEBUG values"):
        pass

    with When("I run clickhouse-backup-race-fips --version in each GODEBUG mode"):
        for mode_name, mode_value in runtime_modes:
            run_fips_version_check(fips_bin, mode_name, mode_value, required_version_marker)

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

            for mode_name, mode_value in runtime_modes:
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
def gofips140_build_flags_present(self):
    """TC4 GOFIPS140 build flag checks"""
    required_flag = "GOFIPS140=v1.0.0"
    makefile_required_checks = [
        "build-fips:",
        "build-race-fips:",
        "$(GO_BUILD)",
        "$(GO_BUILD_STATIC)",
    ]
    dockerfile_required_checks = [
        "clickhouse-backup-race-fips",
        "FROM image_short AS image_fips",
        "COPY build/${TARGETPLATFORM}/clickhouse-backup-fips /bin/clickhouse-backup",
    ]

    root = repo_root()
    makefile_path = os.path.join(root, "Makefile")
    dockerfile_path = os.path.join(root, "Dockerfile")

    with Given("I locate build definitions used for FIPS artifacts"):
        makefile_content = read_required_file(makefile_path)
        dockerfile_content = read_required_file(dockerfile_path)
        makefile_lines = makefile_content.splitlines()
        dockerfile_lines = dockerfile_content.splitlines()

    with When("I validate Makefile FIPS build targets use GOFIPS140=v1.0.0"):
        for makefile_required_text in makefile_required_checks:
            assert_contains(makefile_content, makefile_required_text, "Makefile")
        assert any(
            required_flag in line and "$(GO_BUILD)" in line
            for line in makefile_lines
        ), error("missing GOFIPS140 regular FIPS build command in Makefile")
        assert any(
            required_flag in line and "$(GO_BUILD_STATIC)" in line and "-race" in line
            for line in makefile_lines
        ), error("missing GOFIPS140 build-race-fips command in Makefile")

    with And("I validate Dockerfile FIPS image build path uses GOFIPS140=v1.0.0"):
        for dockerfile_required_text in dockerfile_required_checks:
            assert_contains(dockerfile_content, dockerfile_required_text, "Dockerfile")
        assert any(
            required_flag in line and "go build" in line and "clickhouse-backup-race-fips" in line
            for line in dockerfile_lines
        ), error("missing GOFIPS140 race-fips go build command in Dockerfile")

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


if main():
    fips_ssl_140_3()
