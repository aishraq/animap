"""Clean AniList synopses and turn them into sentence embeddings.

Embeddings are cached in data/synopsis_cache.npz, keyed by a hash of the
cleaned text, so rebuilding vectors only embeds synopses that changed.
"""

import hashlib
import html
import re
from pathlib import Path

import numpy as np

MODEL_NAME = "BAAI/bge-small-en-v1.5"
CACHE_PATH = Path(__file__).parent / "data" / "synopsis_cache.npz"
MIN_LENGTH = 80


def clean(text):
    """Strip markup, source credits and editorial notes from a synopsis."""
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"~!.*?!~", "", text, flags=re.S)
    # Credits like "(Source: Crunchyroll)" or "[Written by MAL Rewrite]".
    text = re.sub(r"[\(\[]\s*(source|written by|from)[^\)\]]*[\)\]]", "", text, flags=re.I)
    # Editorial notes usually come last and describe the release, not the story.
    text = re.split(r"\n\s*(?:notes?|note from|\*\s*note)\s*:", text, flags=re.I)[0]
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def pick_synopsis(members):
    """The synopsis of the earliest entry that has a usable one.

    Later seasons tend to spoil earlier ones or assume you know the story, so
    the first instalment describes the franchise best.
    """
    def start(m):
        d = m["startDate"]
        return (d["year"] or 9999, d.get("month") or 12)

    for m in sorted(members, key=start):
        text = clean(m.get("description"))
        if len(text) >= MIN_LENGTH:
            return text
    return clean(members[0].get("description"))


def _key(text):
    return hashlib.sha1(text.encode()).hexdigest()


def embed(texts, verbose=True):
    """Return one unit-length embedding per text; empty texts get zeros."""
    cache = {}
    if CACHE_PATH.exists():
        stored = np.load(CACHE_PATH)
        cache = dict(zip(stored["keys"], stored["vectors"]))

    keys = [_key(t) for t in texts]
    missing = sorted({k: t for k, t in zip(keys, texts) if t and k not in cache}.items())
    if missing:
        from sentence_transformers import SentenceTransformer

        if verbose:
            print(f"embedding {len(missing)} synopses with {MODEL_NAME}")
        model = SentenceTransformer(MODEL_NAME)
        vectors = model.encode([t for _, t in missing], batch_size=32, normalize_embeddings=True, show_progress_bar=verbose)
        for (k, _), v in zip(missing, vectors):
            cache[k] = v.astype(np.float32)
        np.savez_compressed(CACHE_PATH, keys=np.array(list(cache)), vectors=np.stack(list(cache.values())))

    dim = len(next(iter(cache.values())))
    return np.stack([cache[k] if t else np.zeros(dim, dtype=np.float32) for k, t in zip(keys, texts)])
