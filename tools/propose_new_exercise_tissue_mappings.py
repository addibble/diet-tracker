#!/usr/bin/env python3
"""Propose exercise_tissues mappings for newly added exercises."""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


DEFAULT_DB = Path(__file__).resolve().parents[1] / "diet_tracker.db"


@dataclass(frozen=True)
class MappingSpec:
    tissue_name: str
    role: str
    loading_factor: float


@dataclass(frozen=True)
class ProposedExercise:
    exercise_name: str
    source: str
    mappings: tuple[MappingSpec, ...]


CLONE_MAP: dict[str, str] = {
    "Laying Down Crunches": "Abdominal Curls",
    "Cable Curl with Drop Set": "Straight-Bar Cable Curl",
    "Chest Flys": "Pec Deck",
    "Landmine Row": "Landmine Row (Opposite Stance)",
    "Low Cable Chest Fly Burnout": "Low-High Cable Fly",
    "Single-Arm Cable Curl": "Straight-Bar Cable Curl",
    "Rope Cable Curl Burnout": "Rope Cable Curl",
}

CUSTOM_MAPS: dict[str, tuple[MappingSpec, ...]] = {
    "Arnold Press": (
        MappingSpec("anterior_deltoid", "primary", 0.9),
        MappingSpec("lateral_deltoid", "primary", 0.8),
        MappingSpec("triceps_long_head", "secondary", 0.5),
        MappingSpec("supraspinatus_tendon", "stabilizer", 0.5),
        MappingSpec("shoulder_joint", "stabilizer", 0.4),
        MappingSpec("triceps_lateral_head", "secondary", 0.4),
        MappingSpec("serratus_anterior", "secondary", 0.3),
        MappingSpec("triceps_medial_head", "secondary", 0.3),
        MappingSpec("infraspinatus", "stabilizer", 0.25),
        MappingSpec("teres_minor", "stabilizer", 0.25),
        MappingSpec("subscapularis", "stabilizer", 0.25),
        MappingSpec("pec_clavicular_head", "secondary", 0.2),
        MappingSpec("elbow_joint", "stabilizer", 0.2),
    ),
    "Incline Hammer Curl": (
        MappingSpec("brachioradialis", "primary", 0.9),
        MappingSpec("brachialis", "primary", 0.9),
        MappingSpec("biceps_long_head", "secondary", 0.6),
        MappingSpec("biceps_short_head", "secondary", 0.3),
        MappingSpec("biceps_long_head_tendon", "secondary", 0.3),
        MappingSpec("wrist_extensors", "stabilizer", 0.2),
        MappingSpec("pronator_teres", "stabilizer", 0.2),
        MappingSpec("supinator", "stabilizer", 0.2),
        MappingSpec("elbow_joint", "stabilizer", 0.2),
        MappingSpec("wrist_joint", "stabilizer", 0.2),
        MappingSpec("shoulder_joint", "stabilizer", 0.1),
    ),
    "Preacher Curl": (
        MappingSpec("biceps_short_head", "primary", 1.0),
        MappingSpec("biceps_long_head", "primary", 0.8),
        MappingSpec("brachialis", "secondary", 0.8),
        MappingSpec("brachioradialis", "secondary", 0.4),
        MappingSpec("biceps_long_head_tendon", "secondary", 0.35),
        MappingSpec("elbow_joint", "stabilizer", 0.25),
        MappingSpec("wrist_extensors", "stabilizer", 0.2),
        MappingSpec("wrist_joint", "stabilizer", 0.2),
    ),
    "Bench Press": (
        MappingSpec("pec_sternal_head", "primary", 0.9),
        MappingSpec("pec_clavicular_head", "secondary", 0.6),
        MappingSpec("anterior_deltoid", "secondary", 0.6),
        MappingSpec("triceps_long_head", "secondary", 0.6),
        MappingSpec("triceps_lateral_head", "secondary", 0.6),
        MappingSpec("triceps_medial_head", "secondary", 0.5),
        MappingSpec("serratus_anterior", "stabilizer", 0.3),
        MappingSpec("shoulder_joint", "stabilizer", 0.3),
        MappingSpec("pectoralis_minor", "stabilizer", 0.2),
        MappingSpec("supraspinatus_tendon", "secondary", 0.2),
        MappingSpec("elbow_joint", "stabilizer", 0.2),
        MappingSpec("wrist_joint", "stabilizer", 0.2),
    ),
    "Machine Chest Press": (
        MappingSpec("pec_sternal_head", "primary", 0.9),
        MappingSpec("pec_clavicular_head", "secondary", 0.6),
        MappingSpec("anterior_deltoid", "secondary", 0.5),
        MappingSpec("triceps_long_head", "secondary", 0.5),
        MappingSpec("triceps_lateral_head", "secondary", 0.5),
        MappingSpec("triceps_medial_head", "secondary", 0.4),
        MappingSpec("serratus_anterior", "stabilizer", 0.2),
        MappingSpec("pectoralis_minor", "stabilizer", 0.2),
        MappingSpec("shoulder_joint", "stabilizer", 0.2),
        MappingSpec("elbow_joint", "stabilizer", 0.2),
        MappingSpec("supraspinatus_tendon", "secondary", 0.1),
    ),
    "Hanging Leg Raises": (
        MappingSpec("rectus_abdominis", "primary", 0.9),
        MappingSpec("psoas_major", "primary", 0.9),
        MappingSpec("iliacus", "primary", 0.9),
        MappingSpec("transverse_abdominis", "secondary", 0.5),
        MappingSpec("internal_oblique", "secondary", 0.4),
        MappingSpec("external_oblique", "secondary", 0.4),
        MappingSpec("wrist_flexors", "stabilizer", 0.3),
        MappingSpec("shoulder_joint", "stabilizer", 0.3),
        MappingSpec("latissimus_dorsi", "stabilizer", 0.2),
        MappingSpec("brachioradialis", "stabilizer", 0.2),
        MappingSpec("wrist_joint", "stabilizer", 0.2),
        MappingSpec("elbow_joint", "stabilizer", 0.1),
    ),
    "Incline Barbell Press": (
        MappingSpec("anterior_deltoid", "primary", 0.9),
        MappingSpec("pec_clavicular_head", "primary", 0.8),
        MappingSpec("pec_sternal_head", "secondary", 0.6),
        MappingSpec("triceps_long_head", "secondary", 0.6),
        MappingSpec("triceps_lateral_head", "secondary", 0.5),
        MappingSpec("triceps_medial_head", "secondary", 0.4),
        MappingSpec("pectoralis_minor", "secondary", 0.3),
        MappingSpec("serratus_anterior", "stabilizer", 0.3),
        MappingSpec("shoulder_joint", "stabilizer", 0.3),
        MappingSpec("supraspinatus_tendon", "stabilizer", 0.25),
        MappingSpec("elbow_joint", "stabilizer", 0.2),
        MappingSpec("wrist_joint", "stabilizer", 0.2),
        MappingSpec("infraspinatus", "stabilizer", 0.15),
        MappingSpec("teres_minor", "stabilizer", 0.15),
        MappingSpec("subscapularis", "stabilizer", 0.15),
    ),
    "Incline Neutral-Grip DB Press": (
        MappingSpec("anterior_deltoid", "primary", 0.85),
        MappingSpec("pec_clavicular_head", "primary", 0.75),
        MappingSpec("triceps_long_head", "secondary", 0.6),
        MappingSpec("pec_sternal_head", "secondary", 0.55),
        MappingSpec("triceps_lateral_head", "secondary", 0.5),
        MappingSpec("triceps_medial_head", "secondary", 0.4),
        MappingSpec("serratus_anterior", "stabilizer", 0.35),
        MappingSpec("pectoralis_minor", "secondary", 0.3),
        MappingSpec("shoulder_joint", "stabilizer", 0.25),
        MappingSpec("supraspinatus_tendon", "stabilizer", 0.2),
        MappingSpec("elbow_joint", "stabilizer", 0.2),
        MappingSpec("wrist_joint", "stabilizer", 0.15),
        MappingSpec("infraspinatus", "stabilizer", 0.15),
        MappingSpec("teres_minor", "stabilizer", 0.15),
        MappingSpec("subscapularis", "stabilizer", 0.15),
    ),
    "High-to-Low Cable Fly": (
        MappingSpec("pec_sternal_head", "primary", 0.9),
        MappingSpec("pec_clavicular_head", "secondary", 0.4),
        MappingSpec("anterior_deltoid", "secondary", 0.4),
        MappingSpec("shoulder_joint", "stabilizer", 0.3),
        MappingSpec("pectoralis_minor", "stabilizer", 0.2),
        MappingSpec("serratus_anterior", "stabilizer", 0.2),
        MappingSpec("biceps_long_head_tendon", "secondary", 0.2),
        MappingSpec("elbow_joint", "stabilizer", 0.1),
    ),
}

