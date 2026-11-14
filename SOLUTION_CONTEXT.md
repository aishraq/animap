# Animap: solution context

## Current status and next step

Stage 1 (fetch) is written. At the user's request we are working with the 1,000 most popular anime (data/sample.db, fetched 14 Nov 2026) rather than the full catalogue for now. All four stages now work end to end on that set. Stage 2 turns the 1,000 entries into 682 series with 876 features. Stage 3 produces 31 clusters, with 15 per cent of series unclustered. Opening index.html gives a working map with fuzzy search, a similar-shows panel, hover tooltips, pan and zoom. It was checked in Chrome on 14 Nov 2026 with no console errors. The user decided on 14 Nov 2026 that a set of about 1,000 is enough and that no full catalogue fetch is needed. The set is now chosen as 1,000 series rather than 1,000 entries. They come from a pool of the top 3,000 entries by popularity (data/sample.db, fetched 14 Nov 2026), ranked by a blend of popularity, favourites and score, adjusted for era. The map has 1,000 points and 30 clusters. Tag weighting, cluster labels and the layout were tuned on 14 Nov 2026 (see the tuning log). Studio weight is deliberately left open at the user's request. Next is publishing on GitHub Pages, which is waiting on the user's confirmation of the repo name and public visibility.

## Design decisions

The fetch sorts by ID rather than popularity because ID order is stable while the job runs, which is what makes resume safe. The sample uses popularity order instead, so it contains recognisable shows, and it writes to its own database so it never touches the full run's progress.

Raw media JSON is stored as returned, with no parsing at fetch time. Interpretation (which studios count, which staff roles count, which relations define a franchise) happens in stage 2, so changing those choices never needs a refetch.

The studio query is unfiltered rather than `isMain: true`. Each edge carries `isMain` and `isAnimationStudio`, so stage 2 can filter to main animation studios while still having producers available if they turn out to be useful.

AniList's API is in a degraded state with a 30 requests per minute limit (confirmed from the docs and the `x-ratelimit-limit` header on 14 Nov 2026). The default pace is 28 a minute. Nested staff at 50 entries per page stays within the query complexity cap.

The user clarified on 14 Nov 2026 that the core use is discovery: fuzzy search for a show you know, then a list of similar shows to watch next. That makes neighbour quality the main tuning target and makes search a first-class feature. The fetch now also pulls AniList `synonyms` (909 of the top 1,000 have at least one, including abbreviations such as AoT, SnK and MHA). The search index will cover every title and synonym of every entry in a franchise, mapped to its series. Sample mode now clears the table before refetching, so it never leaves stale rows behind.

## Sample findings (200 most popular, 14 Nov 2026)

About 20 per cent of tag assignments are spoiler tags, which stage 2 will drop. Tag ranks are high in this sample (median 69), and the most common tags are generic cast descriptors such as Male Protagonist, Heterosexual and Primarily Teen Cast, so the Cast-Traits and Cast-Main Cast categories may need a lower weight than theme and setting tags. Director, Series Composition and Music appear for almost every entry within the top 25 staff by relevance. Studio edges average six per entry because producers are included; one entry had no main animation studio. Relations to other anime are dominated by SIDE_STORY, SEQUEL, CHARACTER and OTHER. The sample is biased toward complete, well-tagged entries, so null rates will be much higher in the full catalogue.

## 1,000-entry sample (14 Nov 2026)

The 1,000 most popular shows run down to a popularity of about 78k, so this is still the well-documented end of AniList: null rates stay tiny (5 missing season years, 3 missing episode counts, 10 without a main animation studio). Formats are 825 TV, 99 movies, 37 ONA, 25 OVA, 8 TV shorts and 6 specials. Spoiler tags are again about 18 per cent. Director, Series Composition and Music still dominate the staff roles, and Script now appears often enough to be worth counting as a writer role. A trial franchise collapse using the planned relation types turns the 1,000 entries into 682 series. The largest groups are Monogatari (13 entries), Attack on Titan and My Hero Academia (11 each), and Fate and Sword Art Online (9 each), which are all genuine franchises, so the relation set looks right at this scale.

## Stage 2 log (14 Nov 2026)

The fetch now also pulls up to 10 AniList user recommendations per entry, sorted by rating. Within the top 1,000, about 7,000 recommendations point to other shows in the set, which gives an independent benchmark. The score is the share of a series' user recommendations that appear in our 20 nearest neighbours, where random choice would give 2.9 per cent.

With the planned weights (tags 1.0, genres 0.8, studios 0.5, staff 0.35, format 0.15, numeric 0.15) the score is 45.5 per cent. Tags carry most of the signal, since removing them drops the score to 27.7 per cent. Genres add about 4 points. Studios and staff add almost nothing measurable at this scale, because few series in the top 1,000 share a director or composer.

A grid search found 49.1 per cent with genres 0.5, studios 0.3 and numeric 0.3. Heavier studio weight lowers the score: studios at 0.8 give about 36 per cent. The benchmark has its own bias, because users tend to recommend popular, thematically similar shows, and that is why more popularity weight in the numeric block helps it. So the score is a guide, not the target. Weights are unchanged pending the user's call on how much studio identity should count.

Neighbour lists read well for K-On, Haikyu, Toradora and Frieren. Two odd results stand out: Cowboy Bebop's top match is Gintama, and Steins;Gate's is Re:Zero, which shares a time loop and the studio White Fox.

## Stage 3 and 4 log (14 Nov 2026)

