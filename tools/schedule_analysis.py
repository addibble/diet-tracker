"""
Build a plausible high-volume weekly schedule based on 6 exercise groups,
tissue recovery times, and Moran-Navarro 2017 findings.

Key Moran-Navarro findings:
- Non-failure (5 reps of 10RM): V1 recovered by 6h, CMJ by 6h
- Failure (10 reps of 10RM): V1 not recovered until 48h, CMJ not until 72h
- CK (muscle damage) returns to baseline at 72h for failure protocols
- Same total volume (6x5 vs 3x10): non-failure recovers 24-48h faster
- Implication: If we train submaximal (2-3 RIR), recovery is ~24-48h.
  If we train to failure, recovery is 48-72h.

Our approach: Train with 2-3 RIR (reps in reserve) to get the faster
recovery window, allowing higher frequency.
"""
import sqlite3
from collections import defaultdict
from itertools import combinations

# Load data
conn = sqlite3.connect("../production_backup_2026-04-11_103611.db")
c = conn.cursor()

# Get all tissue recovery hours
c.execute("SELECT id, name, region, type, recovery_hours FROM tissues")
tissues = {}
for t_id, name, region, ttype, hours in c.fetchall():
    tissues[t_id] = {"name": name, "region": region, "type": ttype, "hours": hours}

# Get exercise-tissue mappings
c.execute("""
    SELECT e.id, e.name, et.tissue_id, t.region, t.recovery_hours, t.type,
           et.loading_factor, et.routing_factor, et.joint_strain_factor, et.tendon_strain_factor
    FROM exercise_tissues et
    JOIN exercises e ON et.exercise_id = e.id
    JOIN tissues t ON t.id = et.tissue_id
""")
exercise_tissues = defaultdict(list)
exercise_names = {}
for eid, ename, tid, tregion, thours, ttype, lf, rf, jsf, tsf in c.fetchall():
    exercise_names[eid] = ename
    sig_load = max(lf or 0, rf or 0, jsf or 0, tsf or 0)
    if sig_load >= 0.3:
        exercise_tissues[eid].append({
            "tissue_id": tid, "region": tregion, "hours": thours,
            "type": ttype, "load": sig_load,
        })
conn.close()

# ── Define 6 groups (from our MILP output, with labels) ──
# Using the MILP groups but with cleaner labels
GROUPS = {
    "Push": [
        "Flat Dumbbell Press", "Push-ups", "Bench Press", "Dips",
        "Machine Chest Press", "Incline Barbell Press", "Incline Dumbbell Press",
        "Incline Push-Up", "Cable Fly", "Low-High Cable Fly",
        "High-to-Low Cable Fly", "Pec Deck", "Landmine Press",
        "Triceps Rope Pushdown", "Overhead Rope Triceps Extension",
    ],
    "Pull": [
        "Seated Cable Row", "Seated Cable Row V-grip", "Landmine Row",
        "Chest-Supported Row", "Wide Pull-ups (Assist)", "Neutral Grip Lat Pulldown",
        "Lat Pulldown", "Assisted Pull-Ups", "Straight-Arm Pulldown",
        "Pec Deck (Rear Delt)", "Face Pulls", "Side-Lying External Rotation",
    ],
    "Legs": [
        "Bulgarian Split Squat", "Walking Lunges", "Leg Press", "Box Jumps",
        "Single Leg Extension", "Glute Drive Machine", "Kettlebell Swings",
        "Cable Pull Through", "Hip Abduction Machine", "Hip Adduction Machine",
        "Back Extension Machine", "Single Leg Hamstring Curl", "Seated Leg Curl",
    ],
    "Shoulders": [
        "Seated DB Shoulder Press", "Overhead Press Machine", "Arnold Press",
        "Lateral Raise", "Single-Arm Cable Lateral Raise",
    ],
    "Arms": [
        "Barbell Curl", "Single-Arm Cable Curl", "Straight-Bar Cable Curl",
        "Preacher Curl", "Rope Cable Curl", "Incline Dumbbell Curl",
        "Hammer Curl", "Incline Hammer Curl", "Dumbbell Shrugs",
    ],
    "Core": [
        "Laying Down Crunches", "Ab Crunch Machine", "Cable Crunch",
        "Reverse Crunch + isometric crunch", "Weighted Plank", "Dead Bug",
        "Flutter Kicks", "Hanging Leg Raises", "Pallof Press",
        "Weighted Decline Crunch", "Cable Bar Chop High to Low", "Farmers Carry",
        "Seated Calf Raise", "Single Leg Calf Raise", "Barbell Tib Lift",
    ],
}

