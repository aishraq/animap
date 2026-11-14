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
import os
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

# Layout settings. With 15 neighbours and min_dist 0.1, sports, music and mecha
# floated off as far islands. 50 neighbours weighs global structure more and
# min_dist 0.6 spreads points out, giving one continuous map where clusters
# still stand apart. Both can be overridden with environment variables.
UMAP_NEIGHBORS = int(os.environ.get("UMAP_NEIGHBORS", 50))
UMAP_MIN_DIST = float(os.environ.get("UMAP_MIN_DIST", 0.6))

# Tags that describe the cast or the production rather than what a show is
# about make poor cluster names ("Male Protagonist", "CGI"), so labels skip them.
LABEL_SKIP_CATEGORIES = {"Cast-Main Cast", "Technical", "Demographic"}
LABEL_SKIP_TAGS = {"Heterosexual", "Urban", "Nudity", "Kuudere", "Tsundere", "Primarily Male Cast", "Primarily Female Cast"}

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)


def cluster_labels(X, names, clusters, categories):
    """Name each cluster after the tags most over-represented in it."""
    def usable(tag):
        return tag not in LABEL_SKIP_TAGS and categories.get(tag) not in LABEL_SKIP_CATEGORIES

    tag_cols = np.array([n.startswith("tags:") and usable(n.removeprefix("tags:")) for n in names])
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


def match_scores(sims, neighbour_sims):
    """Turn raw cosine similarities into a friendlier 0-100 match score.

    Raw similarities run low (a typical best match is about 0.49), which reads
    as a weak recommendation. The scale is anchored on the data instead: a
    typical unrelated pair (the median of all pairs) is 0, and the closest 5%
    of best matches are 100. A square root spreads out the middle, so a typical
    best match reads in the 80s and a 20th match in the 60s.
    """
    off_diagonal = sims[np.triu_indices(len(sims), 1)]
    low = np.median(off_diagonal)
    high = np.percentile(neighbour_sims[:, 0], 95)
    scaled = np.clip((neighbour_sims - low) / (high - low), 0, 1)
    return np.round(100 * np.sqrt(scaled)).astype(int)


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
    match = match_scores(sims, neighbour_sims)

    n_components = min(PCA_COMPONENTS, unit.shape[0] - 1, unit.shape[1])
    reduced = PCA(n_components=n_components, random_state=SEED).fit_transform(unit)
    print(f"PCA to {n_components} components")

    xy = umap.UMAP(n_neighbors=UMAP_NEIGHBORS, min_dist=UMAP_MIN_DIST, random_state=SEED).fit_transform(reduced)
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

    with open(DATA_DIR / "tag_categories.json") as f:
        categories = json.load(f)
    labels = cluster_labels(X, names, clusters, categories)
    cluster_info = []
    for c in sorted(labels):
        members = clusters == c
        cluster_info.append({
            "id": int(c),
            "label": ", ".join(labels[c]),
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
            "native": s["native"],
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
            "nnMatch": match[i].tolist(),
            "synopsis": s["synopsis"],
        })

    payload = json.dumps({"clusters": cluster_info, "points": points}, ensure_ascii=False, separators=(",", ":"))
    (ROOT / "animap.json").write_text(payload)
    (ROOT / "animap.js").write_text(f"window.ANIMAP = {payload};\n")
    print(f"wrote animap.json and animap.js ({len(payload) / 1e6:.1f} MB, {len(points)} points)")
    for c in cluster_info:
        print(f"  {c['size']:>3}  {c['label']}")


if __name__ == "__main__":
    main()
