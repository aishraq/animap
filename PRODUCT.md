# Animap: product spec

Animap is an interactive 2D map of anime in which similar shows sit close together. Its main job is discovery: a viewer types the name of an anime they already like, finds it even if they spell it loosely or use a different title, and sees which shows are most similar so they can pick something new to watch. The map gives that list a visual context, letting them explore the wider neighbourhood. It is modelled on Mikey Guinness's movie map and uses AniList metadata.

Search has to be forgiving because anime go by many names: the romaji title (Shingeki no Kyojin), the English title (Attack on Titan), abbreviations (AoT, SnK), romanisation variants (ou, oo and ō), and the titles of individual seasons and movies. Every one of those names should lead to the right series. The quality of the similar-shows list matters more than the look of the map.

Each point is one series. Franchises are collapsed through AniList's relations, so seasons, movies and side stories of the same show form a single point instead of a tight clump. Similarity is driven mainly by themes (tag rank scores) and genres, with studios and key staff (director, writer, composer) carrying meaningful weight, since studio identity matters more in anime than in film. Numeric fields such as year, episode count, popularity and score contribute only lightly.

The map is a single static HTML file drawn on a Canvas, with no server and no build step. It supports pan and zoom, hover tooltips, title search, cluster colouring and a panel listing the 20 most similar shows for any selected point.

The pipeline is deliberately small: a fetch script that caches AniList data in SQLite, a vector script that builds weighted feature vectors, and a reduce script that runs PCA and UMAP for layout, HDBSCAN for clusters and a nearest-neighbour search, then exports one JSON file for the front end.
