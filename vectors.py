"""Stage 2: turn fetched AniList entries into one weighted feature vector per series.

Usage:
    uv run vectors.py                     # reads data/sample.db
    uv run vectors.py data/animap.db

Writes data/vectors.npz (the matrix and feature names) and data/series.json
(per-series metadata for search, tooltips and evaluation).

Franchises are collapsed first: entries joined by sequel, prequel, side story
and similar relations become one series. The map then keeps the top
TARGET_SERIES series by a blend of popularity, favourites and score. Each block of features is TF-IDF
weighted, pooled across the series' entries by popularity, L2-normalised and
then scaled by its block weight, so with cosine similarity a block contributes
roughly in proportion to its weight squared.
"""

import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.stats import rankdata

DATA_DIR = Path(__file__).parent / "data"

BLOCK_WEIGHTS = {
    "tags": 1.0,
    "genres": 0.8,
    "studios": 0.5,
    "staff": 0.35,
    "format": 0.15,
    "numeric": 0.15,
}

# Relations that mean "same story, different instalment". CHARACTER, OTHER,
# ADAPTATION and SOURCE are left out because they chain unrelated franchises.
# SPIN_OFF and its reverse, PARENT, are left out too: AniList marks every
# Gundam series as a spin-off of the 1979 original, which would merge Witch from
# Mercury, Iron-Blooded Orphans, Wing and others into one point. Side stories
# still join through SIDE_STORY, which the main entry lists.
FRANCHISE_RELATIONS = {"SEQUEL", "PREQUEL", "SIDE_STORY", "SUMMARY", "COMPILATION", "ALTERNATIVE"}

# Which series make the map: rank every series by a blend of percentile ranks
# and keep the top TARGET_SERIES. Popularity keeps the shows people know;
# favourites and score swap low-rated filler for acclaimed shows.
TARGET_SERIES = 1000
SELECTION_WEIGHTS = {"popularity": 0.6, "favourites": 0.2, "score": 0.2}

# AniList's audience is young and has grown over time, so raw popularity and
# favourites undercount older shows. ERA_MIX blends in each series' rank within
# its own decade (1980s and earlier, 1990s, 2000s, 2010s, 2020s); 0 turns it off.
ERA_MIX = 0.5

DROP_FORMATS = {"MUSIC"}

# Broad cast descriptors (Male Protagonist, Primarily Teen Cast) say little about
# what a show is like, so they count for less than theme and setting tags.
TAG_CATEGORY_WEIGHTS = {"Cast-Main Cast": 0.5, "Cast-Traits": 0.5, "Technical": 0.5}

# Individual tags that are broad enough to pull unrelated shows together, as
# {name: multiplier}. Tried a list of 15 (Heterosexual, Urban, Work...) at 0.3:
# it didn't beat TAG_RANK_POWER alone, so it's empty but kept as a knob.
GENERIC_TAGS = {}

# Squaring the rank makes the tags that define a show (rank 90: 0.81) count for
# far more than incidental ones (rank 40: 0.16). On the 1,000 series this lifted
# the recommendation score from 39.6% to 40.5% and fixed Space Brothers matching
# workplace comedies ahead of Planetes. A minimum rank cut-off didn't help.
TAG_MIN_RANK = 0
TAG_RANK_POWER = 2.0

STAFF_ROLES = {"Director", "Chief Director", "Series Director", "Series Composition", "Script", "Music"}

MIN_SERIES = {"tags": 3, "studios": 2, "staff": 2}


def load_media(db_path):
    conn = sqlite3.connect(db_path)
    media = [json.loads(row[0]) for row in conn.execute("SELECT json FROM media")]
    return [m for m in media if m["format"] not in DROP_FORMATS]


def group_franchises(media):
    # Entries outside the fetched pool become extra graph nodes, so a franchise
    # still joins up when the link between two seasons runs through an OVA or
    # special we didn't fetch. Those nodes are dropped again after grouping.
    index = {m["id"]: i for i, m in enumerate(media)}
    rows, cols = [], []
    for m in media:
        for edge in m["relations"]["edges"]:
            target = edge["node"]["id"]
            if edge["relationType"] in FRANCHISE_RELATIONS and edge["node"]["type"] == "ANIME":
                if target not in index:
                    index[target] = len(index)
                rows.append(index[m["id"]])
                cols.append(index[target])
    graph = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(index), len(index)))
    _, labels = connected_components(graph, directed=False)

    groups = defaultdict(list)
    for m, label in zip(media, labels[:len(media)]):
        groups[label].append(m)
    return [sorted(members, key=lambda m: -m["popularity"]) for members in groups.values()]


