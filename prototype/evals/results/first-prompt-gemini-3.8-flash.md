# Catalog extraction eval: first Gemini run

Run before the prompt change in PR #21 (units in the size field, chatter turned into a product, weight questions). Same 43 messages as `latest.md`, text only.

## Gemini (gemini-3.8-flash)

Model: `gemini-3.8-flash` · 43 cases × 1 run(s) · 2026-10-17 20:30 UTC · median 5.60s per message

| Metric | Score |
|---|---|
| **Cases fully correct** (every field right, nothing invented, asked when needed) | **36/43 (84%)** |
| Product title | 42/42 (100%) |
| Price (incl. leaving it empty when not given) | 42/42 (100%) |
| Variants (colour / size set) | 37/42 (88%) |
| Stock per variant | 37/42 (88%) |
| Weight (only when the seller stated it) | 2/3 (67%) |
| Asks the seller when information is missing | 6/6 (100%) |
| Questions when nothing was missing (lower is better) | 27 of 37 |
| **Invented prices** (price given although the seller gave none) | **0** |
| **Invented stock numbers** | **6** |
| Invented products (created from chatter or extra items) | 2 |
| Missed products | 0 |
| Errors (API failures) | 0 |

Where it fails (cases fully correct by tag, worst first):

| Tag | Correct |
|---|---|
| buyer-question | 0/1 (0%) |
| long-description | 0/1 (0%) |
| product-level-stock | 0/3 (0%) |
| stock-update | 0/1 (0%) |
| structured | 0/1 (0%) |
| units | 0/1 (0%) |
| no-product | 1/3 (33%) |
| distractor | 2/3 (67%) |
| abbreviation | 1/1 (100%) |
| basic | 6/6 (100%) |
| bundle | 1/1 (100%) |
| emoji | 1/1 (100%) |

Failing cases (7 total):

- `c24` distractor: variants [White/350ML:12] vs [White/-:12]
- `c25` long-description: variants [Black/110X110:5, Navy/110X110:3] vs [Black/-:5, Navy/-:3]
- `c30` structured: variants [-/200G:24] vs [-/-:24]; weight 250 vs 200
- `c31` product-level-stock: variants [-/250GR:30] vs [-/-:30]
- `c32` product-level-stock: variants [-/30ML:15] vs [-/-:15]
- `c42` no-product: invented product 'Hijab Merah'
- `c43` no-product: invented product 'Produk'
