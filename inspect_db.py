"""Print a quick profile of a fetched database.

Usage:
    uv run inspect_db.py data/sample.db
"""

import json
import sqlite3
import sys
from collections import Counter

import numpy as np


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "data/sample.db"
    conn = sqlite3.connect(path)
    media = [json.loads(row[0]) for row in conn.execute("SELECT json FROM media")]
    print(f"{len(media)} entries in {path}\n")

    print("Null or empty counts")
    for field in ["idMal", "episodes", "duration", "seasonYear", "averageScore", "meanScore", "genres", "tags"]:
        missing = sum(1 for m in media if not m.get(field))
        print(f"  {field:<14} {missing}")
    no_year = sum(1 for m in media if not (m.get("seasonYear") or m["startDate"]["year"]))
    print(f"  {'any year':<14} {no_year}")
    no_studio = sum(1 for m in media if not any(e["isMain"] and e["node"]["isAnimationStudio"] for e in m["studios"]["edges"]))
    print(f"  {'main anim studio':<14} {no_studio}\n")

    print("Formats:", dict(Counter(m["format"] for m in media).most_common()))
    print("Sources:", dict(Counter(m["source"] for m in media).most_common()), "\n")

    tag_counts = [len(m["tags"]) for m in media]
    ranks = np.array([t["rank"] for m in media for t in m["tags"]])
    spoilers = sum(1 for m in media for t in m["tags"] if t["isMediaSpoiler"] or t["isGeneralSpoiler"])
    print(f"Tags per entry: median {np.median(tag_counts):.0f}, min {min(tag_counts)}, max {max(tag_counts)}")
    print(f"Tag rank percentiles (10/25/50/75/90): {np.percentile(ranks, [10, 25, 50, 75, 90]).round().tolist()}")
    print(f"Spoiler tags: {spoilers} of {len(ranks)}")
    tag_categories = Counter(t["category"] for m in media for t in m["tags"])
    print("Tag categories:", dict(tag_categories.most_common(12)))
    tags = Counter(t["name"] for m in media for t in m["tags"])
    print(f"Distinct tags: {len(tags)}; top 15:", [name for name, _ in tags.most_common(15)], "\n")

    genres = Counter(g for m in media for g in m["genres"])
    print("Genres:", dict(genres.most_common()), "\n")

    studio_main = Counter(e["node"]["name"] for m in media for e in m["studios"]["edges"] if e["isMain"] and e["node"]["isAnimationStudio"])
    edges_per = [len(m["studios"]["edges"]) for m in media]
    print(f"Studio edges per entry: median {np.median(edges_per):.0f}, max {max(edges_per)}")
    print("Top main animation studios:", studio_main.most_common(12), "\n")

    roles = Counter(e["role"] for m in media for e in m["staff"]["edges"])
    print(f"Distinct staff role strings: {len(roles)}; top 30:")
    for role, n in roles.most_common(30):
        print(f"  {n:>4}  {role}")
    print()

    relations = Counter(e["relationType"] for m in media for e in m["relations"]["edges"])
    print("Relation types:", dict(relations.most_common()))
    anime_rel = Counter(e["relationType"] for m in media for e in m["relations"]["edges"] if e["node"]["type"] == "ANIME")
    print("Relation types to other anime:", dict(anime_rel.most_common()), "\n")

    pop = np.array([m["popularity"] for m in media])
    print(f"Popularity range: {pop.min()} to {pop.max()}")
    print("Top 10:", [m["title"]["english"] or m["title"]["romaji"] for m in sorted(media, key=lambda m: -m["popularity"])[:10]])


if __name__ == "__main__":
    main()
