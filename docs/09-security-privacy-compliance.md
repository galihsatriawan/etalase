# 9. Security, privacy and compliance

> Planning notes, not legal advice. Each item marked **verify** needs confirmation with a lawyer,
> tax adviser or the provider before the pilot.

## Tenant isolation

- Every seller's data is keyed by `seller_id`; services check ownership before acting
  (`_seller_order`, `set_stock`), and tests cover cross-seller attempts.
- The ADK seller agent receives the seller id from session state, bound to the verified WhatsApp
  number. It is never a tool argument the model could change.
- Prompts sent to Gemini contain only the current seller's data.
- Connector API keys are scoped to one connection (one seller).

## Personal data (Indonesia's PDP Law, UU 27/2022)

| Data | Where it lives | Minimisation |
|---|---|---|
| Seller identity and bank details (KYC) | Payment provider | Etalase stores only verification status and the sub-account id |
| Seller WhatsApp number | Etalase | Needed for messaging and sign-in |
| Buyer name, phone, address | Order record | Needed for delivery; retention period to define (**verify**) |
| Product photos, voice notes | Cloud Storage | Product data, deleted on seller request |

- Roles: for buyer data Etalase acts for the seller and the transaction; roles and contracts to be
  defined (**verify**).
- Gemini runs on Vertex AI in an Asia-Pacific region; customer data is not used to train models
  under Google Cloud's terms (**verify** the current terms and region choice).
- Data subject requests (access, correction, deletion) are handled per seller; an audit trail of stock
  and order changes is kept.

## Payments regulation

Holding and forwarding other people's money is a regulated payment activity in Indonesia
(**verify**). Etalase avoids it by design: funds stay in seller sub-accounts at a licensed provider and
Etalase only instructs when they are released ([ADR 0003](adr/0003-never-hold-funds.md)).

## Tax

| Tax | Who | Notes |
|---|---|---|
| Seller income tax | The seller | Final income tax of 0.5% on turnover up to Rp4.8 billion a year under PP 55/2022; for individuals the first Rp500 million of turnover a year is untaxed, and PP 20/2026 extends the 0.5% rate to tax year 2026 for individuals whose period had ended ([DDTC](https://news.ddtc.co.id/berita/nasional/1798923/pajak-final-umkm-05-dikali-apa-simak-lagi-skema-di-pp-552022), [IKPI](https://ikpi.or.id/resmi-pemerintah-perpanjang-fasilitas-pph-final-umkm-lewat-revisi-pp-55/)). Etalase will provide monthly sales reports to help sellers file |
| PPh 22 collection by marketplaces (PMK 37/2025) | Marketplace operators appointed by the tax office (DJP) | DJP postponed collection until 31 October 2026 (PENG-46/PJ.09/2026), revoked earlier appointments and will reissue them; the minister has floated a start in 2027. Etalase is unlikely to be appointed early on, but the `tax_profile` in each country pack is where withholding would be applied at payout |
| VAT on Etalase's commission | Etalase | Commission is a service; VAT applies once Etalase is VAT-registered (mandatory above Rp4.8 billion annual turnover, voluntary earlier) (**verify**). Pricing states commission excluding VAT |
| VAT on goods | The seller | Most small sellers are not VAT-registered |

Sources: [IKPI on the postponement](https://ikpi.or.id/en/djp-tunda-pungutan-pph-pasal-22-marketplace-hingga-31-oktober-2026/),
[IKPI on a possible 2027 start](https://ikpi.or.id/purbaya-buka-opsi-tunda-penerapan-pajak-markerplace-hingga-2027/),
[DDTC on PMK 37/2025](https://news.ddtc.co.id/berita/nasional/1812157/resmi-terbit-pmk-pemungutan-pph-pasal-22-oleh-marketplace),
[DJP press release](https://pajak.go.id/id/siaran-pers/pemerintah-implementasi-pmk-372025-melalui-penunjukan-empat-marketplace-sebagai).

## Application security

Implemented in the prototype and covered by `tests/test_security.py` and `tests/test_api.py`.

| Area | Control |
|---|---|
| Startup checks | `etalase/security.py` refuses to start on a public URL without `DEV_TOKEN`, `WEBHOOK_TOKEN` and a real `SESSION_SECRET`; `prod` also refuses mock payment, shipping and messaging; real WhatsApp or Xendit keys require their webhook secrets |
| Dev endpoints | `/dev/*` exist only when `ETALASE_ENV=dev`; on a public URL every call needs the `X-Dev-Token` header |
| WhatsApp webhook | `X-Hub-Signature-256` (HMAC with the app secret) is mandatory whenever the real Cloud API is on; constant-time verify-token check; duplicate deliveries ignored by message id |
| Payment webhook | Xendit callback token required (constant-time compare); mock payments need `WEBHOOK_TOKEN`; the mock QR string does not reveal the payment reference; status changes are atomic and idempotent |
| Shipping webhook | Needs `WEBHOOK_TOKEN`, and is only a hint: before any payout the courier is asked directly (`confirm_delivered`, Biteship tracking by waybill), so a forged webhook or a fake waybill cannot release money |
| Money movement | One atomic claim per payout (no double release); refunds and payouts that the provider account cannot automate yet become `refund_pending` / `pending_manual` with an operations event instead of crashing |
| Order state | Every transition is a conditional, atomic update (`Store.update_order`), so the expiry job and a payment webhook cannot both win; a payment that arrives after expiry is refunded automatically |
| Stock | Stock only changes through reserve / commit / release / restock / set_stock; `put_product` never overwrites stock, so a stale catalog edit cannot undo a sale |
| Checkout abuse | Max 10 units per checkout, max 5 checkouts per buyer phone per hour, 20 REST checkouts per IP per hour, strict length and format checks on buyer data; payment-creation failures release the hold |
| Dashboard sign-in | WhatsApp one-time codes: 5-minute TTL, 5 tries per code, 60 s resend cooldown, 5 codes per number per hour, lockout after 10 wrong tries per hour across codes, per-IP limits, no number enumeration; HMAC-signed session tokens |
| Dashboard front end | All seller-provided text is HTML-escaped; Content-Security-Policy with `frame-ancestors 'none'`, `nosniff`, `no-referrer` |
| Instagram Login | Short-lived signed `state` (10 minutes), not a session token |
| Connector API | Per-connection keys hashed at rest; validated, size-limited payloads; order webhooks only to public `https` hosts (no private, loopback or metadata addresses, checked again at send time, no redirects); HMAC-signed bodies |
| Media | Agents get public `/media/<file>` URLs, never server paths; the media route serves file names only from the media and samples folders; the console messenger only reads files under those folders |
| AI safety | The model cannot accept orders or move money; tools validate inputs; tenant id comes from session state, not from the model; product text is labelled as seller content for buyer agents |
| Secrets | Secret Manager in Cloud Run (the deploy script creates random secrets once and never prints them); nothing in the repo (`.env.example` only) |
| Requests | 2 MB body limit, security headers on every response |

## Known gaps before a pilot

- Rate limits and OTP counters are in memory per instance; move them to Memorystore or Firestore
  before running more than one instance.
- Persistent ADK sessions and Firestore as the default store (run the Firestore store against the
  emulator in CI; composite index on reservations `(status, expires_ts)`).
- Xendit refunds and xenPlatform settlement are wired as manual steps until the account setup is
  confirmed; Biteship tracking endpoint and payloads to be verified against the live API.
- Instagram access tokens should be encrypted with Cloud KMS before storage.
- Signed URLs and malware scanning for uploaded media; Cloud Storage instead of the local media folder.
- Formal data retention policy and DPIA.