HDBSCAN on the 50-dimensional PCA output failed in both directions. The default eom selection gave one cluster of 620 series. Leaf selection, or fewer PCA components, left half the series as noise. Clustering on a separate 10-dimensional UMAP embedding (min_dist 0, eom, minimum cluster size 8) gave 31 sensible clusters with 15 per cent unclustered. This departs from the movie map's "cluster on full vectors", deliberately. The minimum cluster size scales as max(8, n/100) and will need checking at full scale. Unclustered series are tinted toward the most common cluster among their neighbours.

The neighbour lists come from the full vectors, not the 2D layout. So a show's similar shows can sit some distance apart on the map, and the page draws lines to make that visible.

Fuzzy search runs in the page with no library. It normalises titles (accents, case, punctuation, and the doubled vowels ou, oo, uu and ii), searches every title and synonym of every entry in a franchise, and scores matches by exact, prefix, substring, word-prefix and trigram similarity. Main titles get a bonus, and popularity breaks ties. It was tested against 20 queries, including typos (freiren, stiens gate, one pice), abbreviations (aot, snk, mha), romaji and season titles, and all found the right show first.

The page loads animap.js through a script tag rather than fetching JSON, so it works from file:// with no server.

## Selection log (14 Nov 2026)

The pool is the top 3,000 entries by AniList popularity, which is 60 requests and about two minutes. Selection happens locally, so the rule can change without a refetch. Series are ranked by percentile on popularity (weight 0.6), favourites (0.2) and score (0.2), and the top 1,000 are kept. Compared with popularity alone, this swaps in about 70 series. It drops low-rated fanservice titles such as OniAi and Maken-Ki and adds acclaimed ones such as Princess Tutu, Space Brothers, Gunbuster, Tomorrow's Joe and Tiger & Bunny. Ranking popularity within decades was tried and left out. The pool only holds about 75 pre-2000 series, so it barely changed anything, and fixing the era bias would need a separate fetch sorted by score or favourites.

The franchise relations changed. In the bigger pool, SPIN_OFF and PARENT chained every Gundam series into one point, because AniList marks each one as a spin-off of the 1979 original. Both are now excluded, and side stories still join through SIDE_STORY. ALTERNATIVE stays, so remakes like Fullmetal Alchemist and Brotherhood, or Hunter x Hunter 1999 and 2011, remain one point rather than wasting each other's top slot. Fate stays one point of 20 entries. Grouping now also passes through related entries outside the pool, using the IDs listed in the relations. This rejoins franchises whose seasons link through an unfetched OVA (ARIA was split three ways) and merged 22 groups with no new oversized chains.

The recommendation score is now 39.6 per cent, against 45.5 per cent before. The two numbers aren't comparable: there are 1,000 candidates instead of 682 (random falls from 2.9 to 2.0 per cent), and more of each show's recommendations are now inside the set. Known weak spot: Space Brothers matches workplace comedies (Servant x Service, Blend-S) through the shared Work tag, ahead of Planetes.

## Tuning log (14 Nov 2026, later session)

Classics: `fetch.py --add SORT N --before YEAR --min-popularity P` adds entries to the pool without touching its state. We added the top 300 pre-2000 entries by SCORE_DESC and by FAVOURITES_DESC (popularity above 5,000), which brought 235 new entries into a pool of 3,235. On their own they barely reached the map (49 pre-2000 series), because raw popularity crowds them out. ERA_MIX = 0.5 now blends each series' popularity and favourites rank within its decade with its overall rank. That lifts pre-2000 series to 72, adding Escaflowne, Now and Then Here and There, Outlaw Star, Patlabor, The Big O, Macross, Fist of the North Star, Maison Ikkoku and Pom Poko, and drops mostly forgettable 2010s filler. The benchmark was unchanged.

Tags: a sweep over rank power, a minimum rank cut-off and a 15-tag generic list (Heterosexual, Urban, Work and so on, at 0.3) settled on TAG_RANK_POWER = 2. Squaring the rank lifted the benchmark from 39.6 to 40.5 per cent and put Planetes first for Space Brothers. The generic list and the cut-off didn't help on top of that, so GENERIC_TAGS is empty but kept as a knob. Remaining misses (Bebop not listing Samurai Champloo, Death Note not listing Code Geass) are tone and creator connections the metadata doesn't capture. Bebop's list also pulls in several Sunrise Gundam series, which bears on the open studio weight question.

Cluster labels skip the Cast-Main Cast, Technical and Demographic tag categories plus a short list (Heterosexual, Urban, Nudity, Kuudere, Tsundere, Primarily Male/Female Cast). vectors.py now writes data/tag_categories.json for this.

Layout: with UMAP at 15 neighbours and min_dist 0.1, sports, music and mecha floated off as distant islands. Five variants were compared in Chrome (15, 30 and 50 neighbours; 50 with min_dist 0.3 and 0.6). The choice is 50 neighbours with min_dist 0.6, which gives one continuous map where clusters still stand apart. The settings can be overridden with the UMAP_NEIGHBORS and UMAP_MIN_DIST environment variables. Clustering is unaffected, because it uses its own 10-dimensional embedding.

The page now credits AniList in its footer.

## Open items

1. Publish on GitHub Pages (waiting on the user's confirmation of repo name and visibility).
2. Studio weight: kept open at the user's request. The plan is to compare two maps (light and heavy studio weight) by eye on shows the user knows, rather than tuning to the benchmark, which undervalues "same studio, same feel".
3. Neighbour misses that the metadata can't see (Bebop and Samurai Champloo, Death Note and Code Geass). AniList user recommendations could be blended into the similarity directly, rather than used only for evaluation, if this matters.