def select_series(series, target):
    """Keep the top series by the blend in SELECTION_WEIGHTS."""
    popularity = np.array([members[0]["popularity"] for members in series], dtype=float)
    favourites = np.array([max(m["favourites"] for m in members) for members in series], dtype=float)
    score = np.array([series_score(members) or 0 for members in series], dtype=float)
    years = np.array([series_year(members) or 2030 for members in series])
    era = np.select([years < 1990, years < 2000, years < 2010, years < 2020], [1980, 1990, 2000, 2010], 2020)

    def overall(values):
        return rankdata(values) / len(values)

    def within_era(values):
        ranks = np.zeros(len(values))
        for e in np.unique(era):
            members = era == e
            ranks[members] = rankdata(values[members]) / members.sum()
        return ranks

    def era_adjusted(values):
        return (1 - ERA_MIX) * overall(values) + ERA_MIX * within_era(values)

    blend = (
        SELECTION_WEIGHTS["popularity"] * era_adjusted(popularity)
        + SELECTION_WEIGHTS["favourites"] * era_adjusted(favourites)
        + SELECTION_WEIGHTS["score"] * overall(score)
    )
    keep = np.argsort(-blend)[:target]
    return [series[i] for i in sorted(keep, key=lambda i: -popularity[i])]


def series_year(members):
    years = [m["seasonYear"] or m["startDate"]["year"] for m in members]
    years = [y for y in years if y]
    return min(years) if years else None


def series_score(members):
    """Popularity-weighted average score, or None if no entry has one."""
    scored = [(m["averageScore"], m["popularity"]) for m in members if m["averageScore"]]
    if not scored:
        return None
    return sum(s * p for s, p in scored) / sum(p for _, p in scored)


def entry_features(m):
    """Raw (feature -> value) dicts for one entry, before vocabulary filtering."""
    tags = {}
    for t in m["tags"]:
        if t["isMediaSpoiler"] or t["isGeneralSpoiler"]:
            continue
        if t["rank"] < TAG_MIN_RANK:
            continue
        weight = TAG_CATEGORY_WEIGHTS.get(t["category"], 1.0) * GENERIC_TAGS.get(t["name"], 1.0)
        tags[t["name"]] = (t["rank"] / 100) ** TAG_RANK_POWER * weight

    genres = {g: 1.0 for g in m["genres"]}

    studios = {}
    for edge in m["studios"]["edges"]:
        if edge["isMain"] and edge["node"]["isAnimationStudio"]:
            studios[edge["node"]["name"]] = 1.0

    staff = {}
    for edge in m["staff"]["edges"]:
        # Role strings look like "Director" or "Script (eps 1-3)"; episode-level
        # credits are too granular to say much about the show as a whole.
        role = edge["role"]
        if role in STAFF_ROLES:
            staff[edge["node"]["name"]["full"]] = 1.0

    fmt = {m["format"]: 1.0} if m["format"] else {}
    return {"tags": tags, "genres": genres, "studios": studios, "staff": staff, "format": fmt}


def pool(members, block):
    """Popularity-weighted mean of one block across a series' entries."""
    weights = np.array([max(m["popularity"], 1) for m in members], dtype=float)
    weights /= weights.sum()
    pooled = Counter()
    for m, w in zip(members, weights):
        for name, value in m["_features"][block].items():
            pooled[name] += w * value
    return pooled


def build_block(series, block, min_series):
    pooled = [pool(members, block) for members in series]
    doc_freq = Counter(name for p in pooled for name in p)
    vocab = sorted(name for name, n in doc_freq.items() if n >= min_series)
    column = {name: j for j, name in enumerate(vocab)}

    matrix = np.zeros((len(series), len(vocab)), dtype=np.float32)
    for i, p in enumerate(pooled):
        for name, value in p.items():
            if name in column:
                matrix[i, column[name]] = value

    idf = np.log((1 + len(series)) / (1 + np.array([doc_freq[v] for v in vocab]))) + 1
    matrix *= idf.astype(np.float32)
    return matrix, [f"{block}:{v}" for v in vocab]