SOURCE_NOTES: dict[str, str] = {
    "Laying Down Crunches": "clone of Abdominal Curls",
    "Arnold Press": "between Seated DB Shoulder Press and Overhead Press Machine, with more lateral delt and cuff demand",
    "Cable Curl with Drop Set": "clone of Straight-Bar Cable Curl",
    "Chest Flys": "clone of Pec Deck",
    "Incline Hammer Curl": "hybrid of Hammer Curl and Incline Dumbbell Curl",
    "Landmine Row": "clone of Landmine Row (Opposite Stance)",
    "Preacher Curl": "curl profile shifted toward biceps short head and elbow loading",
    "Bench Press": "barbell bench profile, slightly more wrist and elbow loading than Flat Dumbbell Press",
    "Machine Chest Press": "press profile with chest emphasis and reduced stability demand",
    "Hanging Leg Raises": "heavier hip-flexor contribution than Hanging Knee Raises",
    "Incline Barbell Press": "incline press profile with more bilateral barbell demand",
    "Incline Neutral-Grip DB Press": "incline press profile with slightly lower shoulder stress and higher triceps contribution",
    "High-to-Low Cable Fly": "fly profile shifted toward sternal pec fibers",
    "Low Cable Chest Fly Burnout": "clone of Low-High Cable Fly",
    "Single-Arm Cable Curl": "clone of Straight-Bar Cable Curl",
    "Rope Cable Curl Burnout": "clone of Rope Cable Curl",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--format",
        choices=("markdown", "tsv"),
        default="markdown",
        help="Report format for stdout/output.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional file path for the generated proposal report.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Replace existing mappings for the proposed exercises with this proposal.",
    )
    return parser.parse_args()


