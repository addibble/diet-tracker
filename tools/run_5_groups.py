"""Run MILP grouping with exactly 5 groups on production data."""
import sqlite3
import time
from collections import defaultdict

import sys
sys.path.insert(0, ".")

from app.planner_groups import (
    build_similarity_groups,
    exercise_tissue_vector,
    weighted_jaccard_similarity,
)

conn = sqlite3.connect("../production_backup_2026-04-11_103611.db")
c = conn.cursor()
c.execute("SELECT id, name, equipment FROM exercises")
exercises_raw = c.fetchall()
c.execute(
    "SELECT et.exercise_id, et.tissue_id, et.role, et.loading_factor,"
    " et.routing_factor, et.fatigue_factor, et.joint_strain_factor,"
    " et.tendon_strain_factor, t.name, t.region, t.recovery_hours"
    " FROM exercise_tissues et JOIN tissues t ON et.tissue_id = t.id"
)
mappings_raw = c.fetchall()
conn.close()

exercise_by_id = {}
for ex_id, name, equipment in exercises_raw:
    exercise_by_id[ex_id] = dict(
        exercise_id=ex_id, name=name, exercise_name=name,
        equipment=equipment, tissues=[],
    )

tissue_name_map = {}
for row in mappings_raw:
    ex_id, t_id, role, loading, routing, fatigue, joint, tendon, t_name, t_region, t_recovery = row
    tissue_name_map[t_id] = (t_name, t_region)
    if ex_id in exercise_by_id:
        exercise_by_id[ex_id]["tissues"].append(dict(
            tissue_id=t_id, tissue_name=t_name, tissue_region=t_region,
            recovery_hours=t_recovery, role=role, loading_factor=loading,
            routing_factor=routing, fatigue_factor=fatigue,
            joint_strain_factor=joint, tendon_strain_factor=tendon,
        ))

exercises = [ex for ex in exercise_by_id.values() if exercise_tissue_vector(ex)]
priorities = [1.0] * len(exercises)

print("Running with 5 groups, min_size=2, max_size=20 on %d exercises..." % len(exercises))
t0 = time.perf_counter()
groups = build_similarity_groups(
    exercises,
    priorities=priorities,
    min_group_size=2,
    max_group_size=20,
    min_groups=5,
    max_groups=5,
)
elapsed = time.perf_counter() - t0
print("Completed in %.2fs => %d groups\n" % (elapsed, len(groups)))

for g in groups:
    ex_list = g["exercises"]
    profile = g["profile"]
    region_loads = defaultdict(list)
    for tid, load in profile.items():
        if tid in tissue_name_map:
            region_loads[tissue_name_map[tid][1]].append(load)
    region_summary = {r: round(sum(v) / len(v), 2) for r, v in region_loads.items()}
    top_regions = sorted(region_summary.items(), key=lambda x: -x[1])[:5]
    region_str = ", ".join("%s=%.2f" % (r, v) for r, v in top_regions)
    print("=== %s (%d exercises) ===" % (g["group_id"], len(ex_list)))
    print("  Regions: %s" % region_str)
    for ex in ex_list:
        # show per-exercise dominant region
        ex_regions = defaultdict(float)
        for t in ex["tissues"]:
            load = max(t["loading_factor"] or 0, t["routing_factor"] or 0,
                       t["joint_strain_factor"] or 0, t["tendon_strain_factor"] or 0)
            if load >= 0.3:
                ex_regions[t["tissue_region"]] = max(ex_regions[t["tissue_region"]], load)
        top = sorted(ex_regions.items(), key=lambda x: -x[1])[:3]
        tag = ", ".join("%s=%.1f" % (r, v) for r, v in top)
        print("    - %-40s  [%s]" % (ex["exercise_name"], tag))
    print()

print("=== Inter-group overlap (Jaccard) ===")
gp = [(g["group_id"], g["profile"]) for g in groups]
for i in range(len(gp)):
    for j in range(i + 1, len(gp)):
        sim = weighted_jaccard_similarity(gp[i][1], gp[j][1])
        print("  %s <-> %s: %.3f" % (gp[i][0], gp[j][0], sim))
