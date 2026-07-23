#!/usr/bin/env python3
"""
embed_book_text.py — Phase 3 Branch A, step 2a: embed the sourced descriptions
with a sentence-transformer (all-MiniLM-L6-v2, 384-d, L2-normalised). Deterministic
given fixed text+model. Saves the matrix + the aligned title index so the
walk-forward harness can look up any book's vector. PCA reduction happens LATER,
inside each fold (never here — corpus-wide reduction would leak).

Run with the venv that has sentence-transformers (.venv-tabpfn).
"""
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_BACKEND", "sqlite")

import db_write
from sentence_transformers import SentenceTransformer

EMB_NPY = os.path.join(ROOT, "validation", "book_embeddings.npy")
EMB_TITLES = os.path.join(ROOT, "validation", "book_embeddings_titles.json")
MODEL = "all-MiniLM-L6-v2"


def main():
    text = db_write.get_book_text()
    titles = sorted(text)
    if not titles:
        raise SystemExit("No book text cached. Run source_book_text.py first.")
    model = SentenceTransformer(MODEL)
    emb = model.encode([text[t] for t in titles], normalize_embeddings=True,
                       batch_size=32, show_progress_bar=False).astype(np.float32)
    np.save(EMB_NPY, emb)
    json.dump(titles, open(EMB_TITLES, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(f"Embedded {len(titles)} books -> {emb.shape} ({MODEL}).")
    print(f"  wrote {EMB_NPY}\n  wrote {EMB_TITLES}")


if __name__ == "__main__":
    main()
