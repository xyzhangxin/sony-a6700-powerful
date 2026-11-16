"""Create small, deterministic CUBE files for manual LUT-path experiments.

This writes local diagnostic data only. It does not connect to a camera or
create/install a firmware patch. CUBE red coordinates vary fastest.
"""

import argparse
import hashlib
import json
from pathlib import Path


SIZE = 17


def transforms():
    return {
        "IDENTITY17": (lambda r, g, b: (r, g, b), "R'=R; G'=G; B'=B"),
        "RBSWAP17": (lambda r, g, b: (b, g, r), "R'=B; G'=G; B'=R"),
        "CROSSMIX17": (lambda r, g, b: (b, g * (0.25 + 0.75 * r * b), r),
                       "R'=B; G'=G*(0.25+0.75*R*B); B'=R"),
    }


def cube(name, transform):
    lines = [f'TITLE "{name}"', "LUT_3D_SIZE 17", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    for blue in range(SIZE):
        for green in range(SIZE):
            for red in range(SIZE):
                values = transform(red / (SIZE - 1), green / (SIZE - 1), blue / (SIZE - 1))
                if not all(0 <= value <= 1 for value in values):
                    raise ValueError("Out-of-range output")
                lines.append(" ".join(f"{value:.8f}" for value in values))
    return ("\n".join(lines) + "\n").encode("ascii")


def validate(payload, transform):
    rows = [tuple(float(value) for value in line.split()) for line in payload.decode("ascii").splitlines()[4:]]
    if len(rows) != SIZE ** 3:
        raise AssertionError("Incorrect grid size")
    maximum_error = 0.0
    for blue in range(SIZE):
        for green in range(SIZE):
            for red in range(SIZE):
                index = red + SIZE * green + SIZE ** 2 * blue
                expected = transform(red / (SIZE - 1), green / (SIZE - 1), blue / (SIZE - 1))
                maximum_error = max(maximum_error, max(abs(actual - target) for actual, target in zip(rows[index], expected)))
    if maximum_error > 1e-8:
        raise AssertionError("CUBE data or coordinate order mismatch")
    return maximum_error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New directory; refuses an existing output")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Output exists")
    # Validate all generated grids before creating output files.
    prepared = []
    for name, (transform, formula) in transforms().items():
        payload = cube(name, transform)
        error = validate(payload, transform)
        prepared.append((name, formula, payload, error))
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = {"schema": "a6700-lab/lut-diagnostics/1", "grid_size": SIZE, "coordinate_order": "red-fastest, then green, then blue",
                "input_domain": [0.0, 1.0], "files": [], "camera_import_verified": False,
                "limitations": ["Numerical RGB transforms only; no S-Log3, gamut, exposure or display conversion",
                                "Identity output need not match a photo taken with PP disabled",
                                "Host validation does not establish Sony import acceptance or still-JPEG application"]}
    for name, formula, payload, error in prepared:
        path = args.output / f"{name}.cube"
        with path.open("xb") as stream:
            stream.write(payload)
        manifest["files"].append({"name": path.name, "formula": formula, "bytes": len(payload), "entries": SIZE ** 3,
                                  "sha256": hashlib.sha256(payload).hexdigest(), "max_numeric_error": error})
    with (args.output / "manifest.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    for item in manifest["files"]:
        print(item["name"], item["entries"], "entries", item["bytes"], "bytes; max error", item["max_numeric_error"])


if __name__ == "__main__":
    main()
