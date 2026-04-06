from __future__ import annotations

from collections import defaultdict

import numpy as np
from scipy import sparse
from scipy.optimize import Bounds, LinearConstraint, milp

DEFAULT_MIN_GROUP_SIZE = 2
DEFAULT_MAX_GROUP_SIZE = 6
DEFAULT_MIN_GROUP_COUNT = 8
DEFAULT_MAX_GROUP_COUNT = 16
DEFAULT_TARGET_GROUP_SIZE = 4.5
SIGNIFICANT_GROUP_LOAD = 0.3


def significant_mapping_load(mapping: dict) -> float:
    return max(
        float(mapping.get("loading_factor") or 0.0),
        float(mapping.get("routing_factor") or 0.0),
        float(mapping.get("joint_strain_factor") or 0.0),
        float(mapping.get("tendon_strain_factor") or 0.0),
    )


def exercise_tissue_vector(
    exercise: dict,
    *,
    min_load: float = SIGNIFICANT_GROUP_LOAD,
) -> dict[int, float]:
    vector: dict[int, float] = {}
    for mapping in exercise.get("tissues", []):
        tissue_id = mapping.get("tissue_id")
        if tissue_id is None:
            continue
        load = significant_mapping_load(mapping)
        if load < min_load:
            continue
        vector[int(tissue_id)] = max(vector.get(int(tissue_id), 0.0), load)
    return vector


def combine_tissue_vectors(exercises: list[dict]) -> dict[int, float]:
    combined: dict[int, list[float]] = defaultdict(list)
    for exercise in exercises:
        for tissue_id, load in exercise_tissue_vector(exercise).items():
            combined[tissue_id].append(load)
    return {
        tissue_id: round(sum(loads) / len(loads), 4)
        for tissue_id, loads in combined.items()
    }


def weighted_jaccard_similarity(
    left: dict[int, float],
    right: dict[int, float],
) -> float:
    keys = set(left) | set(right)
    if not keys:
        return 0.0
    overlap = 0.0
    union = 0.0
    for key in keys:
        left_value = left.get(key, 0.0)
        right_value = right.get(key, 0.0)
        overlap += min(left_value, right_value)
        union += max(left_value, right_value)
    return overlap / union if union > 0 else 0.0


