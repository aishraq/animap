"""Check how good the stage 2 vectors are at finding similar shows.

Usage:
    uv run evaluate.py
    uv run evaluate.py "Frieren" "Cowboy Bebop"

Prints the nearest neighbours for a few known shows (from data/vectors.npz),
then scores the vectors against AniList's user recommendations: for each
series, what share of the shows users recommend for it appear in our 20
nearest neighbours. The same score is repeated with each feature block
removed, to show what each one adds.

Recommendations are also an ingredient of the vectors, so the score uses only
a fixed held-out fifth of recommendation pairs, and rebuilds the vectors with
those pairs removed. That keeps the benchmark honest at any recommendation
weight.
"""

import json
import sys
from pathlib import Path

import numpy as np

import vectors

DATA_DIR = Path(__file__).parent / "data"
K = 20
DEFAULT_SHOWS = ["Frieren", "Cowboy Bebop", "Attack on Titan", "K-On", "Steins;Gate", "Haikyu", "Mushishi", "Toradora"]


def neighbours(X, k=K):
    unit = X / np.linalg.norm(X, axis=1, keepdims=True)
    sims = unit @ unit.T
    np.fill_diagonal(sims, -np.inf)
    order = np.argsort(-sims, axis=1)[:, :k]
    return order, np.take_along_axis(sims, order, axis=1)


def held_out_targets(series):
    """For each series, the held-out shows users recommend alongside it."""
    targets = [set() for _ in series]
    for a, b in vectors.recommendation_edges(series):
        if vectors.is_held_out(series, (a, b)):
            targets[a].add(b)
            targets[b].add(a)
    return targets


def recall(order, targets):
    scores = [len(t & set(order[i])) / len(t) for i, t in enumerate(targets) if t]
    return np.mean(scores), len(scores)


def find(series, query):
    query = query.lower()
    exact = [i for i, s in enumerate(series) if any(query == t.lower() for t in s["titles"])]
    matches = exact or [i for i, s in enumerate(series) if any(query in t.lower() for t in s["titles"])]
    return max(matches, key=lambda i: series[i]["popularity"]) if matches else None


def main():
    X = np.load(DATA_DIR / "vectors.npz")["X"]
    names = np.load(DATA_DIR / "vectors.npz")["feature_names"]
    with open(DATA_DIR / "series.json") as f:
        series = json.load(f)

    order, sims = neighbours(X)
    shows = sys.argv[1:] or DEFAULT_SHOWS
    for query in shows:
        i = find(series, query)
        if i is None:
            print(f"\n{query}: not found")
            continue
        s = series[i]
        print(f"\n{s['title']} ({s['year']}, {', '.join(s['studios'][:2])}) | {', '.join(s['top_tags'][:4])}")
        for j, sim in zip(order[i][:10], sims[i][:10]):
            print(f"  {sim:.2f}  {series[j]['title']}")

    score_held_out(verbose=True)


def score_held_out(verbose=False):
    """Rebuild without the held-out pairs and score on them."""
    X, names, series, _ = vectors.build(vectors.DATA_DIR / "sample.db", verbose=False, holdout=True)
    targets = held_out_targets(series)
    order, _ = neighbours(X)
    score, n = recall(order, targets)
    if verbose:
        print(f"\nShare of held-out user recommendations found in our top {K}: {score:.1%} (over {n} series; random would be about {K / len(series):.1%})")
        for block in sorted({name.split(":")[0] for name in names}):
            mask = np.array([not name.startswith(block + ":") for name in names])
            ablated, _ = neighbours(X[:, mask])
            without, _ = recall(ablated, targets)
            print(f"  without {block:<8} {without:.1%}  ({without - score:+.1%})")
    return score


if __name__ == "__main__":
    main()
