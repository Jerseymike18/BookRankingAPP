"""
backfill_read_month.py
======================
One-time backfill of the reading-order columns on ``books`` /
``nonfiction_books`` from the owner's reading log:
  * ``read_month`` (1-12) — the month each book was read (by-month Timeline).
  * ``read_seq``          — the exact reading-order rank from the log's line
                            order (top of the log = most recent), HIGHER = more
                            recently read. The Delta Log sorts by it DESC so the
                            page matches the log order exactly, even within a
                            single month. The log is newest-first.
Writes ONLY through db_write setters (constraint: all writes go through db_write).
Idempotent and safe to re-run.

Importing db_write self-migrates both columns (ALTER-if-missing) on whichever
backend is selected, so this also creates them on first run.

Usage
-----
Local SQLite (already done during development):
    python3 backfill_read_month.py --commit

Live Postgres (run once against the hosted DB — a production write):
    DB_BACKEND=postgres DATABASE_URL='<supabase-session-pooler-dsn>' \
        python3 backfill_read_month.py --commit

Flags:
    (no flag)         dry run — prints what it WOULD set, writes nothing.
    --commit          perform the writes.
    --user-id <uid>   target a specific user_id (defaults to db_backend.DEFAULT_USER_ID,
                      which is the owner's uid on both the local DB and Postgres).

A book already carrying the right month is simply re-set to the same value, so
re-running is harmless. A title with no month in the log is left NULL and logged.
"""
import sys
import re
import db_write
import db_backend

DRY = "--commit" not in sys.argv
USER_ID = db_backend.DEFAULT_USER_ID
if "--user-id" in sys.argv:
    USER_ID = sys.argv[sys.argv.index("--user-id") + 1]

# Owner's reading log, newest-first. (year, month, "; "-joined titles)
LOG = """2026|7|Lord of Emperors; The Obelisk Gate; The Rise of Endymion
2026|6|The Republic of Thieves; Endymion; The Martian Chronicles; Notes From a Dead House; Red Seas Under Red Skies; The Fall of Hyperion; The Lies of Locke Lamora; Ready Player Two; Hyperion; The Return of the King
2026|5|The Two Towers; The Fellowship of the Ring; The Hobbit; A Parade of Horribles; The Crippled God; Dust of Dreams; This Inevitable Ruin; The Eye of the Bedlam Bride; The Butcher's Masquerade; The Gate of the Feral Gods; The Fifth Season; The Dungeon Anarchist's Cookbook
2026|4|Carl's Doomsday Scenario; There is no Antimemetics Division; The Name of the Wind; Red Country; Dungeon Crawler Carl; The Heroes; Best Served Cold; The Last Argument of Kings; Between Two Fires; Blindsight; The Fires of Vengeance; The Rage of Dragons; Momo; Before They Are Hanged; Future of an Illusion
2026|3|Toll the Hounds; The Death of Ivan Ilyich; The Blade Itself; Reaper's Gale; Picture of Dorian Gray; The Bonehunters; Piranesi; Frankenstein; Ready Player One; Midnight Tides; Twilight of the Idols
2026|2|House of Chains; Rendezvous with Rama; Memories of Ice
2026|1|Deadhouse Gates; Gardens of the Moon; Einstein's Dreams; To Green Angel Tower; The Last Shadow; The Prince
2025|12|Project Hail Mary; Shadows Upon Time; Disquiet Gods; Ashes of Man; Kingdoms of Death; Demon in White; Inferno
2025|11|Howling Dark; The Strength of the Few; Sailing to Sarantium
2025|10|The Oresteia
2025|9|The Odyssey
2025|8|Silence; Station 11; The Anxious Generation; Shadows in Flight; Ender's Shadow; Rosencrantz and Guildenstern are Dead
2025|7|The Stone of Farewell; A Memory of Light; The Last Command; Dark Force Rising; Heir to the Empire; The Lions of Al-Rassan; Towers of Midnight; The Gathering Storm; Knife of Dreams
2025|6|The Neverending Story; Crossroads of Twilight; Bringing Down the House; Winter's Heart; A Path of Daggers; A Crown of Swords; Children of the Mind; Xenocide; Speaker for the Dead; Ender's Game; Lord of Chaos; The Fires of Heaven; The Shadow Rising; Wind and Truth; The Dragonbone Chair
2025|5|The Dragon Reborn; The Great Hunt; The Eye of the World; New Spring; Dawnshard; Rhythm of War; Shadows For Silence In the Forests of Hell; The Emperor's Soul; Elantris; Mistborn: Secret History; Oathbringer
2025|4|Empire of Silence; Edgedancer; Warbreaker; Words of Radiance; The Way of Kings; Mistborn: The Hero of Ages; Mistborn: The Well of Ascension
2025|3|Mistborn: The Final Empire; Lightbringer; Dark Age; Iron Gold; The Will of the Many; Morning Star; Golden Son
2025|2|Red Rising; The Idiot; Neuromancer; Twenty Thousand Leagues Under the Sea; Ironweed
2025|1|Martin Eden; Sister Carrie; Don Quixote"""


