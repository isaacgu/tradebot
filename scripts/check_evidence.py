"""Check Appendix-G evidence, frozen identity and carried obligations, never approve a gate.

Artifact references use `` `repo/path` SHA-256 `<64 lowercase hex>` ``. Downloaded
CI artifacts must be present at their recorded repository-relative paths before
local hash verification can succeed. Signatures are checked for recorded human
names/dates only: this program cannot authenticate a person or grant approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

FROZEN_SPEC_SHA256 = "dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37"
CATEGORIES = {
    1: "CI run on a committed git SHA",
    2: "Report / manifest artifact hashes",
    3: "Observability evidence",
    4: "Independent reviewer sign-off",
    5: "Principal sign-off",
}
STATUSES = {"PROVIDED", "DEFERRED-BY-PHASE", "FAILED"}
_ARTIFACT = re.compile(r"`([^`\n]+)`\s+SHA-256\s+`([0-9a-f]{64})`")
_SHA256 = re.compile(r"\b[0-9a-f]{64}\b")
_GATE = re.compile(r"^# Gate (\d+) evidence\b", re.MULTILINE)
_OBLIGATION = re.compile(r"G\d+-[A-Za-z0-9_-]+\Z")


@dataclass(frozen=True, slots=True)
class EvidenceResult:
    gate: int | None
    errors: tuple[str, ...]
    verified_artifacts: tuple[str, ...]
    human_approvals_authenticated: bool = False
    limitation: str = (
        "Machine validation is not gate approval. Recorded human sign-offs are checked "
        "for presence only; their authenticity and authority require human review."
    )

    @property
    def machine_checks_passed(self) -> bool:
        return not self.errors


def _tables(text: str) -> dict[str, list[list[str]]]:
    result: dict[str, list[list[str]]] = {}
    section = ""
    fenced = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fenced = not fenced
        if fenced:
            continue
        if line.startswith("## "):
            section = line[3:].strip()
            result.setdefault(section, [])
        elif line.startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if not cells or cells[0] in {"#", "id"} or re.fullmatch(r":?-+:?", cells[0]):
                continue
            result.setdefault(section, []).append(cells)
    return result


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _artifacts(
    content: str,
    *,
    root: Path,
    context: str,
    errors: list[str],
    verified: set[str],
    required: bool = False,
) -> tuple[str, ...]:
    references = _ARTIFACT.findall(content)
    if required and not references:
        errors.append(f"{context}: requires artifact repo path and SHA-256")
    paired_hashes = {digest for _, digest in references}
    if set(_SHA256.findall(content)) - paired_hashes:
        errors.append(f"{context}: an artifact hash has no paired repository path")
    paths: list[str] = []
    for relative, expected in references:
        # Path.resolve also rejects a symlink leading outside the evidence root.
        path = (root / relative).resolve()
        if (
            Path(relative).is_absolute()
            or "\\" in relative
            or ":" in relative
            or not path.is_relative_to(root)
        ):
            errors.append(
                f"{context}: artifact path is outside repository or nonportable: {relative}"
            )
            continue
        paths.append(relative)
        if not path.is_file():
            errors.append(f"{context}: artifact missing: {relative}")
        elif _hash(path) != expected:
            errors.append(f"{context}: artifact hash mismatch: {relative}")
        else:
            verified.add(relative)
    return tuple(paths)


def _obligations(tables: dict[str, list[list[str]]], *, inherited: bool) -> list[list[str]]:
    prefix = "Inherited obligations" if inherited else "Obligations this gate creates"
    return [row for heading, rows in tables.items() if heading.startswith(prefix) for row in rows]


def _check_obligations(
    *,
    gate: int,
    tables: dict[str, list[list[str]]],
    previous: Path | None,
    root: Path,
    errors: list[str],
    verified: set[str],
) -> None:
    inherited = _obligations(tables, inherited=True)
    created = _obligations(tables, inherited=False)
    current: dict[str, list[str]] = {}
    for row in inherited + created:
        if len(row) not in (5, 6) or not _OBLIGATION.fullmatch(row[0]):
            errors.append("obligation rows require id, category, deferred gate, due gate, status")
            continue
        identity, _category, _deferred, due_text, status = row[:5]
        if identity in current:
            errors.append(f"duplicate obligation {identity}")
        current[identity] = row
        due_gates = re.findall(r"Gate\s+(\d+)", due_text)
        if not due_gates:
            errors.append(f"{identity}: missing due gate")
        elif int(due_gates[0]) <= gate and status != "PROVIDED":
            errors.append(
                f"{identity}: inherited obligation is due at Gate {due_gates[0]} and not discharged"
            )
        if status not in STATUSES | {"open"}:
            errors.append(f"{identity}: invalid obligation status {status!r}")
        if status == "PROVIDED":
            paths = _artifacts(
                row[5] if len(row) == 6 else "",
                root=root,
                context=identity,
                errors=errors,
                verified=verified,
                required=True,
            )
            if identity == "G0-1" and not any(
                path.endswith((".png", ".jpg", ".webp")) for path in paths
            ):
                errors.append("G0-1: dashboard screenshot obligation requires a rendered image")
            if identity == "G0-2" and "scripts/check_evidence.py" not in paths:
                errors.append("G0-2: checker obligation requires scripts/check_evidence.py")
    if gate == 0:
        if inherited:
            errors.append("Gate 0 cannot inherit obligations from an earlier gate")
        return
    if previous is None or not previous.is_file():
        errors.append("previous gate evidence is required to validate inherited obligations")
        return
    prior_text = previous.read_text(encoding="utf-8")
    prior_gate = _GATE.search(prior_text)
    if prior_gate is None or int(prior_gate[1]) != gate - 1:
        errors.append("previous evidence must be from the immediately preceding gate")
        return
    prior_tables = _tables(prior_text)
    if FROZEN_SPEC_SHA256 not in prior_text:
        errors.append("previous gate must retain the frozen SPEC hash")
    if gate > 1:
        _check_obligations(
            gate=gate - 1,
            tables=prior_tables,
            previous=root / "docs" / "reports" / f"gate{gate - 2}_evidence.md",
            root=root,
            errors=errors,
            verified=verified,
        )
    prior_rows = _obligations(prior_tables, inherited=True) + _obligations(
        prior_tables, inherited=False
    )
    inherited_ids = {row[0] for row in inherited if row}
    prior_ids: set[str] = set()
    for row in prior_rows:
        if len(row) < 5:
            errors.append("previous gate has a malformed obligation")
            continue
        if row[4] == "PROVIDED":
            continue
        identity = row[0]
        prior_ids.add(identity)
        now = current.get(identity)
        if identity not in inherited_ids or now is None:
            errors.append(f"{identity}: missing inherited obligation from previous gate")
        elif now[:4] != row[:4]:
            errors.append(
                f"{identity}: inherited identity/category/deferred/due fields "
                "must be copied verbatim"
            )
    if inherited_ids - prior_ids:
        errors.append(
            "inherited obligations must correspond to unresolved previous-gate obligations"
        )


def _signoffs(text: str, provided: dict[int, str], errors: list[str]) -> None:
    names: list[str] = []
    for category, role in ((4, r"Independent (?:human )?reviewer"), (5, "Principal")):
        if category not in provided:
            continue
        match = re.search(
            rf"^{role}:\s*(.*?)\s+date:\s*(\d{{4}}-\d{{2}}-\d{{2}})\s*$",
            text,
            re.MULTILINE | re.IGNORECASE,
        )
        if match is None:
            errors.append(
                f"category {category}: PROVIDED requires a recorded human sign-off name and date"
            )
            continue
        name = re.sub(r"\s*[—-]\s*APPROVED\s*$", "", match[1], flags=re.IGNORECASE).strip()
        try:
            date.fromisoformat(match[2])
        except ValueError:
            errors.append(f"category {category}: human sign-off date is invalid")
        if (
            not name
            or "_" in name
            or re.search(
                r"\b(agent|codex|assistant|automation|gpt|pending|unknown|tbd)\b",
                name,
                re.IGNORECASE,
            )
        ):
            errors.append(f"category {category}: approval must be recorded by a named human")
        if name not in provided[category] or match[2] not in provided[category]:
            errors.append(f"category {category}: human sign-off must match its evidence row")
        names.append(name.casefold())
    if len(names) == 2 and names[0] == names[1]:
        errors.append("reviewer must be independent of the Principal")


def check_evidence(
    evidence: Path,
    *,
    repo_root: Path,
    previous: Path | None = None,
) -> EvidenceResult:
    """Validate a local evidence pack without editing it or authenticating approvals."""
    root = repo_root.resolve()
    text = evidence.read_text(encoding="utf-8")
    errors: list[str] = []
    verified: set[str] = set()
    match = _GATE.search(text)
    gate = int(match[1]) if match is not None else None
    if gate is None:
        errors.append("missing '# Gate N evidence' heading")
    frozen = re.search(
        r"Frozen SPEC judged against:\s*`?docs/SPEC\.md`?\s+v1\.0,\s*SHA-256\s*`([0-9a-f]{64})`",
        text,
    )
    spec = root / "docs" / "SPEC.md"
    if frozen is None or frozen[1] != FROZEN_SPEC_SHA256:
        errors.append("evidence must pin the frozen SPEC v1.0 SHA-256")
    if not spec.is_file() or _hash(spec) != FROZEN_SPEC_SHA256:
        errors.append(
            "local SPEC differs from frozen identity; a signed amendment requires checker review"
        )

    tables = _tables(text)
    rows = tables.get("Evidence categories", [])
    seen: list[int] = []
    provided: dict[int, str] = {}
    for row in rows:
        if len(row) != 4 or not row[0].isdigit():
            errors.append("evidence categories require exactly four cells per row")
            continue
        number = int(row[0])
        seen.append(number)
        _number, category, status, content = row
        if CATEGORIES.get(number) != category:
            errors.append(f"category {number}: category name does not match Appendix G")
        if status not in STATUSES:
            errors.append(f"category {number}: status must be exactly one allowed token")
            continue
        if status == "FAILED":
            errors.append(f"category {number}: FAILED evidence prevents readiness")
            if not content:
                errors.append(f"category {number}: FAILED needs cause and remediation")
        elif status == "DEFERRED-BY-PHASE":
            if number in (1, 2, 4, 5):
                errors.append(f"category {number}: this category may never be deferred")
            phase = re.search(r"\bP(\d+)\b", content)
            due = re.search(r"\bGate\s+(\d+)\b", content)
            if (
                phase is None
                or due is None
                or gate is None
                or int(phase[1]) <= gate
                or int(due[1]) < int(phase[1])
                or not re.search(r"§\d+(?:\.\d+)?", content)
            ):
                errors.append(
                    f"category {number}: deferral requires later owning phase, "
                    "due gate and SPEC criterion"
                )
            if _ARTIFACT.search(content):
                errors.append(
                    f"category {number}: no-downgrade forbids deferring an existing artifact class"
                )
            if number == 3:
                errors.append(
                    "observability exposition is required at every gate; "
                    "only a missing format may defer"
                )
        else:
            provided[number] = content
            if number == 1:
                if not re.search(r"https://[^\s)]+/actions/runs/\d+\b", content) or not re.search(
                    r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])",
                    content,
                ):
                    errors.append(
                        "category 1: CI requires an immutable run URL and full committed SHA"
                    )
            if number in (2, 3):
                paths = _artifacts(
                    content,
                    root=root,
                    context=f"category {number}",
                    errors=errors,
                    verified=verified,
                    required=True,
                )
                if number == 3 and not any(path.endswith(".prom") for path in paths):
                    errors.append("category 3: machine-readable Prometheus exposition is required")
                if number == 3 and gate is not None and gate >= 4:
                    if not any(path.endswith(".json") for path in paths) or not any(
                        path.endswith((".png", ".jpg", ".webp")) for path in paths
                    ):
                        errors.append(
                            "category 3: Gate 4+ requires dashboard JSON and rendered screenshot"
                        )
    if sorted(seen) != list(CATEGORIES):
        errors.append("exactly one row for each of the five evidence categories is required")
    _signoffs(text, provided, errors)
    if gate is not None:
        if previous is None and gate > 0:
            previous = root / "docs" / "reports" / f"gate{gate - 1}_evidence.md"
        _check_obligations(
            gate=gate, tables=tables, previous=previous, root=root, errors=errors, verified=verified
        )
    return EvidenceResult(
        gate=gate, errors=tuple(errors), verified_artifacts=tuple(sorted(verified))
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args(argv)
    try:
        result = check_evidence(args.evidence, repo_root=args.repo_root, previous=args.previous)
    except (OSError, ValueError) as exc:
        print(json.dumps({"machine_checks_passed": False, "error": str(exc)}))
        return 1
    print(
        json.dumps(
            asdict(result) | {"machine_checks_passed": result.machine_checks_passed}, indent=2
        )
    )
    return 0 if result.machine_checks_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
