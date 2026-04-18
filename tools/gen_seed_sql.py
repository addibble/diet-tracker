"""Generate backend/app/seed_data.sql from a production DB backup.

Usage:
    python tools/gen_seed_sql.py [path/to/production_backup.db]

If no path is given, the newest `production_backup_*.db` in the repo root is used.

The output contains idempotent `INSERT OR IGNORE` statements for reference-catalog
tables (tissues, exercises, exercise_tissues, tissue_model_configs, tracked_tissues,
training_exclusion_windows). Only columns present in the current ORM schema are
emitted — any extra legacy columns in the source DB are dropped.
"""
from __future__ import annotations

import glob
import os
import sqlite3
import sys

# Column allow-lists must match the current SQLModel definitions.
ALLOWED: dict[str, set[str]] = {
    "tissues": {
        "id", "name", "display_name", "type", "region", "tracking_mode",
        "recovery_hours", "notes", "updated_at",
    },
    "tracked_tissues": {
        "id", "tissue_id", "side", "display_name", "active", "notes",
        "created_at", "updated_at",
    },
    "exercises": {
        "id", "name", "equipment", "allow_heavy_loading", "load_input_mode",
        "laterality", "bodyweight_fraction", "external_load_multiplier",
        "variant_group", "grip_style", "grip_width", "support_style",
        "set_metric_mode", "estimated_minutes_per_set", "notes", "created_at",
    },
    "exercise_tissues": {
        "id", "exercise_id", "tissue_id", "role", "loading_factor",
        "routing_factor", "fatigue_factor", "joint_strain_factor",
        "tendon_strain_factor", "laterality_mode", "updated_at",
    },
    "tissue_model_configs": {
        "tissue_id", "capacity_prior", "recovery_tau_days", "fatigue_tau_days",
        "collapse_drop_threshold", "ramp_sensitivity", "risk_sensitivity",
        "updated_at",
    },
    "training_exclusion_windows": {
        "id", "start_date", "end_date", "kind", "notes", "exclude_from_model",
        "created_at",
    },
}

ORDER = [
    "tissues",
    "tracked_tissues",
    "exercises",
    "exercise_tissues",
    "tissue_model_configs",
    "training_exclusion_windows",
]

# Defaults for NOT NULL columns that may be missing from older prod DBs.
# Must match ORM defaults in backend/app/models.py.
DEFAULTS: dict[str, dict[str, object]] = {
    "exercises": {
        "allow_heavy_loading": True,
        "grip_style": "none",
        "grip_width": "none",
        "support_style": "none",
        "set_metric_mode": "reps",
        "variant_group": None,
    },
}


def format_value(v: object) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        src_path = argv[1]
    else:
        candidates = sorted(glob.glob("production_backup_*.db"))
        if not candidates:
            print("No production_backup_*.db found in cwd", file=sys.stderr)
            return 1
        src_path = candidates[-1]

    out_path = os.path.join("backend", "app", "seed_data.sql")
    src = sqlite3.connect(src_path)
    src.row_factory = sqlite3.Row

    with open(out_path, "w", encoding="utf-8") as out:
        out.write(f"-- Auto-generated from {os.path.basename(src_path)}\n")
        out.write("-- Regenerate via tools/gen_seed_sql.py.\n")
        out.write("-- Rows are idempotent (INSERT OR IGNORE).\n\n")
        for t in ORDER:
            prod_cols = [
                d[0] for d in src.execute(f'SELECT * FROM "{t}" LIMIT 0').description
            ]
            keep = [c for c in prod_cols if c in ALLOWED[t]]
            # Add ORM default columns that are allowed but missing from prod.
            defaults = DEFAULTS.get(t, {})
            synthesized = [
                c for c in ALLOWED[t] if c in defaults and c not in prod_cols
            ]
            out_cols = keep + synthesized
            rows = src.execute(f'SELECT * FROM "{t}"').fetchall()
            out.write(f"-- {t}: {len(rows)} rows\n")
            collist = ", ".join(f'"{c}"' for c in out_cols)
            for r in rows:
                values = ", ".join(
                    [format_value(r[c]) for c in keep]
                    + [format_value(defaults[c]) for c in synthesized]
                )
                out.write(
                    f'INSERT OR IGNORE INTO "{t}" ({collist}) VALUES ({values});\n'
                )
            out.write("\n")

    print(f"Wrote {out_path} from {src_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
