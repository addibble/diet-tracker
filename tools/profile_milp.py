"""Profile MILP grouping solver at different exercise counts."""
import time

import numpy as np
from scipy import sparse
from scipy.optimize import Bounds, LinearConstraint, milp


def profile_milp(n: int, group_count: int | None = None) -> tuple[float, bool]:
    sim = np.random.rand(n, n)
    sim = (sim + sim.T) / 2
    np.fill_diagonal(sim, 1.0)

    if group_count is None:
        group_count = max(2, n // 5)
    min_gs, max_gs = 2, 6

    x_var_count = n * n
    total = x_var_count + n

    rows, cols, data = [], [], []
    lb, ub = [], []
    row_index = [0]

    def add(c, v):
        rows.append(row_index[0])
        cols.append(c)
        data.append(v)

    def fin(lo, hi):
        lb.append(lo)
        ub.append(hi)
        row_index[0] += 1

    for i in range(n):
        for g in range(n):
            add(i * n + g, 1.0)
        fin(1.0, 1.0)

    for i in range(n):
        for g in range(n):
            add(i * n + g, 1.0)
            add(x_var_count + g, -1.0)
            fin(-np.inf, 0.0)

    for g in range(n):
        for i in range(n):
            add(i * n + g, 1.0)
        add(x_var_count + g, -float(max_gs))
        fin(-np.inf, 0.0)

    for g in range(n):
        for i in range(n):
            add(i * n + g, 1.0)
        add(x_var_count + g, -float(min_gs))
        fin(0.0, np.inf)

    for g in range(n):
        add(g * n + g, 1.0)
        add(x_var_count + g, -1.0)
        fin(0.0, 0.0)

    for g in range(n):
        add(x_var_count + g, 1.0)
    fin(float(group_count), float(group_count))

    ri = row_index[0]
    mat = sparse.csr_array((data, (rows, cols)), shape=(ri, total))
    obj = np.zeros(total)
    obj[:x_var_count] = -sim.reshape(-1)

    t0 = time.perf_counter()
    result = milp(
        c=obj,
        constraints=LinearConstraint(mat, np.array(lb), np.array(ub)),
        bounds=Bounds(lb=np.zeros(total), ub=np.ones(total)),
        integrality=np.ones(total, dtype=int),
    )
    elapsed = time.perf_counter() - t0
    print(
        "n=%3d: %d vars, %d constraints, %d groups => %.2fs (%s)"
        % (n, total, ri, group_count, elapsed, "OK" if result.success else "FAIL")
    )
    return elapsed, result.success


if __name__ == "__main__":
    for n in [20, 40, 69, 100, 150]:
        profile_milp(n)
