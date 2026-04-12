"""Comprehensive analysis: 6 groups, recovery times, workout history patterns."""
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta

import sys
sys.path.insert(0, ".")

from app.planner_groups import (
    build_similarity_groups,
    exercise_tissue_vector,
    weighted_jaccard_similarity,
)

conn = sqlite3.connect("../production_backup_2026-04-11_103611.db")
c = conn.cursor()

# ── Load exercises + tissue mappings ──
c.execute("SELECT id, name, equipment FROM exercises")
exercises_raw = c.fetchall()
c.execute(
    "SELECT et.exercise_id, et.tissue_id, et.role, et.loading_factor,"
    " et.routing_factor, et.fatigue_factor, et.joint_strain_factor,"
    " et.tendon_strain_factor, t.name, t.region, t.recovery_hours, t.type"
    " FROM exercise_tissues et JOIN tissues t ON et.tissue_id = t.id"
)
mappings_raw = c.fetchall()

exercise_by_id = {}
for ex_id, name, equipment in exercises_raw:
    exercise_by_id[ex_id] = dict(
        exercise_id=ex_id, name=name, exercise_name=name,
        equipment=equipment, tissues=[],
    )

tissue_name_map = {}
tissue_recovery = {}
for row in mappings_raw:
    ex_id, t_id, role, loading, routing, fatigue, joint, tendon, t_name, t_region, t_recovery, t_type = row
    tissue_name_map[t_id] = (t_name, t_region, t_type)
    tissue_recovery[t_id] = t_recovery
    if ex_id in exercise_by_id:
        exercise_by_id[ex_id]["tissues"].append(dict(
            tissue_id=t_id, tissue_name=t_name, tissue_region=t_region,
            recovery_hours=t_recovery, role=role, loading_factor=loading,
            routing_factor=routing, fatigue_factor=fatigue,
            joint_strain_factor=joint, tendon_strain_factor=tendon,
        ))

exercises = [ex for ex in exercise_by_id.values() if exercise_tissue_vector(ex)]
priorities = [1.0] * len(exercises)

# ══════════════════════════════════════════════════════════════════════
# PART 1: 6 groups
# ══════════════════════════════════════════════════════════════════════
print("=" * 70)
print("PART 1: 6-GROUP MILP RESULT")
print("=" * 70)
t0 = time.perf_counter()
groups = build_similarity_groups(
    exercises, priorities=priorities,
    min_group_size=2, max_group_size=16,
    min_groups=6, max_groups=6,
)
elapsed = time.perf_counter() - t0
print("Completed in %.2fs => %d groups\n" % (elapsed, len(groups)))

group_exercise_ids = {}  # group_id -> set of exercise_ids
for g in groups:
    ex_list = g["exercises"]
    profile = g["profile"]
    region_loads = defaultdict(list)
    recovery_hours_list = []
    for tid, load in profile.items():
        if tid in tissue_name_map:
            region_loads[tissue_name_map[tid][1]].append(load)
        if tid in tissue_recovery:
            recovery_hours_list.append(tissue_recovery[tid])
    region_summary = {r: round(sum(v) / len(v), 2) for r, v in region_loads.items()}
    top_regions = sorted(region_summary.items(), key=lambda x: -x[1])[:5]
    region_str = ", ".join("%s=%.2f" % (r, v) for r, v in top_regions)
    avg_recovery = sum(recovery_hours_list) / len(recovery_hours_list) if recovery_hours_list else 0
    max_recovery = max(recovery_hours_list) if recovery_hours_list else 0
    gid = g["group_id"]
    group_exercise_ids[gid] = set(ex["exercise_id"] for ex in ex_list)
    print("%s (%d exercises) | avg_recovery=%.0fh max=%.0fh" % (gid, len(ex_list), avg_recovery, max_recovery))
    print("  Regions: %s" % region_str)
    for ex in ex_list:
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

print("Inter-group overlap:")
gp = [(g["group_id"], g["profile"]) for g in groups]
for i in range(len(gp)):
    for j in range(i + 1, len(gp)):
        sim = weighted_jaccard_similarity(gp[i][1], gp[j][1])
        if sim > 0.01:
            print("  %s <-> %s: %.3f" % (gp[i][0], gp[j][0], sim))
print()

# ══════════════════════════════════════════════════════════════════════
# PART 2: Tissue recovery times by region
# ══════════════════════════════════════════════════════════════════════
print("=" * 70)
print("PART 2: TISSUE RECOVERY HOURS BY REGION")
print("=" * 70)
c.execute("SELECT id, name, region, type, recovery_hours FROM tissues ORDER BY region, type, name")
all_tissues = c.fetchall()
region_data = defaultdict(list)
for t_id, t_name, t_region, t_type, t_recovery in all_tissues:
    region_data[t_region].append((t_name, t_type, t_recovery))

for region in sorted(region_data.keys()):
    tissues = region_data[region]
    muscles = [(n, h) for n, t, h in tissues if t == "muscle"]
    tendons = [(n, h) for n, t, h in tissues if t == "tendon"]
    joints = [(n, h) for n, t, h in tissues if t == "joint"]
    all_hours = [h for _, _, h in tissues]
    print("\n%s: avg=%.0fh range=%s-%sh (%d tissues: %d muscle, %d tendon, %d joint)" % (
        region.upper(), sum(all_hours)/len(all_hours), min(all_hours), max(all_hours),
        len(tissues), len(muscles), len(tendons), len(joints)))
    for name, ttype, hours in sorted(tissues, key=lambda x: (-x[2], x[0])):
        print("  %-35s %-8s %3.0fh (%.1f days)" % (name, ttype, hours, hours/24))