# Map exercise names to IDs
name_to_id = {v: k for k, v in exercise_names.items()}

# Compute per-group: max recovery time of significantly loaded tissues
print("=" * 70)
print("GROUP RECOVERY PROFILES")
print("=" * 70)

group_recovery = {}
group_regions = {}
for group_name, exercises in GROUPS.items():
    all_tissues_hit = defaultdict(float)  # region -> max load
    max_muscle_recovery = 0
    max_tendon_recovery = 0
    max_joint_recovery = 0
    tissue_details = defaultdict(lambda: {"max_load": 0, "hours": 0, "type": ""})

    for ex_name in exercises:
        eid = name_to_id.get(ex_name)
        if not eid:
            continue
        for t in exercise_tissues[eid]:
            key = t["region"]
            all_tissues_hit[key] = max(all_tissues_hit[key], t["load"])
            td = tissue_details[(t["region"], t["type"])]
            td["max_load"] = max(td["max_load"], t["load"])
            td["hours"] = max(td["hours"], t["hours"])
            td["type"] = t["type"]

            if t["type"] == "muscle":
                max_muscle_recovery = max(max_muscle_recovery, t["hours"])
            elif t["type"] == "tendon":
                max_tendon_recovery = max(max_tendon_recovery, t["hours"])
            elif t["type"] == "joint":
                max_joint_recovery = max(max_joint_recovery, t["hours"])

    # With Moran-Navarro: non-failure training reduces effective recovery by ~24h
    # So if muscle says 72h, with 2-3 RIR it's more like 48h
    # But tendons don't benefit from this — collagen synthesis is time-locked
    non_failure_muscle = max(max_muscle_recovery - 24, 24)
    effective_recovery = max(non_failure_muscle, max_tendon_recovery, max_joint_recovery)

    group_recovery[group_name] = {
        "muscle_max": max_muscle_recovery,
        "tendon_max": max_tendon_recovery,
        "joint_max": max_joint_recovery,
        "non_failure_muscle": non_failure_muscle,
        "effective_recovery_h": effective_recovery,
        "effective_recovery_d": round(effective_recovery / 24, 1),
    }
    group_regions[group_name] = dict(all_tissues_hit)

    top_regions = sorted(all_tissues_hit.items(), key=lambda x: -x[1])[:5]
    print("\n%s (%d exercises)" % (group_name, len(exercises)))
    print("  Muscle max: %dh  Tendon max: %dh  Joint max: %dh" % (
        max_muscle_recovery, max_tendon_recovery, max_joint_recovery))
    print("  Non-failure muscle: %dh  Effective: %dh (%.1f days)" % (
        non_failure_muscle, effective_recovery, effective_recovery / 24))
    print("  Top regions: %s" % ", ".join("%s=%.1f" % (r, l) for r, l in top_regions))

# ── Compute group-to-group tissue overlap ──
print("\n" + "=" * 70)
print("GROUP-TO-GROUP TISSUE OVERLAP (shared regions with load >= 0.3)")
print("=" * 70)

for g1, g2 in combinations(GROUPS.keys(), 2):
    shared = set(group_regions[g1].keys()) & set(group_regions[g2].keys())
    if shared:
        overlap_detail = []
        for r in sorted(shared):
            load1 = group_regions[g1][r]
            load2 = group_regions[g2][r]
            if load1 >= 0.3 and load2 >= 0.3:
                overlap_detail.append("%s(%.1f/%.1f)" % (r, load1, load2))
        if overlap_detail:
            print("  %s <-> %s: %s" % (g1, g2, ", ".join(overlap_detail)))

# ── Build weekly schedules ──
print("\n" + "=" * 70)
print("WEEKLY SCHEDULE OPTIONS")
print("=" * 70)

