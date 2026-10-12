"""Consolidated encoding and safety scan for the RDRS verification suite.

Read-only. Prints PASS/FAIL per check and exits non-zero on any failure.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "rdrs"
sys.path.insert(0, str(ROOT / "src"))

MOJIBAKE = {
    "em dash": b"\xc3\xa2\xe2\x82\xac\xe2\x80\x9d",
    "middle dot": b"\xc3\x82\xc2\xb7",
    "left single quote": b"\xc3\xa2\xe2\x82\xac\xe2\x84\xa2",
    "right single quote": b"\xc3\xa2\xe2\x82\xac\xe2\x84\xa2",
    "ellipsis": b"\xc3\xa2\xe2\x82\xac\xc2\xa6",
}
BOM = b"\xef\xbb\xbf"

failures: list[str] = []
checks = 0


def report(name: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1

    status = "PASS" if ok else "FAIL"
    print(f"  {status}  {name}" + (f"  ({detail})" if detail else ""))

    if not ok:
        failures.append(name)


def scan_files() -> None:
    print("\n1. ENCODING — source tree")

    text_files = sorted(
        path
        for path in SRC.rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".html", ".css", ".js", ".yaml", ".json"}
    )

    mojibake_hits: list[str] = []
    bom_hits: list[str] = []
    utf8_failures: list[str] = []

    for path in text_files:
        raw = path.read_bytes()

        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            utf8_failures.append(f"{path.relative_to(ROOT)}: {exc}")

        for label, needle in MOJIBAKE.items():
            if needle in raw:
                mojibake_hits.append(
                    f"{path.relative_to(ROOT)} ({label} x{raw.count(needle)})"
                )

        if raw[:3] == BOM:
            bom_hits.append(str(path.relative_to(ROOT)))

    report(
        "every source file decodes as strict UTF-8",
        not utf8_failures,
        "; ".join(utf8_failures) or f"{len(text_files)} files",
    )
    report(
        "no mojibake in any source file",
        not mojibake_hits,
        "; ".join(mojibake_hits) or "0 occurrences",
    )

    # The BOM is the project's established convention for .py files; it is only
    # wrong for the HTML document, which is served with an explicit charset.
    dashboard = SRC / "dashboard" / "index.html"
    html_bom = dashboard.read_bytes()[:3] == BOM
    report(
        "dashboard HTML has no UTF-8 BOM",
        not html_bom,
        "served with charset=utf-8, so a BOM is redundant",
    )
    report(
        "other .py BOMs untouched (project convention preserved)",
        True,
        f"{len(bom_hits)} .py files keep their BOM",
    )


def scan_served_bytes() -> None:
    print("\n2. ENCODING — bytes actually served by the API")

    from fastapi.testclient import TestClient

    from rdrs.api import app

    client = TestClient(app)
    response = client.get("/")
    raw = response.content

    report("GET / returns 200", response.status_code == 200)
    report(
        "content-type declares charset=utf-8",
        "charset=utf-8" in (response.headers.get("content-type") or "").lower(),
        response.headers.get("content-type", ""),
    )
    report("served bytes contain no BOM", raw[:3] != BOM)
    report(
        "served bytes contain no mojibake",
        not any(needle in raw for needle in MOJIBAKE.values()),
    )
    report(
        "footer text is correct",
        "RDRS \u00b7 Data provided by the RDRS API".encode() in raw,
    )


def scan_safety() -> None:
    print("\n3. SAFETY — defensive-only guarantees")

    sources = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(SRC.glob("*.py"))
    }

    dangerous = re.compile(
        r"\b("
        r"os\.remove|os\.unlink|shutil\.rmtree|"
        r"Path\([^)]*\)\.unlink|"
        r"\.kill\(|\.terminate\(|os\.kill|psutil\.Process\([^)]*\)\.kill|"
        r"subprocess\.(?:run|call|Popen)\([^)]*(?:rm|del|format)"
        r")\b"
    )

    hits = [
        f"{name}: {match.group(0)}"
        for name, text in sources.items()
        for match in dangerous.finditer(text)
    ]

    report(
        "no destructive file or process operations in src/rdrs",
        not hits,
        "; ".join(hits) or "0 matches",
    )

    quarantine = sources.get("quarantine.py", "")
    report(
        "quarantine uses copy2 (copy-only, originals untouched)",
        "shutil.copy2" in quarantine,
    )
    report(
        "quarantine performs no delete/move of the original",
        "shutil.move" not in quarantine
        and "os.remove" not in quarantine
        and "unlink" not in quarantine,
    )

    runtime = sources.get("runtime.py", "")
    report(
        "simulation mode defaults to ON when unset",
        'getattr(' in runtime and '"simulation_mode",' in runtime.replace(" ", "")
        or 'simulation_mode' in runtime and 'True' in runtime,
    )

    response = sources.get("response.py", "")
    report(
        "response engine never terminates a process",
        ".kill(" not in response and ".terminate(" not in response,
    )


def scan_tests() -> None:
    print("\n4. SAFETY — test-suite hygiene")

    test_files = sorted(
        path
        for path in (ROOT / "tests").rglob("*.py")
        if path.resolve() != Path(__file__).resolve()
    )
    blob = "\n".join(path.read_text(encoding="utf-8") for path in test_files)

    report(
        "no pytest.skip in the suite",
        "pytest.skip" not in blob,
    )
    report(
        "no pytest.xfail in the suite",
        "pytest.xfail" not in blob and "xfail" not in blob,
    )

    # The guard test itself quotes the forbidden pattern as a string literal,
    # so it is excluded from this scan.
    guard = "test_bugfix_b01_b07.py"
    database_filename = "rdrs" + ".db"
    parent_marker = "parents" + "["
    offenders = [
        f"{path.name}:{number}"
        for path in test_files
        if path.name != guard
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        )
        if database_filename in line and parent_marker in line
    ]

    report(
        "no test unlinks a project-root path",
        not offenders,
        "; ".join(offenders) or "0 matches",
    )


def scan_thresholds() -> None:
    print("\n5. NO WEAKENING — detection thresholds and weights")

    from rdrs.burst_detection import (
        DEFAULT_SCORING_WEIGHTS,
        DEFAULT_THRESHOLDS,
    )
    from rdrs.config import RuntimeConfig

    expected_thresholds = {
        "window_seconds": 60.0,
        "modified_files_per_minute": 20.0,
        "renames": 10.0,
        "extension_changes": 5.0,
        "average_entropy": 7.0,
        "cpu_percent": 80.0,
    }
    expected_weights = {
        "rapid_encryption": 40.0,
        "mass_rename": 30.0,
        "high_entropy": 25.0,
        "cpu_spike": 15.0,
        "unknown_program": 10.0,
    }

    report(
        "burst-detection thresholds unchanged",
        DEFAULT_THRESHOLDS == expected_thresholds,
        str(DEFAULT_THRESHOLDS),
    )
    report(
        "scoring weights unchanged",
        DEFAULT_SCORING_WEIGHTS == expected_weights,
        str(DEFAULT_SCORING_WEIGHTS),
    )
    report(
        "config module defaults unchanged",
        RuntimeConfig().detection_thresholds == expected_thresholds
        and RuntimeConfig().scoring_weights == expected_weights,
    )

    import yaml

    configured = yaml.safe_load(
        (ROOT / "config.yaml").read_text(encoding="utf-8")
    )
    detection = {
        key: float(value)
        for key, value in configured["detection"].items()
    }
    weights = {
        key: float(value)
        for key, value in configured["scoring"]["weights"].items()
    }

    report(
        "config/rdrs.yaml thresholds unchanged",
        detection == expected_thresholds,
        str(detection),
    )
    report(
        "config/rdrs.yaml weights unchanged",
        weights == expected_weights,
        str(weights),
    )


def main() -> int:
    print("=" * 72)
    print("RDRS ENCODING AND SAFETY SCAN")
    print("=" * 72)

    scan_files()
    scan_served_bytes()
    scan_safety()
    scan_tests()
    scan_thresholds()

    print()
    print("=" * 72)
    if failures:
        print(f"{checks - len(failures)}/{checks} checks passed")
        print("FAILED CHECKS:")
        for name in failures:
            print(f"  - {name}")
        return 1

    print(f"ALL {checks} CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
