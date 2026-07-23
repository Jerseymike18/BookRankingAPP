# Test–Retest Noise Floor (Phase 2.1)

Blind re-ratings by Michael of 26 books finished 12+ months ago (read Jan–Jul 2025), stratified across the WA range. The originals were hidden during re-rating; this compares them.

## Headline — WA test–retest MAE

**0.319 WA** (95% bootstrap CI [0.215, 0.433], n=26).

## Headroom vs the engine (Phase-1 walk-forward, honest)

| | WA MAE |
| --- | --- |
| test–retest noise floor (this) | 0.319 |
| engine — time split | 0.628 |
| engine — author-holdout (cold-start) | 0.859 |
| engine — series-holdout | 0.817 |

_Gate A reading (owner's call): if the floor sits at/above the grouped cold-start MAE (~0.82–0.86), point accuracy is at its irreducible limit — skip Phases 3–4, go to Phase 5. If the floor is well below it, there is real headroom and Gate B selects the branch._

## Component test–retest MAE (worst first; WB realist sentinel excluded)

| component | n | MAE |
| --- | --- | --- |
| Emotional Impact | 26 | 0.988 |
| Integration *(WB)* | 21 | 0.862 |
| Narration | 26 | 0.792 |
| Depth2 *(WB)* | 21 | 0.752 |
| Motivations | 26 | 0.715 |
| Prose | 26 | 0.708 |
| Ending | 26 | 0.665 |
| Thought-Provokingness | 26 | 0.665 |
| Originality *(WB)* | 21 | 0.648 |
| Insights | 26 | 0.646 |
| Action | 26 | 0.554 |
| Entertainment | 26 | 0.512 |
| Plot | 26 | 0.506 |
| Depth | 26 | 0.485 |

## Per-book (original vs blind re-rating WA)

| title | genre | orig WA | retest WA | Δ |
| --- | --- | --- | --- | --- |
| The Last Command | Science Fantasy | 5.37 | 6.42 | +1.06 |
| Ironweed | Literary Fiction | 4.78 | 5.81 | +1.03 |
| Dawnshard | Literary Fantasy | 5.90 | 5.00 | -0.90 |
| Sister Carrie | Literary Fiction | 6.55 | 7.10 | +0.55 |
| New Spring | Epic Fantasy | 7.47 | 6.98 | -0.50 |
| Xenocide | Science Fiction (Har | 6.98 | 7.45 | +0.47 |
| The Eye of the World | Epic Fantasy | 7.32 | 7.74 | +0.42 |
| Heir to the Empire | Science Fantasy | 5.96 | 6.33 | +0.37 |
| The Dragonbone Chair | Epic Fantasy | 6.69 | 7.06 | +0.37 |
| Ender's Game | Science Fiction (Har | 8.45 | 8.82 | +0.36 |
| Elantris | Literary Fantasy | 7.13 | 7.48 | +0.34 |
| Dark Force Rising | Science Fantasy | 6.53 | 6.20 | -0.34 |
| Speaker For The Dead | Science Fiction (Har | 9.35 | 9.07 | -0.27 |
| Edgedancer | Literary Fantasy | 6.09 | 5.84 | -0.25 |
| The Will of the Many | Epic Fantasy | 7.81 | 7.99 | +0.19 |
| Morning Star | Science Fiction (Sof | 8.03 | 8.18 | +0.14 |
| Iron Gold | Science Fiction (Sof | 7.45 | 7.59 | +0.14 |
| Golden Son | Science Fiction (Sof | 8.55 | 8.67 | +0.13 |
| Shadows for Silence | Literary Fantasy | 5.07 | 5.18 | +0.11 |
| Neuromancer | Cyberpunk | 7.94 | 8.04 | +0.10 |
| Don Quixote | Classical Epic | 8.35 | 8.44 | +0.09 |
| The Neverending Story | Literary Fantasy | 9.33 | 9.25 | -0.08 |
| The Idiot | Russian Literature | 8.52 | 8.47 | -0.05 |
| The Lions of Al-Rassan | Literary Fantasy | 8.84 | 8.82 | -0.03 |
| Crossroads of Twilight | Epic Fantasy | 7.54 | 7.54 | -0.00 |
| Empire of Silence | Science Fiction (Sof | 7.13 | 7.13 | +0.00 |

