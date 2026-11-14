"""Stage 3: lay out the series in 2D, cluster them and export data for the map.

Usage:
    uv run reduce.py

Reads data/vectors.npz and data/series.json, writes animap.json and animap.js
next to index.html. The .js copy is the same data wrapped in a script so the
page works when opened straight from disk, without a local server.

Vectors are L2-normalised first, so Euclidean distance after PCA tracks the
cosine similarity used for the neighbour lists. HDBSCAN runs on a separate
10-dimensional UMAP embedding rather than on the PCA output directly: in 50
dimensions the points are too evenly spread for density clustering, and it
either merges almost everything or leaves half the shows unclustered.
"""

import json
import warnings
from pathlib import Path

import numpy as np
import umap
from sklearn.cluster import HDBSCAN
from sklearn.decomposition import PCA

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
K = 20
PCA_COMPONENTS = 50
SEED = 42

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)


def cluster_labels(X, names, clusters):
    """Name each cluster after the tags most over-represented in it."""
    tag_cols = np.array([n.startswith("tags:") for n in names])
    tags = X[:, tag_cols]
    tag_names = [n.removeprefix("tags:") for n in names[tag_cols]]
    present = tags > 0
    overall = present.mean(axis=0)
    labels = {}
    for c in sorted(set(clusters) - {-1}):
        inside = present[clusters == c].mean(axis=0)
        # Lift weighted by how common the tag is in the cluster, so a tag shared
        # by two members out of thirty doesn't win on lift alone.
        score = inside * np.log((inside + 1e-3) / (overall + 1e-3))
        labels[c] = [tag_names[j] for j in np.argsort(-score)[:3]]
    return labels


def main():
    vectors = np.load(DATA_DIR / "vectors.npz")
    X = vectors["X"]
    names = vectors["feature_names"]
    with open(DATA_DIR / "series.json") as f:
        series = json.load(f)

    unit = X / np.linalg.norm(X, axis=1, keepdims=True)

    sims = unit @ unit.T
    np.fill_diagonal(sims, -np.inf)
    neighbours = np.argsort(-sims, axis=1)[:, :K]
    neighbour_sims = np.take_along_axis(sims, neighbours, axis=1)

    n_components = min(PCA_COMPONENTS, unit.shape[0] - 1, unit.shape[1])
    reduced = PCA(n_components=n_components, random_state=SEED).fit_transform(unit)
    print(f"PCA to {n_components} components")

    xy = umap.UMAP(n_neighbors=15, min_dist=0.1, random_state=SEED).fit_transform(reduced)
    xy = (xy - xy.min(axis=0)) / (xy.max(axis=0) - xy.min(axis=0)).max() * 1000

    cluster_space = umap.UMAP(n_components=10, n_neighbors=15, min_dist=0.0, random_state=SEED).fit_transform(reduced)
    min_cluster_size = max(8, len(series) // 100)
    clusters = HDBSCAN(min_cluster_size=min_cluster_size, min_samples=3).fit_predict(cluster_space)
    n_clusters = len(set(clusters) - {-1})
    print(f"HDBSCAN: {n_clusters} clusters, {np.mean(clusters == -1):.0%} unclustered (min size {min_cluster_size})")

    # Unclustered shows take the most common cluster among their neighbours,
    # so the map can tint them toward the region they lean into.
    soft = clusters.copy()
    for i in np.where(clusters == -1)[0]:
        votes = clusters[neighbours[i]]
        votes = votes[votes >= 0]
        if len(votes):
            soft[i] = np.bincount(votes).argmax()

    labels = cluster_labels(X, names, clusters)
    cluster_info = []
    for c in sorted(labels):
        members = clusters == c
        cluster_info.append({
            "id": int(c),
            "label": " · ".join(labels[c]),
            "x": round(float(np.median(xy[members, 0])), 1),
            "y": round(float(np.median(xy[members, 1])), 1),
            "size": int(members.sum()),
        })

    points = []
    for i, s in enumerate(series):
        points.append({
            "id": s["id"],
            "title": s["title"],
            "romaji": s["romaji"],
            "titles": s["titles"],
            "parts": len(s["members"]),
            "x": round(float(xy[i, 0]), 1),
            "y": round(float(xy[i, 1]), 1),
            "cluster": int(clusters[i]),
            "soft": int(soft[i]),
            "year": s["year"],
            "format": s["format"],
            "episodes": s["episodes"],
            "score": s["score"],
            "popularity": s["popularity"],
            "genres": s["genres"],
            "tags": s["top_tags"],
            "studios": s["studios"],
            "cover": s["cover"],
            "url": s["url"],
            "nn": neighbours[i].tolist(),
            "nnSim": [round(float(v), 3) for v in neighbour_sims[i]],
        })

    payload = json.dumps({"clusters": cluster_info, "points": points}, ensure_ascii=False, separators=(",", ":"))
    (ROOT / "animap.json").write_text(payload)
    (ROOT / "animap.js").write_text(f"window.ANIMAP = {payload};\n")
    print(f"wrote animap.json and animap.js ({len(payload) / 1e6:.1f} MB, {len(points)} points)")
    for c in cluster_info:
        print(f"  {c['size']:>3}  {c['label']}")


if __name__ == "__main__":
    main()
