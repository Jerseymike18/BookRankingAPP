# Branch A — text-embedding candidates (Phase 3)

Walk-forward (read_seq order, past-only pools, burn-in 15). All models fit in-fold (StandardScaler / PCA≤12 / RidgeCV / kNN k=10) — no leakage. Text = MiniLM(384-d) of a spoiler-free premise/themes description. **Lower MAE + higher ρ/τ is better.**

## Split: time

| candidate | WA MAE | Δ vs base | Spearman ρ | Kendall τ | n |
| --- | --- | --- | --- | --- | --- |
| baseline | 0.628 | +0.000 | 0.690 | 0.505 | 116 |
| ridge_llm | 0.799 | +0.171 | 0.441 | 0.302 | 116 |
| ridge_emb | 0.908 | +0.280 | 0.159 | 0.104 | 116 |
| ridge_llm_emb | 0.787 | +0.159 | 0.492 | 0.350 | 116 |
| ridge_llm_emb_time | 0.782 | +0.154 | 0.475 | 0.345 | 116 |
| knn_emb | 0.883 | +0.255 | 0.257 | 0.179 | 116 |
| blend_base_knn | 0.697 | +0.068 | 0.620 | 0.461 | 116 |

## Split: author

| candidate | WA MAE | Δ vs base | Spearman ρ | Kendall τ | n |
| --- | --- | --- | --- | --- | --- |
| baseline | 0.859 | +0.000 | 0.369 | 0.252 | 110 |
| ridge_llm | 0.967 | +0.108 | 0.238 | 0.165 | 110 |
| ridge_emb | 0.965 | +0.106 | 0.020 | 0.024 | 110 |
| ridge_llm_emb ⭐ | 0.858 | -0.001 | 0.347 | 0.245 | 110 |
| ridge_llm_emb_time | 0.889 | +0.030 | 0.283 | 0.196 | 110 |
| knn_emb | 0.967 | +0.108 | 0.059 | 0.042 | 110 |
| blend_base_knn | 0.870 | +0.012 | 0.292 | 0.211 | 110 |

## Split: series

| candidate | WA MAE | Δ vs base | Spearman ρ | Kendall τ | n |
| --- | --- | --- | --- | --- | --- |
| baseline | 0.817 | +0.000 | 0.461 | 0.314 | 114 |
| ridge_llm | 0.864 | +0.047 | 0.324 | 0.221 | 114 |
| ridge_emb | 0.999 | +0.181 | -0.154 | -0.098 | 114 |
| ridge_llm_emb | 0.879 | +0.062 | 0.308 | 0.219 | 114 |
| ridge_llm_emb_time | 0.896 | +0.079 | 0.275 | 0.193 | 114 |
| knn_emb | 0.968 | +0.151 | 0.025 | 0.017 | 114 |
| blend_base_knn | 0.845 | +0.028 | 0.343 | 0.242 | 114 |