def fetch_lookup(conn: sqlite3.Connection, query: str) -> dict[str, int]:
    return {name: row_id for row_id, name in conn.execute(query).fetchall()}


def load_clone_mappings(
    conn: sqlite3.Connection,
    source_name: str,
    tissue_names_by_id: dict[int, str],
) -> tuple[MappingSpec, ...]:
    row = conn.execute("SELECT id FROM exercises WHERE name = ?", (source_name,)).fetchone()
    if row is None:
        raise ValueError(f"Source exercise {source_name!r} not found")
    source_id = row[0]
    specs = []
    for tissue_id, role, loading_factor in conn.execute(
        """
        SELECT tissue_id, role, loading_factor
        FROM exercise_tissues
        WHERE exercise_id = ?
        ORDER BY loading_factor DESC, tissue_id
        """,
        (source_id,),
    ).fetchall():
        specs.append(MappingSpec(tissue_names_by_id[tissue_id], role, float(loading_factor)))
    if not specs:
        raise ValueError(f"Source exercise {source_name!r} has no exercise_tissues rows")
    return tuple(specs)


def build_proposals(conn: sqlite3.Connection) -> list[ProposedExercise]:
    tissue_names_by_id = {
        row_id: name for row_id, name in conn.execute("SELECT id, name FROM tissues").fetchall()
    }
    proposals: list[ProposedExercise] = []

    all_names = sorted(set(CLONE_MAP) | set(CUSTOM_MAPS))
    for exercise_name in all_names:
        if exercise_name in CUSTOM_MAPS:
            mappings = CUSTOM_MAPS[exercise_name]
        else:
            mappings = load_clone_mappings(conn, CLONE_MAP[exercise_name], tissue_names_by_id)
        proposals.append(
            ProposedExercise(
                exercise_name=exercise_name,
                source=SOURCE_NOTES[exercise_name],
                mappings=mappings,
            )
        )
    return proposals