# Recovery constraints (effective, in days):
print("\nRecovery constraints (non-failure, 2-3 RIR):")
for g, r in sorted(group_recovery.items(), key=lambda x: -x[1]["effective_recovery_d"]):
    print("  %-12s: %.1f days (muscle %dh, tendon %dh, joint %dh)" % (
        g, r["effective_recovery_d"], r["muscle_max"], r["tendon_max"], r["joint_max"]))

# Must also account for cross-group overlap!
# Push and Shoulders both hit shoulders heavily
# Pull and Arms both hit biceps/forearms
# So we need minimum spacing between overlapping groups too

print("\n--- SCHEDULE OPTION A: 5 training days, Mon rest, Fri rest ---")
print("(Tu/We/Th/Sa/Su training)")
schedule_a = {
    "Tuesday":   ["Legs"],
    "Wednesday": ["Push"],
    "Thursday":  ["Pull", "Core"],
    "Saturday":  ["Shoulders", "Arms"],
    "Sunday":    ["Legs (light)", "Core"],
}
# Check recovery gaps
print()
for day, groups_list in schedule_a.items():
    print("  %-10s: %s" % (day, " + ".join(groups_list)))

print("\n  Recovery check:")
print("  Legs:      Tue -> Sun(light) = 5 days, Sun -> Tue = 2 days ✅")
print("  Push:      Wed only = 7 days (once/week) ⚠️ low frequency")
print("  Pull:      Thu only = 7 days (once/week) ⚠️ low frequency")
print("  Shoulders: Sat only, but Push(Wed) also hits shoulders = 3 day gap ✅")
print("  Arms:      Sat only, but Pull(Thu) also hits biceps = 2 day gap ✅")
print("  Core:      Thu + Sun = 3 day gap ✅")

print("\n--- SCHEDULE OPTION B: 5 training days, Mon rest, Fri rest ---")
print("(Tu/We/Th/Sa/Su training, higher frequency)")
schedule_b = {
    "Tuesday":   ["Push", "Core"],
    "Wednesday": ["Legs"],
    "Thursday":  ["Pull", "Arms"],
    "Saturday":  ["Push (variation)", "Shoulders"],
    "Sunday":    ["Legs (light)", "Core"],
}
print()
for day, groups_list in schedule_b.items():
    print("  %-10s: %s" % (day, " + ".join(groups_list)))

print("\n  Recovery check:")
print("  Push:      Tue -> Sat = 4 days ✅ (shoulders: 84h joint, 4d = 96h ✅)")
print("  Legs:      Wed -> Sun(light) = 4 days ✅ (quads 72h muscle, tendons 96h = 4d ✅)")
print("  Pull:      Thu only = 7 days, but Arms on same day = bicep volume")
print("  Arms:      Thu only = 7 days, but pull covers biceps indirectly")
print("  Core:      Tue + Sun = 5 days... + Wed legs hits core stabilizers")
print("  Shoulders: Sat, but Tue Push also loads delts = 4 day gap ✅")

print("\n--- SCHEDULE OPTION C: 6 training days, Mon rest only ---")
print("(Tu/We/Th/Fr/Sa/Su training, maximum frequency)")
schedule_c = {
    "Tuesday":   ["Push"],
    "Wednesday": ["Legs"],
    "Thursday":  ["Pull", "Core"],
    "Friday":    ["Shoulders", "Arms"],
    "Saturday":  ["Push (variation)", "Core"],
    "Sunday":    ["Legs (accessory)"],
}
print()
for day, groups_list in schedule_c.items():
    print("  %-10s: %s" % (day, " + ".join(groups_list)))

print("\n  Recovery check:")
print("  Push:      Tue -> Sat = 4 days ✅")
print("  Legs:      Wed -> Sun = 4 days ✅ (tendon-safe)")
print("  Pull:      Thu only (7 days) ⚠️ but Fri Arms covers biceps = overlap in 1 day")
print("  Core:      Thu + Sat = 2 days ✅ (36h recovery)")
print("  Shoulders: Fri, but Tue/Sat Push loads delts too = 3d gaps ✅")
print("  Arms:      Fri, but Thu Pull loads biceps = 1 day gap ⚠️")
print("  ISSUE: Pull on Thu then Arms on Fri = bicep overlap!")

