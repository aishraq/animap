# Animap

Animap is an interactive 2D map of anime, where shows that are similar sit close together. It follows the approach of Mikey Guinness's [movie map](https://mikeyguinness.com/projects/movie-map/), using AniList as the data source instead of TMDB.

The pipeline is three Python scripts and one HTML page. Dependencies are managed with uv; run `uv sync` once.

## Stage 1: fetch

`fetch.py` pulls anime metadata from the AniList GraphQL API and caches the raw JSON for each entry in SQLite. Adult titles are excluded at query time; everything else is kept, and filtering happens later.

```
uv run fetch.py --sample 3000  # the top 3,000 by popularity into data/sample.db (what the map uses)
uv run fetch.py                # full catalogue into data/animap.db
uv run inspect_db.py data/animap.db
```

AniList currently limits clients to 30 requests a minute (normally 90), so the script defaults to 28 and also watches the rate limit headers. A 429 waits out the `Retry-After` period, and server or network errors back off exponentially. The full fetch walks pages sorted by ID, so page boundaries never move. Each page is written in the same transaction as the progress marker, which means you can stop it with Ctrl-C at any time and rerun the command to continue. `--reset` starts from page one again. At 50 entries per page the full catalogue is a few hundred requests, which takes around a quarter of an hour.

## Stage 2: vectors

`vectors.py` collapses franchises into single series, using sequel, prequel, side story, summary, compilation and alternative-version relations. It then keeps the top 1,000 series, ranked by a blend of popularity, favourites and score. It then builds one weighted vector per series and writes `data/vectors.npz` plus `data/series.json`, which holds titles, synonyms, cover, tags and AniList recommendations for each series. Each feature block (tags, genres, studios, key staff, format and numeric fields) is TF-IDF weighted and pooled across the series' entries by popularity. The block is then L2-normalised and scaled by its weight, which is set at the top of the script.

```
uv run vectors.py                 # data/sample.db by default
uv run evaluate.py                # neighbours for a few known shows plus a score
uv run evaluate.py "Mob Psycho" "Monster"
```

`evaluate.py` scores the vectors against AniList's own user recommendations. For each series, it measures what share of the shows users recommend appear in our 20 nearest neighbours. It also reports what the score would be with each block removed.

## Stage 3: layout and clusters

`reduce.py` computes the 20 most similar series for every series using cosine similarity on the full vectors. It reduces the vectors to 50 dimensions with PCA and lays them out in 2D with UMAP. It assigns clusters with HDBSCAN, run on a separate 10-dimensional UMAP embedding, because in 50 dimensions the points are too evenly spread for density clustering to find structure. Each cluster is named after its most over-represented tags. The output is `animap.json`, plus `animap.js`, which holds the same data wrapped in a script tag so the page works when opened straight from disk.

```
uv run reduce.py
```

## Stage 4: the map

Open `index.html` in a browser. No server or build step is needed. Type any title into the search box: English, romaji, an abbreviation like AoT, a season title or a misspelling all work. Pick a show to see its 20 most similar shows in the side panel, with lines to them on the map, and click any of them to keep exploring. Scroll or pinch to zoom, drag to pan, and press `/` to jump to search.

To rebuild everything after a change: `uv run vectors.py && uv run reduce.py`, then reload the page.
