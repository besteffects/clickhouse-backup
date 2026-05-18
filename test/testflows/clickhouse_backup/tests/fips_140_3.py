import os

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


@TestScenario
def gofips140_build_flags_present(self):
    """Verify GOFIPS140=v1.0.0 is defined in FIPS build paths."""
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
    """TC4-first FIPS 140-3 automation entrypoint for clickhouse-backup."""
    for scenario in loads(current_module(), Scenario, Suite):
        Scenario(run=scenario, flags=TE)


if main():
    fips_ssl_140_3()