def write_markdown(proposals: list[ProposedExercise], fh: TextIO) -> None:
    fh.write("# Proposed Exercise-Tissue Mappings\n\n")
    total_rows = sum(len(proposal.mappings) for proposal in proposals)
    fh.write(f"Exercises: {len(proposals)}\n")
    fh.write(f"Proposed mapping rows: {total_rows}\n\n")
    for proposal in proposals:
        fh.write(f"## {proposal.exercise_name}\n")
        fh.write(f"Basis: {proposal.source}\n\n")
        fh.write("| Tissue | Role | Loading |\n")
        fh.write("| --- | --- | ---: |\n")
        for mapping in proposal.mappings:
            fh.write(
                f"| `{mapping.tissue_name}` | `{mapping.role}` | {mapping.loading_factor:.2f} |\n"
            )
        fh.write("\n")


def write_tsv(proposals: list[ProposedExercise], fh: TextIO) -> None:
    fh.write("exercise_name\tsource\ttissue_name\trole\tloading_factor\n")
    for proposal in proposals:
        for mapping in proposal.mappings:
            fh.write(
                f"{proposal.exercise_name}\t{proposal.source}\t{mapping.tissue_name}"
                f"\t{mapping.role}\t{mapping.loading_factor:.2f}\n"
            )


def render_report(proposals: list[ProposedExercise], fmt: str, output: Path | None) -> None:
    writer = write_markdown if fmt == "markdown" else write_tsv
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as fh:
            writer(proposals, fh)
    else:
        writer(proposals, fh=None)  # type: ignore[arg-type]


def print_report(proposals: list[ProposedExercise], fmt: str) -> None:
    import sys

    writer = write_markdown if fmt == "markdown" else write_tsv
    writer(proposals, sys.stdout)


def apply_proposals(conn: sqlite3.Connection, proposals: list[ProposedExercise]) -> tuple[int, int]:
    exercise_ids = fetch_lookup(conn, "SELECT id, name FROM exercises")
    tissue_ids = fetch_lookup(conn, "SELECT id, name FROM tissues")
    updated_at = dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat(sep=" ")

    replaced = 0
    inserted = 0
    for proposal in proposals:
        exercise_id = exercise_ids.get(proposal.exercise_name)
        if exercise_id is None:
            raise ValueError(f"Exercise {proposal.exercise_name!r} not found")
        replaced += conn.execute(
            "DELETE FROM exercise_tissues WHERE exercise_id = ?",
            (exercise_id,),
        ).rowcount
        for mapping in proposal.mappings:
            tissue_id = tissue_ids.get(mapping.tissue_name)
            if tissue_id is None:
                raise ValueError(f"Tissue {mapping.tissue_name!r} not found")
            conn.execute(
                """
                INSERT INTO exercise_tissues (
                    exercise_id, tissue_id, role, loading_factor, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    exercise_id,
                    tissue_id,
                    mapping.role,
                    mapping.loading_factor,
                    updated_at,
                ),
            )
            inserted += 1
    return replaced, inserted


def main() -> int:
    args = parse_args()
    conn = sqlite3.connect(args.db)
    try:
        proposals = build_proposals(conn)
        if args.output is not None:
            render_report(proposals, args.format, args.output)
        print_report(proposals, args.format)
        if args.apply:
            replaced, inserted = apply_proposals(conn, proposals)
            conn.commit()
            print(f"\nApplied proposal: replaced {replaced} rows, inserted {inserted} rows.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