def choose_group_count(
    exercise_count: int,
    *,
    min_group_size: int = DEFAULT_MIN_GROUP_SIZE,
    max_group_size: int = DEFAULT_MAX_GROUP_SIZE,
    min_groups: int = DEFAULT_MIN_GROUP_COUNT,
    max_groups: int = DEFAULT_MAX_GROUP_COUNT,
    target_group_size: float = DEFAULT_TARGET_GROUP_SIZE,
) -> int:
    if exercise_count <= 0:
        return 0

    feasible = [
        group_count
        for group_count in range(min_groups, max_groups + 1)
        if min_group_size * group_count <= exercise_count <= max_group_size * group_count
    ]
    if feasible:
        return min(
            feasible,
            key=lambda group_count: (
                abs((exercise_count / group_count) - target_group_size),
                -group_count,
            ),
        )

    if exercise_count < min_groups * min_group_size:
        return max(1, exercise_count // min_group_size)

    return max_groups


def trim_grouping_pool(
    exercises: list[dict],
    *,
    max_group_size: int = DEFAULT_MAX_GROUP_SIZE,
    max_groups: int = DEFAULT_MAX_GROUP_COUNT,
) -> list[dict]:
    max_pool_size = max_group_size * max_groups
    return exercises[:max_pool_size]


def build_similarity_groups(
    exercises: list[dict],
    *,
    priorities: list[float] | None = None,
    min_group_size: int = DEFAULT_MIN_GROUP_SIZE,
    max_group_size: int = DEFAULT_MAX_GROUP_SIZE,
    min_groups: int = DEFAULT_MIN_GROUP_COUNT,
    max_groups: int = DEFAULT_MAX_GROUP_COUNT,
) -> list[dict]:
    if not exercises:
        return []

    trimmed = trim_grouping_pool(
        exercises,
        max_group_size=max_group_size,
        max_groups=max_groups,
    )
    vectors = [exercise_tissue_vector(exercise) for exercise in trimmed]
    priorities = priorities[: len(trimmed)] if priorities is not None else None

    group_count = choose_group_count(
        len(trimmed),
        min_group_size=min_group_size,
        max_group_size=max_group_size,
        min_groups=min_groups,
        max_groups=max_groups,
    )
    if group_count <= 1:
        return [{
            "group_id": "group-1",
            "medoid_index": 0,
            "exercise_indices": list(range(len(trimmed))),
            "exercises": list(trimmed),
            "profile": combine_tissue_vectors(trimmed),
        }]

    similarity = np.zeros((len(trimmed), len(trimmed)), dtype=float)
    for i, left in enumerate(vectors):
        for j, right in enumerate(vectors):
            if i == j:
                similarity[i, j] = 1.0
            elif j > i:
                score = weighted_jaccard_similarity(left, right)
                similarity[i, j] = score
                similarity[j, i] = score

    assignment = _solve_similarity_grouping(
        similarity,
        group_count=group_count,
        min_group_size=min_group_size,
        max_group_size=max_group_size,
    )
    groups = []
    for group_index, member_indices in enumerate(assignment):
        medoid_index = max(
            member_indices,
            key=lambda idx: (
                sum(similarity[idx, peer] for peer in member_indices),
                priorities[idx] if priorities is not None else 0.0,
            ),
        )
        sorted_members = sorted(
            member_indices,
            key=lambda idx: (
                -similarity[medoid_index, idx],
                -priorities[idx] if priorities is not None else 0.0,
                str(trimmed[idx].get("name") or trimmed[idx].get("exercise_name") or ""),
            ),
        )
        group_exercises = [trimmed[idx] for idx in sorted_members]
        groups.append({
            "group_id": f"group-{group_index + 1}",
            "medoid_index": medoid_index,
            "exercise_indices": sorted_members,
            "exercises": group_exercises,
            "profile": combine_tissue_vectors(group_exercises),
        })

    groups.sort(
        key=lambda group: (
            -sum(priorities[idx] for idx in group["exercise_indices"]) if priorities is not None else 0.0,
            len(group["exercise_indices"]),
        ),
    )
    for index, group in enumerate(groups, start=1):
        group["group_id"] = f"group-{index}"
    return groups


def similarity_to_group_profile(group_profile: dict[int, float], exercise: dict) -> float:
    return weighted_jaccard_similarity(group_profile, exercise_tissue_vector(exercise))


def _solve_similarity_grouping(
    similarity: np.ndarray,
    *,
    group_count: int,
    min_group_size: int,
    max_group_size: int,
) -> list[list[int]]:
    item_count = similarity.shape[0]
    x_var_count = item_count * item_count
    total_var_count = x_var_count + item_count

    def x_index(item_idx: int, group_idx: int) -> int:
        return item_idx * item_count + group_idx

    def y_index(group_idx: int) -> int:
        return x_var_count + group_idx

    rows: list[int] = []
    cols: list[int] = []
    data: list[float] = []
    lower_bounds: list[float] = []
    upper_bounds: list[float] = []
    row_index = 0

    def add_term(col_idx: int, value: float) -> None:
        rows.append(row_index)
        cols.append(col_idx)
        data.append(value)

    def finish_row(lower: float, upper: float) -> None:
        nonlocal row_index
        lower_bounds.append(lower)
        upper_bounds.append(upper)
        row_index += 1

    for item_idx in range(item_count):
        for group_idx in range(item_count):
            add_term(x_index(item_idx, group_idx), 1.0)
        finish_row(1.0, 1.0)

    for item_idx in range(item_count):
        for group_idx in range(item_count):
            add_term(x_index(item_idx, group_idx), 1.0)
            add_term(y_index(group_idx), -1.0)
            finish_row(-np.inf, 0.0)

    for group_idx in range(item_count):
        for item_idx in range(item_count):
            add_term(x_index(item_idx, group_idx), 1.0)
        add_term(y_index(group_idx), -float(max_group_size))
        finish_row(-np.inf, 0.0)

    for group_idx in range(item_count):
        for item_idx in range(item_count):
            add_term(x_index(item_idx, group_idx), 1.0)
        add_term(y_index(group_idx), -float(min_group_size))
        finish_row(0.0, np.inf)

    for group_idx in range(item_count):
        add_term(x_index(group_idx, group_idx), 1.0)
        add_term(y_index(group_idx), -1.0)
        finish_row(0.0, 0.0)

    for group_idx in range(item_count):
        add_term(y_index(group_idx), 1.0)
    finish_row(float(group_count), float(group_count))

    constraint_matrix = sparse.csr_array(
        (data, (rows, cols)),
        shape=(row_index, total_var_count),
    )
    objective = np.zeros(total_var_count, dtype=float)
    objective[:x_var_count] = -similarity.reshape(-1)

    result = milp(
        c=objective,
        constraints=LinearConstraint(
            constraint_matrix,
            np.asarray(lower_bounds, dtype=float),
            np.asarray(upper_bounds, dtype=float),
        ),
        bounds=Bounds(lb=np.zeros(total_var_count), ub=np.ones(total_var_count)),
        integrality=np.ones(total_var_count, dtype=int),
    )
    if not result.success or result.x is None:
        raise RuntimeError(
            f"Exercise grouping optimizer failed: {getattr(result, 'message', 'unknown error')}"
        )

    assignment_matrix = result.x[:x_var_count].reshape(item_count, item_count)
    selected_groups = [
        group_idx
        for group_idx, value in enumerate(result.x[x_var_count:])
        if round(float(value)) == 1
    ]
    groups: dict[int, list[int]] = {group_idx: [] for group_idx in selected_groups}
    for item_idx in range(item_count):
        assigned_group = max(
            selected_groups,
            key=lambda group_idx: assignment_matrix[item_idx, group_idx],
        )
        groups[assigned_group].append(item_idx)

    return list(groups.values())