def numeric_block(series):
    rows = []
    for members in series:
        years = [m["seasonYear"] or m["startDate"]["year"] for m in members]
        years = [y for y in years if y]
        episodes = sum(m["episodes"] or 0 for m in members)
        score = series_score(members)
        score = np.nan if score is None else score
        rows.append([min(years) if years else np.nan, np.log1p(episodes), np.log1p(members[0]["popularity"]), score])

    matrix = np.array(rows, dtype=np.float64)
    mean = np.nanmean(matrix, axis=0)
    std = np.nanstd(matrix, axis=0)
    matrix = (matrix - mean) / np.where(std > 0, std, 1)
    matrix = np.nan_to_num(matrix, nan=0.0)
    # Spread the block's weight across its columns so it behaves like the others.
    matrix /= np.sqrt(matrix.shape[1])
    return matrix.astype(np.float32), ["numeric:year", "numeric:log_episodes", "numeric:log_popularity", "numeric:score"]


def l2_rows(matrix):
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms > 0, norms, 1)


def series_metadata(members, top_tags):
    main = members[0]
    titles = set()
    for m in members:
        titles.update(t for t in m["title"].values() if t)
        titles.update(m.get("synonyms") or [])
    years = [m["seasonYear"] or m["startDate"]["year"] for m in members]
    years = [y for y in years if y]
    return {
        "id": main["id"],
        "title": main["title"]["english"] or main["title"]["romaji"],
        "romaji": main["title"]["romaji"],
        "titles": sorted(titles),
        "members": [m["id"] for m in members],
        "member_titles": [m["title"]["english"] or m["title"]["romaji"] for m in members],
        "year": min(years) if years else None,
        "format": main["format"],
        "episodes": sum(m["episodes"] or 0 for m in members),
        "score": round(series_score(members)) if series_score(members) else None,
        "popularity": main["popularity"],
        "genres": main["genres"],
        "top_tags": top_tags,
        "studios": [e["node"]["name"] for e in main["studios"]["edges"] if e["isMain"] and e["node"]["isAnimationStudio"]],
        "cover": main["coverImage"]["medium"],
        "color": main["coverImage"]["color"],
        "url": main["siteUrl"],
        "recommendations": [
            {"id": r["mediaRecommendation"]["id"], "rating": r["rating"]}
            for m in members
            for r in (m.get("recommendations") or {}).get("nodes", [])
            if r["mediaRecommendation"]
        ],
    }


def build(db_path, verbose=True):
    """Return the weighted matrix, its feature names and the selected series."""
    say = print if verbose else (lambda *a, **k: None)
    media = load_media(db_path)
    for m in media:
        m["_features"] = entry_features(m)

    series = group_franchises(media)
    say(f"{len(media)} entries collapsed into {len(series)} series")
    series = select_series(series, TARGET_SERIES)
    say(f"kept the top {len(series)} by {SELECTION_WEIGHTS}, era mix {ERA_MIX}")
    for members in sorted(series, key=len, reverse=True)[:8]:
        say(f"  {len(members):>3}  {members[0]['title']['romaji']}")

    blocks, names = [], []
    for block in ["tags", "genres", "studios", "staff", "format"]:
        matrix, feature_names = build_block(series, block, MIN_SERIES.get(block, 1))
        blocks.append(l2_rows(matrix) * BLOCK_WEIGHTS[block])
        names += feature_names
        say(f"{block:<8} {matrix.shape[1]:>5} features")
    numeric, numeric_names = numeric_block(series)
    blocks.append(numeric * BLOCK_WEIGHTS["numeric"])
    names += numeric_names

    X = np.hstack(blocks)
    say(f"vector matrix {X.shape}")
    return X, names, series, media


def main():
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA_DIR / "sample.db"
    X, names, series, media = build(db_path)

    tag_count = sum(1 for n in names if n.startswith("tags:"))
    metadata = []
    for i, members in enumerate(series):
        tag_values = X[i, :tag_count]
        top = [names[j].removeprefix("tags:") for j in np.argsort(-tag_values)[:5] if tag_values[j] > 0]
        metadata.append(series_metadata(members, top))

    np.savez_compressed(DATA_DIR / "vectors.npz", X=X, feature_names=np.array(names))
    with open(DATA_DIR / "series.json", "w") as f:
        json.dump(metadata, f, ensure_ascii=False)
    categories = {t["name"]: t["category"] for m in media for t in m["tags"]}
    with open(DATA_DIR / "tag_categories.json", "w") as f:
        json.dump(categories, f, ensure_ascii=False)
    print(f"wrote {DATA_DIR / 'vectors.npz'} and {DATA_DIR / 'series.json'}")


if __name__ == "__main__":
    main()