def norm(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


month_of, year_of = {}, {}
ordered = []   # normalized titles in log order (top = most recently read)
for line in LOG.strip().splitlines():
    y, m, titles = line.split("|")
    for t in titles.split(";"):
        k = norm(t)
        month_of[k] = int(m)
        year_of[k] = int(y)
        ordered.append(k)

# read_seq: higher = more recently read. The log is newest-first, so the TOP
# title gets the highest rank (len) and the bottom (oldest) gets 1. Global across
# fiction+nonfiction, so fiction ranks have gaps where nonfiction sits — harmless,
# relative order is preserved.
_N = len(ordered)
seq_of = {k: _N - i for i, k in enumerate(ordered)}

# DB title (normalized) -> log title (normalized), for titles stored differently
# than the log spelling (Mistborn prefix, "A/The" swap, truncation).
ALIASES = {
    norm("The Path of Daggers"):    norm("A Path of Daggers"),
    norm("The Final Empire"):       norm("Mistborn: The Final Empire"),
    norm("The Hero of Ages"):       norm("Mistborn: The Hero of Ages"),
    norm("The Well of Ascension"):  norm("Mistborn: The Well of Ascension"),
    norm("Shadows for Silence"):    norm("Shadows For Silence In the Forests of Hell"),
}


def _resolve(title):
    """Return the normalized log-key for a DB title (direct or via ALIASES)."""
    k = norm(title)
    if k in month_of:
        return k
    if k in ALIASES:
        return ALIASES[k]
    return None


def month_for(title):
    k = _resolve(title)
    return (month_of[k], year_of[k]) if k else (None, None)


def seq_for(title):
    k = _resolve(title)
    return seq_of[k] if k else None


def main():
    con = db_backend.connect(db_write.DB)
    fic = con.execute("SELECT title, year_read FROM books WHERE user_id=?",
                      (USER_ID,)).fetchall()
    nf = con.execute("SELECT title, year_read FROM nonfiction_books WHERE user_id=?",
                     (USER_ID,)).fetchall()
    con.close()

    print(f"backend={db_backend.backend()}  user_id={USER_ID}  "
          f"{'DRY RUN' if DRY else 'COMMIT'}")

    def run(rows, month_setter, seq_setter, label):
        print(f"\n=== {label} ({len(rows)} rows) ===")
        set_n = miss = yearwarn = 0
        for title, yr in rows:
            m, ly = month_for(title)
            s = seq_for(title)
            if m is None:
                miss += 1
                print(f"  NO MONTH: {title!r} (yr {yr}) — left NULL")
                continue
            if ly is not None and yr is not None and int(ly) != int(yr):
                yearwarn += 1
                print(f"  YEAR MISMATCH: {title!r} db={yr} log={ly} (setting month {m} anyway)")
            if DRY:
                print(f"  would set {title!r} -> month {m}, seq {s}")
            else:
                if not month_setter(title, m, user_id=USER_ID) \
                        or not seq_setter(title, s, user_id=USER_ID):
                    print(f"  SETTER FAILED: {title!r}")
                    continue
            set_n += 1
        print(f"  -> {set_n} set, {miss} left NULL, {yearwarn} year-mismatch")

    run(fic, db_write.set_read_month, db_write.set_read_seq, "fiction books")
    run(nf, db_write.set_nonfiction_read_month, db_write.set_nonfiction_read_seq,
        "nonfiction_books")
    print("\nDRY RUN — re-run with --commit to write." if DRY
          else "\nDONE (committed).")


if __name__ == "__main__":
    main()
