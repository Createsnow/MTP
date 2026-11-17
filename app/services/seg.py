"""流水号段的区间运算（闭区间，升序、不重叠）：生成排号、空号段用。"""

from __future__ import annotations

Seg = tuple[int, int]

#: 「最大号之后」的上界（区间运算用，不是流水上限）
INF = 10**18


def merge(segs: list[Seg] | list[list[int]]) -> list[Seg]:
    out: list[list[int]] = []
    for a, b in sorted((int(s), int(e)) for s, e in segs if s <= e):
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def intersect(xs: list[Seg], ys: list[Seg]) -> list[Seg]:
    xs, ys = merge(xs), merge(ys)
    out = []
    i = j = 0
    while i < len(xs) and j < len(ys):
        a, b = max(xs[i][0], ys[j][0]), min(xs[i][1], ys[j][1])
        if a <= b:
            out.append((a, b))
        if xs[i][1] < ys[j][1]:
            i += 1
        else:
            j += 1
    return out


def subtract(xs: list[Seg], ys: list[Seg]) -> list[Seg]:
    out = []
    ys = merge(ys)
    for a, b in merge(xs):
        pos = a
        for s, e in ys:
            if e < pos or s > b:
                continue
            if s > pos:
                out.append((pos, s - 1))
            pos = max(pos, e + 1)
            if pos > b:
                break
        if pos <= b:
            out.append((pos, b))
    return out


def total(segs: list[Seg]) -> int:
    return sum(b - a + 1 for a, b in segs)