# ══════════════════════════════════════════════════════════════════════
# PART 3: Actual workout history analysis
# ══════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 3: YOUR WORKOUT HISTORY PATTERNS")
print("=" * 70)

# Get all workout sessions with their exercises
c.execute("""
    SELECT ws.id, ws.date, ws.notes,
           GROUP_CONCAT(DISTINCT e.name) as exercises,
           COUNT(DISTINCT wset.exercise_id) as exercise_count,
           COUNT(wset.id) as set_count
    FROM workout_sessions ws
    JOIN workout_sets wset ON wset.session_id = ws.id
    JOIN exercises e ON wset.exercise_id = e.id
    GROUP BY ws.id
    ORDER BY ws.date
""")
sessions = c.fetchall()
print("\nTotal sessions: %d" % len(sessions))
if sessions:
    print("Date range: %s to %s" % (sessions[0][1], sessions[-1][1]))

# Region trained per session
c.execute("""
    SELECT ws.date, t.region, MAX(
        MAX(et.loading_factor, et.routing_factor, et.joint_strain_factor, et.tendon_strain_factor)
    ) as max_load
    FROM workout_sessions ws
    JOIN workout_sets wset ON wset.session_id = ws.id
    JOIN exercise_tissues et ON et.exercise_id = wset.exercise_id
    JOIN tissues t ON t.id = et.tissue_id
    WHERE MAX(et.loading_factor, et.routing_factor, et.joint_strain_factor, et.tendon_strain_factor) >= 0.3
    GROUP BY ws.date, t.region
    ORDER BY ws.date
""")
date_regions = c.fetchall()

# Build date -> regions trained
sessions_by_date = defaultdict(set)
for date_str, region, load in date_regions:
    sessions_by_date[date_str].add(region)

# Analyze rest days between training the same region
print("\nREST DAYS BETWEEN TRAINING SAME REGION:")
region_dates = defaultdict(list)
for date_str, region, load in date_regions:
    region_dates[region].append(date_str)

for region in sorted(region_dates.keys()):
    dates = sorted(set(region_dates[region]))
    if len(dates) < 2:
        continue
    gaps = []
    for i in range(1, len(dates)):
        d1 = datetime.strptime(dates[i-1], "%Y-%m-%d")
        d2 = datetime.strptime(dates[i], "%Y-%m-%d")
        gap = (d2 - d1).days
        if gap <= 14:  # ignore long breaks
            gaps.append(gap)
    if gaps:
        avg_gap = sum(gaps) / len(gaps)
        print("  %-20s: avg=%.1f days, median=%.1f, range=%d-%d (n=%d sessions)" % (
            region, avg_gap, sorted(gaps)[len(gaps)//2], min(gaps), max(gaps), len(dates)))

# Days between sessions
print("\nDAYS BETWEEN SESSIONS:")
all_dates = sorted(sessions_by_date.keys())
session_gaps = []
for i in range(1, len(all_dates)):
    d1 = datetime.strptime(all_dates[i-1], "%Y-%m-%d")
    d2 = datetime.strptime(all_dates[i], "%Y-%m-%d")
    gap = (d2 - d1).days
    if gap <= 7:
        session_gaps.append(gap)

if session_gaps:
    avg = sum(session_gaps) / len(session_gaps)
    print("  Average gap: %.1f days" % avg)
    print("  Median gap: %d days" % sorted(session_gaps)[len(session_gaps)//2])
    from collections import Counter
    gap_counts = Counter(session_gaps)
    for gap_val in sorted(gap_counts.keys()):
        pct = gap_counts[gap_val] / len(session_gaps) * 100
        print("  %d day(s) rest: %d times (%.0f%%)" % (gap_val, gap_counts[gap_val], pct))

# What regions do you typically train together?
print("\nMOST COMMON REGION COMBOS PER SESSION (top 20):")
combo_counts = Counter()
for date_str, regions in sessions_by_date.items():
    # Simplify to major regions only
    major = sorted(r for r in regions if r in {
        "chest", "shoulders", "triceps", "biceps", "upper_back",
        "forearms", "core", "quads", "hamstrings", "glutes",
        "calves", "lower_back", "hips"
    })
    if major:
        combo_counts[tuple(major)] += 1

for combo, count in combo_counts.most_common(20):
    print("  %3d x %s" % (count, " + ".join(combo)))

# Recent 30 sessions detail
print("\nLAST 30 SESSIONS:")
for sid, date_str, notes, ex_names, ex_count, set_count in sessions[-30:]:
    regions = sessions_by_date.get(date_str, set())
    major = sorted(r for r in regions if r in {
        "chest", "shoulders", "triceps", "biceps", "upper_back",
        "forearms", "core", "quads", "hamstrings", "glutes",
        "calves", "lower_back", "hips"
    })
    print("  %s: %d exercises, %d sets | %s" % (date_str, ex_count, set_count, " + ".join(major)))

conn.close()
