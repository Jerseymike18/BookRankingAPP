# Branch A — robustness (paired bootstrap + oracle blend)

Paired bootstrap over folds (B=5000). ΔMAE = candidate − baseline (negative = better). Oracle blend picks w on the same folds (optimistic). **A candidate only 'wins' if a CI is entirely below 0.**

## Split: time

| candidate | base MAE | cand MAE | ΔMAE [95% CI] | oracle w* | blend MAE | blend gain [95% CI] |
| --- | --- | --- | --- | --- | --- | --- |
| ridge_llm_emb | 0.628 | 0.787 | +0.159 [+0.072, +0.248] | 0.11 | 0.621 | -0.007 [-0.019, +0.006] |
| ridge_llm_emb_time | 0.628 | 0.782 | +0.154 [+0.058, +0.248] | 0.10 | 0.623 | -0.005 [-0.016, +0.006] |
| knn_emb | 0.628 | 0.883 | +0.255 [+0.148, +0.365] | 0.05 | 0.626 | -0.002 [-0.008, +0.005] |
| blend_base_knn | 0.628 | 0.697 | +0.068 [+0.008, +0.129] | 0.10 | 0.626 | -0.002 [-0.008, +0.005] |

## Split: author

| candidate | base MAE | cand MAE | ΔMAE [95% CI] | oracle w* | blend MAE | blend gain [95% CI] |
| --- | --- | --- | --- | --- | --- | --- |
| ridge_llm_emb | 0.859 | 0.858 | -0.001 [-0.088, +0.091] | 0.52 | 0.808 | -0.051 [-0.104, +0.002] |
| ridge_llm_emb_time | 0.859 | 0.889 | +0.030 [-0.058, +0.124] | 0.41 | 0.834 | -0.025 [-0.065, +0.016] |
| knn_emb | 0.859 | 0.967 | +0.108 [-0.001, +0.221] | 0.17 | 0.850 | -0.009 [-0.030, +0.012] |
| blend_base_knn | 0.859 | 0.870 | +0.012 [-0.046, +0.071] | 0.34 | 0.850 | -0.009 [-0.030, +0.012] |

## Split: series

| candidate | base MAE | cand MAE | ΔMAE [95% CI] | oracle w* | blend MAE | blend gain [95% CI] |
| --- | --- | --- | --- | --- | --- | --- |
| ridge_llm_emb | 0.817 | 0.879 | +0.062 [-0.023, +0.150] | 0.41 | 0.794 | -0.024 [-0.066, +0.018] |
| ridge_llm_emb_time | 0.817 | 0.896 | +0.079 [-0.007, +0.169] | 0.37 | 0.810 | -0.008 [-0.045, +0.031] |
| knn_emb | 0.817 | 0.968 | +0.151 [+0.049, +0.251] | 0.15 | 0.817 | -0.001 [-0.019, +0.017] |
| blend_base_knn | 0.817 | 0.845 | +0.028 [-0.030, +0.087] | 0.30 | 0.817 | -0.001 [-0.019, +0.018] |

