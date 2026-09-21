"""Static validation of the Docker deployment configuration.

These tests check the Dockerfile, the compose file and the ignore rules for the
properties Phase 10 depends on. They are **static**: they parse the files and do
not build an image or start a container, and they must never be presented as
container runtime verification.

The runtime half of Phase 10 needs a working Docker daemon; see the README's
"Docker" section for the exact commands and the current verification status.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = PROJECT_ROOT / "Dockerfile"
COMPOSE = PROJECT_ROOT / "docker-compose.yml"
DOCKERIGNORE = PROJECT_ROOT / ".dockerignore"


@pytest.fixture(scope="module")
def dockerfile() -> str:
    assert DOCKERFILE.is_file(), "Dockerfile is missing"
    return DOCKERFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compose() -> str:
    assert COMPOSE.is_file(), "docker-compose.yml is missing"
    return COMPOSE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dockerignore() -> str:
    assert DOCKERIGNORE.is_file(), ".dockerignore is missing"
    return DOCKERIGNORE.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Dockerfile — structure
# --------------------------------------------------------------------------


def test_dockerfile_starts_from_a_python_image(dockerfile):
    assert re.search(r"(?m)^FROM\s+python:", dockerfile)


def test_dockerfile_python_version_satisfies_the_project(dockerfile):
    """The image must satisfy requires-python (>= 3.11) and the guide (>= 3.12)."""
    match = re.search(r"(?m)^FROM\s+python:(\d+)\.(\d+)", dockerfile)
    assert match, "the base image does not pin a python major.minor"

    major, minor = int(match.group(1)), int(match.group(2))
    assert (major, minor) >= (3, 12), f"python {major}.{minor} is below the guide minimum"


def test_dockerfile_sets_a_working_directory(dockerfile):
    assert re.search(r"(?m)^WORKDIR\s+/", dockerfile)


def test_dockerfile_installs_from_requirements(dockerfile):
    assert "COPY requirements.txt" in dockerfile
    assert re.search(r"pip install[^\n]*-r requirements\.txt", dockerfile)


def test_dockerfile_copies_the_application_source(dockerfile):
    assert "COPY src ./src" in dockerfile


def test_dockerfile_copies_the_canonical_configuration(dockerfile):
    """FSD FR-1 requires the application to load config.yaml."""
    assert "COPY config.yaml ./config.yaml" in dockerfile or "COPY config.yaml ." in dockerfile


def test_dockerfile_does_not_copy_secrets_or_runtime_state(dockerfile):
    forbidden = (".env", "*.pem", "*.key", "data/", "logs/", ".venv")
    for token in forbidden:
        assert f"COPY {token}" not in dockerfile, f"Dockerfile copies {token}"


def test_dockerfile_runs_as_a_non_root_user(dockerfile):
    assert re.search(r"(?m)^USER\s+(?!root)\S+", dockerfile), (
        "the image must drop root before starting the application"
    )


def test_dockerfile_creates_the_writable_directories(dockerfile):
    assert "/app/data" in dockerfile
    assert "/app/logs" in dockerfile


def test_dockerfile_exposes_the_application_port(dockerfile):
    assert re.search(r"(?m)^EXPOSE\s+8000", dockerfile)


def test_dockerfile_entrypoint_is_the_real_application(dockerfile):
    """The real entrypoint is uvicorn serving rdrs.api:app."""
    assert "uvicorn" in dockerfile
    assert "rdrs.api:app" in dockerfile


def test_dockerfile_binds_all_interfaces(dockerfile):
    """A container port must be reachable from outside the container."""
    assert "--host" in dockerfile and "0.0.0.0" in dockerfile


def test_dockerfile_has_a_healthcheck(dockerfile):
    assert re.search(r"(?m)^HEALTHCHECK", dockerfile)


def test_dockerfile_healthcheck_calls_the_real_health_endpoint(dockerfile):
    assert "/health" in dockerfile


def test_dockerfile_healthcheck_validates_the_response(dockerfile):
    """It must fail on a non-2xx response rather than reporting a fixed state."""
    health_block = dockerfile[dockerfile.index("HEALTHCHECK"):]
    assert "status" in health_block or "sys.exit" in health_block, (
        "the healthcheck does not validate the HTTP status"
    )


# --------------------------------------------------------------------------
# docker-compose.yml — structure
# --------------------------------------------------------------------------


def test_compose_defines_exactly_one_service(compose):
    """No unnecessary services or fake dependencies."""
    services = re.findall(r"(?m)^  ([a-z0-9_-]+):$", compose)
    assert services == ["rdrs"], f"expected a single 'rdrs' service, found {services}"


def test_compose_builds_from_the_local_dockerfile(compose):
    assert "context: ." in compose
    assert "dockerfile: Dockerfile" in compose


def test_compose_maps_the_application_port(compose):
    assert '"8000:8000"' in compose or "8000:8000" in compose


def test_compose_mounts_the_data_directory(compose):
    assert "./data:/app/data" in compose


def test_compose_mounts_the_logs_directory(compose):
    assert "./logs:/app/logs" in compose


def test_compose_mounts_the_configuration(compose):
    assert "./config.yaml:/app/config.yaml" in compose


def test_compose_config_mount_is_read_write(compose):
    """POST /settings writes back to config.yaml, so it cannot be :ro."""
    assert "config.yaml:/app/config.yaml:ro" not in compose


def test_compose_has_a_healthcheck_on_the_real_endpoint(compose):
    assert "healthcheck:" in compose
    assert "/health" in compose


def test_compose_healthcheck_validates_the_status(compose):
    assert "sys.exit" in compose or "status" in compose


def test_compose_sets_a_restart_policy(compose):
    assert re.search(r"(?m)^\s+restart:\s*(unless-stopped|always|on-failure)", compose)


def test_compose_uses_an_init_process(compose):
    """PID 1 must forward SIGTERM so the container stops cleanly."""
    assert re.search(r"(?m)^\s+init:\s*true", compose)


def test_compose_contains_no_hardcoded_secrets(compose):
    """Look for real credential assignments, not the word in a comment.

    A comment such as "no secrets are required" is legitimate documentation;
    what matters is that no key is given an inline value.
    """
    # Drop comment lines before looking, so documentation is not mistaken for
    # configuration.
    code = "\n".join(
        line for line in compose.splitlines() if not line.strip().startswith("#")
    ).lower()

    for pattern in ("password", "api_key", "apikey", "secret", "token", "private_key"):
        assert pattern not in code, f"compose sets {pattern}"


def test_compose_does_not_mount_sensitive_host_directories(compose):
    """Watch paths must stay inside the project, never a system directory."""
    for dangerous in ("/c/", "C:\\", "/etc:", "/root:", "/home:", "/var/run/docker.sock"):
        assert dangerous not in compose, f"compose mounts a sensitive host path: {dangerous}"


def test_compose_mounts_only_project_paths(compose):
    sources = re.findall(r"(?m)^\s+- (\./[^:]+):", compose)
    assert sources, "no bind mounts found"
    for source in sources:
        assert source.startswith("./"), f"mount {source} is not a project-relative path"


# --------------------------------------------------------------------------
# .dockerignore — build-context hygiene
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern",
    [
        ".venv/",
        "__pycache__/",
        ".git/",
        "data/",
        "logs/",
        "ptmp/",
        "reports/",
        "tests/",
        ".env",
        "*.db",
        "*.log",
        "config.yaml.bak-*",
    ],
)
def test_dockerignore_excludes(dockerignore, pattern):
    assert pattern in dockerignore, f"{pattern} is not excluded from the build context"


def test_dockerignore_keeps_the_canonical_config_available(dockerignore):
    """config.yaml must NOT be ignored: the image copies it in."""
    assert not re.search(r"(?m)^config\.yaml\s*$", dockerignore), (
        "config.yaml is excluded, but the Dockerfile copies it into the image"
    )


# --------------------------------------------------------------------------
# Cross-file consistency
# --------------------------------------------------------------------------


def test_dockerfile_and_compose_agree_on_the_port(dockerfile, compose):
    assert "8000" in dockerfile and "8000" in compose


def test_dockerfile_and_compose_agree_on_the_health_endpoint(dockerfile, compose):
    assert "/health" in dockerfile and "/health" in compose


def test_the_watch_path_is_the_mounted_data_directory(compose):
    """The configured watch path must be inside a volume the container has.

    config.yaml sets runtime.watch_paths to ['data'], which resolves to
    /app/data inside the container and is the ./data bind mount.
    """
    import yaml

    config = yaml.safe_load((PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8"))
    watch_paths = config["runtime"]["watch_paths"]

    assert watch_paths, "no watch paths configured"

    for path in watch_paths:
        assert not Path(path).is_absolute(), (
            f"watch path {path!r} is absolute; inside the container it would point "
            "outside the mounted data volume"
        )

    assert "data" in watch_paths, (
        "the compose file mounts ./data at /app/data, so 'data' must be a watch path"
    )


def test_the_configured_database_path_lives_under_the_mounted_volume():
    import yaml

    config = yaml.safe_load((PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8"))
    db_path = str(config["persistence"]["db_path"])

    assert db_path.startswith("data"), (
        f"db_path {db_path!r} is outside the mounted data volume, so the database "
        "would not persist across a container restart"
    )
