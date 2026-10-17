# Catalog extraction eval

Text messages only (captions, chat text, voice-note transcripts). Photos and real audio are not part of this eval yet.

## Offline rules (baseline)

Model: `rules` · 43 cases × 1 run(s) · 2026-10-15 11:35 UTC · median 0.00s per message

| Metric | Score |
|---|---|
| **Cases fully correct** (every field right, nothing invented, asked when needed) | **21/43 (49%)** |
| Product title | 27/42 (64%) |
| Price (incl. leaving it empty when not given) | 33/42 (79%) |
| Variants (colour / size set) | 24/42 (57%) |
| Stock per variant | 24/42 (57%) |
| Weight (only when the seller stated it) | 2/3 (67%) |
| Asks the seller when information is missing | 1/6 (17%) |
| Questions when nothing was missing (lower is better) | 0 of 37 |
| **Invented prices** (price given although the seller gave none) | **0** |
| **Invented stock numbers** | **1** |
| Invented products (created from chatter or extra items) | 6 |
| Missed products | 8 |
| Errors (API failures) | 0 |

Where it fails (cases fully correct by tag, worst first):

| Tag | Correct |
|---|---|
| bundle | 0/1 (0%) |
| emoji | 0/1 (0%) |
| long-description | 0/1 (0%) |
| low-stock | 0/1 (0%) |
| missing-stock | 0/5 (0%) |
| order | 0/1 (0%) |
| phone-number | 0/1 (0%) |
| preorder | 0/1 (0%) |
| product-level-stock | 0/3 (0%) |
| sizes | 0/4 (0%) |
| slang | 0/1 (0%) |
| sold-out | 0/1 (0%) |

Failing cases (22 total, first 15):

- `c15` price-format: missed product 'hijab voal'; invented product 'Product'
- `c16` sizes: title 'Kaos Oversize Hitam Size S' vs 'kaos oversize'; variants [] vs [Black/L:2, Black/M:5, Black/S:3]
- `c17` sizes: variants [] vs [White/39:2, White/40:3, White/41:1]
- `c18` sizes: title 'Jaket Denim Size M Tinggal' vs 'jaket denim'; variants [] vs [-/M:1]
- `c19` sizes: title 'Baju Koko Putih Size L' vs 'baju koko'; variants [] vs [White/L:None]; did not ask about the missing information
- `c20` emoji: missed product 'cardigan rajut'; invented product 'READY'
- `c21` slang: title 'Ready Ya Kak Gamis Syari Warna Dusty' vs 'gamis syari'; variants [Mocca/-:2, Pink/-:4] vs [Mocca/-:2, dusty pink/-:4]
- `c22` distractor: title 'Free Ongkir Jabodetabek Blouse Polos' vs 'blouse polos'
- `c23` distractor: missed product 'dompet kulit'; invented product 'Kontak'
- `c25` long-description: title 'Ini Hijab Bahan Voal Premium Lasercut Ringan dan Adem Warna' vs 'hijab voal'
- `c26` sold-out: title 'Hijab Voal Hitam Habis Navy Tinggal' vs 'hijab voal'; variants [] vs [Black/-:0, Navy/-:2]
- `c28` multi-product: missed product 'kaos polos'; missed product 'celana kulot'; invented product 'Product'; invented product 'Product'
- `c29` variant-price: price ['25000'] vs ['25000', '30000']; variants [] vs [Blue/-:3, Red/-:5]
- `c30` structured: missed product 'sambal roa'; invented product 'Product'
- `c31` product-level-stock: variants [] vs [-/-:30]

## Gemini (gemini-3.8-flash)

Model: `gemini-3.8-flash` · 43 cases × 1 run(s) · 2026-10-17 20:59 UTC · median 4.78s per message

| Metric | Score |
|---|---|
| **Cases fully correct** (every field right, nothing invented, asked when needed) | **42/43 (98%)** |
| Product title | 41/42 (98%) |
| Price (incl. leaving it empty when not given) | 42/42 (100%) |
| Variants (colour / size set) | 42/42 (100%) |
| Stock per variant | 42/42 (100%) |
| Weight (only when the seller stated it) | 3/3 (100%) |
| Asks the seller when information is missing | 6/6 (100%) |
| Questions when nothing was missing (lower is better) | 3 of 37 |
| **Invented prices** (price given although the seller gave none) | **0** |
| **Invented stock numbers** | **0** |
| Invented products (created from chatter or extra items) | 0 |
| Missed products | 0 |
| Errors (API failures) | 0 |

Where it fails (cases fully correct by tag, worst first):

| Tag | Correct |
|---|---|
| long-description | 0/1 (0%) |
| abbreviation | 1/1 (100%) |
| basic | 6/6 (100%) |
| bundle | 1/1 (100%) |
| buyer-question | 1/1 (100%) |
| distractor | 3/3 (100%) |
| emoji | 1/1 (100%) |
| en | 3/3 (100%) |
| id | 4/4 (100%) |
| juta | 2/2 (100%) |
| low-stock | 1/1 (100%) |
| missing-price | 1/1 (100%) |

Failing cases (1 total):

- `c25` long-description: title 'Hijab Voal Premium Lasercut 110x110' vs 'hijab voal'

## Gemini (gemini-3.1-flash-lite)

Model: `gemini-3.1-flash-lite` · 43 cases × 1 run(s) · 2026-10-17 21:01 UTC · median 1.22s per message

| Metric | Score |
|---|---|
| **Cases fully correct** (every field right, nothing invented, asked when needed) | **36/43 (84%)** |
| Product title | 41/42 (98%) |
| Price (incl. leaving it empty when not given) | 41/42 (98%) |
| Variants (colour / size set) | 42/42 (100%) |
| Stock per variant | 39/42 (93%) |
| Weight (only when the seller stated it) | 2/3 (67%) |
| Asks the seller when information is missing | 5/6 (83%) |
| Questions when nothing was missing (lower is better) | 7 of 37 |
| **Invented prices** (price given although the seller gave none) | **0** |
| **Invented stock numbers** | **0** |
| Invented products (created from chatter or extra items) | 0 |
| Missed products | 0 |
| Errors (API failures) | 0 |

Where it fails (cases fully correct by tag, worst first):

| Tag | Correct |
|---|---|
| juta | 0/2 (0%) |
| long-description | 0/1 (0%) |
| missing-price | 0/1 (0%) |
| structured | 0/1 (0%) |
| price-format | 4/7 (57%) |
| product-level-stock | 2/3 (67%) |
| missing-stock | 4/5 (80%) |
| abbreviation | 1/1 (100%) |
| basic | 6/6 (100%) |
| bundle | 1/1 (100%) |
| buyer-question | 1/1 (100%) |
| distractor | 3/3 (100%) |

Failing cases (7 total):

- `c10` price-format: stock [Mocca/-:None] vs [Mocca/-:4]
- `c11` price-format: price ['600000'] vs ['1200000']
- `c12` price-format: stock [Brown/-:None] vs [Brown/-:3]
- `c25` long-description: title 'Voal Premium Lasercut Hijab 110x110' vs 'hijab voal'
- `c30` structured: weight 250 vs 200
- `c36` missing-price: stock [Black/-:None, Navy/-:3] vs [Black/-:5, Navy/-:3]
- `c37` missing-stock: did not ask about the missing information