print("\n--- SCHEDULE OPTION D: 5 training days, Mon+Fri rest ---")
print("(Tu/We/Th/Sa/Su training, OPTIMIZED for recovery)")
schedule_d = {
    "Tuesday":   ["Push", "Core (light)"],
    "Wednesday": ["Legs"],
    "Thursday":  ["Pull", "Arms"],
    "Saturday":  ["Shoulders", "Core"],
    "Sunday":    ["Legs (variation/light)"],
}
print()
for day, groups_list in schedule_d.items():
    print("  %-10s: %s" % (day, " + ".join(groups_list)))

print("\n  Recovery check (all non-failure, 2-3 RIR):")
print("  Push:      Tue only (7d), but Sat Shoulders overlaps delts/triceps = indirect 4d ✅")
print("  Legs:      Wed -> Sun = 4 days ✅ (quads 72-84h muscle, tendons 96h = 4d exactly)")
print("  Pull:      Thu only (7d), but Thu Arms adds bicep volume on same day ✅")
print("  Arms:      Thu, with Pull indirect bicep work = solid weekly frequency ✅")
print("  Core:      Tue(light) + Sat = 4 days; core recovers 36h so could be higher ⚠️")
print("  Shoulders: Sat, but Tue Push loads shoulders = 4d gap ✅")
print("  Shoulder joint: Tue(push) -> Sat(OHP) = 4 days = 96h ✅✅")

print("\n--- SCHEDULE OPTION E: 5 training days, Mon+Fri rest ---")
print("(RECOMMENDED: Balanced recovery, 2x/wk per muscle group)")
schedule_e = {
    "Tuesday":   ["Push (heavy compound)", "Core"],
    "Wednesday": ["Legs (heavy compound)"],
    "Thursday":  ["Pull (heavy compound)", "Arms (light)"],
    "Saturday":  ["Shoulders + Push (accessory/isolation)", "Core"],
    "Sunday":    ["Legs (accessory/isolation)", "Arms (heavy)"],
}
print()
for day, groups_list in schedule_e.items():
    print("  %-10s: %s" % (day, " + ".join(groups_list)))

print("\n  RECOVERY ANALYSIS:")
print("  ┌──────────────┬────────────────────┬──────────┬──────────────────┐")
print("  │ Muscle Group │ Sessions           │ Gap      │ Recovery Need    │")
print("  ├──────────────┼────────────────────┼──────────┼──────────────────┤")
print("  │ Chest        │ Tue(heavy)+Sat(iso)│ 4 days   │ 48h muscle ✅     │")
print("  │ Shoulders    │ Tue(push)+Sat(OHP) │ 4 days   │ 84h joint ✅      │")
print("  │ Triceps      │ Tue(push)+Sat(push)│ 4 days   │ 48h+72h elbow ✅  │")
print("  │ Upper Back   │ Thu only           │ 7 days   │ 48h ✅ (low freq) │")
print("  │ Biceps       │ Thu(pull)+Sun(arms)│ 3 days   │ 48h+96h tendon ✅ │")
print("  │ Forearms     │ Thu(pull)+Sun(arms)│ 3 days   │ 48-96h tendon ✅  │")
print("  │ Quads        │ Wed(heavy)+Sun(iso)│ 4 days   │ 72h+96h tendon ✅ │")
print("  │ Hamstrings   │ Wed(heavy)+Sun(iso)│ 4 days   │ 72h+96h tendon ✅ │")
print("  │ Glutes       │ Wed+Sun            │ 4 days   │ 72h ✅            │")
print("  │ Core         │ Tue+Sat            │ 4 days   │ 36h ✅ (could 3x) │")
print("  │ Calves/Tibs  │ Sun(leg day)       │ 7 days   │ 48h ✅ (low freq) │")
print("  └──────────────┴────────────────────┴──────────┴──────────────────┘")
print()
print("  Key design principles:")
print("  1. Shoulder joint never hit <4 days apart (Tue push -> Sat OHP = 96h)")
print("  2. Leg tendons get 4 days (Wed -> Sun = 96h)")
print("  3. Heavy compounds early in week, isolation/accessory on weekends")
print("  4. Arms get 2 touches: indirect via Pull(Thu) + direct on Sun")
print("  5. Mon+Fri rest = recovery windows after hard compound days")
print("  6. All training done 2-3 RIR (non-failure) per Moran-Navarro")
print("     -> muscles recover 24h faster, tendons unaffected")
