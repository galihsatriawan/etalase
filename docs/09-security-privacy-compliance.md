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
- Gemini runs on Vertex AI. The default location is the **global endpoint**, because `gemini-3.8-flash` returned 404 in
  `asia-southeast1` for our project (18 Oct 2026); Google's documentation says not to use the global endpoint when you have
  ML processing location requirements. The sandbox holds made-up data only. For a pilot with real seller photos and buyer
  data, choose a model and region that satisfy the location requirement (set `GOOGLE_CLOUD_LOCATION`). Customer data is not
  used to train models under Google Cloud's terms (**verify** the current terms).
- Data subject requests (access, correction, deletion) are handled per seller; an audit trail of stock
  and order changes is kept.

**Order attribution (channel and referrer).** Each order stores which door it came through and a coarse label (`attribution.py`), for measuring demand only. The label is a fixed word, a seller's own link tag or an agent's product name, clipped to 40 characters; never a URL, query string, IP address or identifier. On the store link, one first-party cookie (`etl_src`, 30 minutes, HttpOnly, SameSite=Lax, Secure on https) carries the label to the buy form. A visitor can choose their own label with `?src=`, so the numbers are indicative, not audited. A cookie value is accepted only if it already looks like one of our labels. Mention the cookie in the privacy notice before real buyers use the store link.

## Payments regulation

Holding and forwarding other people's money is a regulated payment activity in Indonesia
(**verify**). By design, Etalase does not receive buyer payments in its own bank account: funds stay in seller sub-accounts at a licensed provider and
Etalase instructs when they are released ([ADR 0003](adr/0003-never-hold-funds.md)), and it
instructs the release only after the courier-confirmed delivery and the buyer's return window
([ADR 0007](adr/0007-payout-hold-and-returns.md)).

**What is not settled.** "Not holding the money in our account" is not the same as "not carrying out a regulated activity". Bank Indonesia's PBI 23/6/PBI/2021 lists forwarding funds (disbursement) to goods and services providers
among the payment-service activities (the lowest licence category has a minimum capital of Rp15 billion), and the explanation of PP 80/2019 mentions a business licence and registration for e-commerce operators (the operative text has not been read).
The sub-account balance is the provider's liability to the sub-account holder. Public attention is also on platforms that withhold seller money: in 2026 TikTok Shop Tokopedia said it was withholding about Rp24 billion from 217 sellers, sellers' representatives spoke of about 500 sellers and Rp3 trillion in parliament,
and a legislator questioned the OJK licence ([Katadata](https://katadata.co.id/digital/e-commerce/6aa7c6d3c4062/dana-seller-rp-24-m-tokopedia-tiktok-tertahan-pemerintah-dan-dpr-turun-tangan)).
So the hold must be published (duration, who decides disputes, how to appeal, when the money is paid), time-limited, and **not branded "rekber"**. It is now published at `/policy/payouts` (2026-10-16), with numbers read from the live settings, a computed longest wait, no "rekber" or "escrow", and an appeal paragraph that appears only when `SUPPORT_CONTACT` is set. The appeal itself (a second team member, seven days) is a proposal that nobody is staffed for yet. Questions for counsel and for the provider are in the market-validation review; none has been answered. Do not describe the design as legally cleared or say that "Xendit holds the funds for us".

**Payment-provider feasibility (gating).** No Xendit page we read documents a platform-controlled hold. If it cannot be done there, the options are a scheduled disbursement at the provider, DOKU's custom hold periods, or a smaller deposit model (already on the roadmap).

**Also open:** whether sellers' payout fees and the Rp4,000 gateway fee apply to Indonesian contracts; whether the xenPlatform account fee applies (Rp25,000 per active account per month on Xendit's Indonesian page); and which legal entity can sign the xenPlatform and Biteship invoice contracts (NPWP, NIB, deed).

**Consumer-facing duties (verify with a lawyer).** Our reading of Permendag 31/2023, through a summary and not the
legal text, is that an e-commerce operator must offer a complaint service (Pasal 9–10) and that social-commerce
operators may not facilitate payments (Pasal 21(3)). Whether Etalase is a PMSE operator, needs a licence and registration (PP 80/2019 Article 11), or has
statutory return duties is open. The definition of social commerce (Article 1(17)) appears to target social-media operators, so Article 21(3) probably does not reach Etalase, but this is a question for counsel and the primary text was not read.
The return flow is a product decision, not a legal conclusion.

## Tax

| Tax | Who | Notes |
|---|---|---|
| Seller income tax | The seller | Final income tax of 0.5% on turnover up to Rp4.8 billion a year under PP 55/2022; for individuals the first Rp500 million of turnover a year is untaxed, and PP 20/2026 extends the 0.5% rate to tax year 2026 for individuals whose period had ended ([DDTC](https://news.ddtc.co.id/berita/nasional/1798923/pajak-final-umkm-05-dikali-apa-simak-lagi-skema-di-pp-552022), [IKPI](https://ikpi.or.id/resmi-pemerintah-perpanjang-fasilitas-pph-final-umkm-lewat-revisi-pp-55/)). Etalase will provide monthly sales reports to help sellers file |
| PPh 22 collection by marketplaces (PMK 37/2025) | Marketplace operators appointed by the tax office (DJP) | DJP postponed collection until 31 October 2026 (PENG-46/PJ.09/2026), revoked earlier appointments and will reissue them; the minister has floated a start in 2027. Etalase is unlikely to be appointed early on, but the `tax_profile` in each country pack is where withholding would be applied at payout. The PMK 37/2025 definition of a collector covers PMSE operators that use an escrow account to hold merchants' earnings above a DJP threshold we have not found, and a sub-account and payout-hold design is close to that definition: ask a tax adviser |
| VAT on Etalase's commission | Etalase | Commission is a service; VAT applies once Etalase is VAT-registered (mandatory above Rp4.8 billion annual turnover, voluntary earlier) (**verify**). Pricing states commission excluding VAT |
| VAT on goods | The seller | Most small sellers are not VAT-registered |

**Personal data update.** PP 33/2026, the PDP implementing regulation, was enacted on 15 July 2026 and applies from 15 January 2027 according to Hukumonline (datapribadi.id gives 16 July and 16 January). Reported content: controller and processor roles, a data protection officer, data-subject rights within 3×24 hours and fines of up to 2% of annual revenue. We have not read the full text and have not confirmed that the PDP agency exists. Open questions: whether Etalase is a controller, processor or joint controller of buyers' names, phone numbers and addresses; whether a DPO and a DPIA are required for AI processing of photos and voice; and whether Vertex AI processing outside Jakarta counts as a cross-border transfer.

**Platform policy.** Meta has barred AI chatbots from the WhatsApp Business API when the chatbot is the main service offered (from 15 January 2026). Our seller agent is on the business-purpose side as we read it, but this needs confirmation from Meta or the business solution provider.

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
| Telegram webhook | With a real bot the call must carry the secret set with `setWebhook` in the `X-Telegram-Bot-Api-Secret-Token` header (constant-time compare; 503 if the secret is not configured; startup refuses a bot token without a 16+ character secret); without a bot it needs `WEBHOOK_TOKEN` like the console WhatsApp webhook. Only private chats are read; groups and channels are ignored. A failing update is still acknowledged so Telegram does not redeliver it. The bot token is part of every API URL, so it is never logged or put in an error text |
| Linking a Telegram chat | A signed code (HMAC, 10 minutes, carries only the seller id and a random part) obtained inside a signed-in dashboard session; the chat is linked when `/start <code>` arrives. The code works **once** (an atomic server-side record), so a copy of it is worthless after use. A forged, altered, expired or used code links nothing; a chat that already belongs to a shop cannot be moved by another code; replacing a linked chat is announced to the old chat and the WhatsApp number; a stranger's chat gets a few short replies an hour. Until it is used, the code is a bearer value ([ADR 0008](adr/0008-seller-channels-and-shop-addresses.md)) |
| Shop addresses (`<slug>.<domain>`) | A slug is 3 to 30 characters of `a-z 0-9` and single hyphens, with a reserved list (`www`, `api`, `admin`, `mcp`, …) and no `xn--` prefix; unique per shop. The Host header maps only `/` to the shop, an unclaimed address answers 404 (wildcard DNS never reaches the dashboard) and the platform's own host is never treated as a shop. Each shop is its own browser origin, and the form-post check compares the `Origin` with the `Host`, so a page on one shop cannot post to another. Photos we host are loaded from `/media/<file>` on the page's own origin, so the CSP (`'self'` and `https:`) covers them on a shop address even over plain http. Brand impersonation through a slug is not prevented beyond the reserved list |
| Payment webhook | Xendit callback token required (constant-time compare); mock payments need `WEBHOOK_TOKEN`; the mock QR string does not reveal the payment reference; status changes are atomic and idempotent |
| Shipping webhook | Needs `WEBHOOK_TOKEN`, and is only a hint: before any payout the courier is asked directly (`confirm_delivered`, Biteship tracking by waybill), so a forged webhook or a fake waybill cannot release money |
| Returns: who may ask | `request_return` needs the checkout id **and** the phone used at checkout (normalised, `08…` = `+628…`). A wrong phone gets the same "checkout not found" as an unknown id, so ids cannot be probed. Only 10 return calls per hour per IP, shared between REST and MCP |
| Returns: untrusted text | The buyer's note (500 characters, whitespace collapsed) and evidence links (at most 5, `https` only, no spaces, 500 characters each) are stored and shown to the seller and operator, **never fetched** by Etalase, so there is no server-side request to forge. The seller's policy note (300 characters) is shown to agents labelled as seller content. The public policy page escapes all HTML and sends a Content-Security-Policy |
| Returns: who may decide | Sellers answer only returns on their own orders (`_seller_order`). Disputes are resolved only through `/tasks/resolve-dispute` behind `WEBHOOK_TOKEN`. Refund and payout are two atomic claims on the same order, so both can never happen |
| Money movement | One atomic claim per payout (no double release); refunds and payouts that the provider account cannot automate yet become `refund_pending` / `pending_manual` with an operations event instead of crashing |
| Order state | Every transition is a conditional, atomic update (`Store.update_order`), so the expiry job and a payment webhook cannot both win; a payment that arrives after expiry is refunded automatically |
| Stock | Stock only changes through reserve / commit / release / restock / set_stock; `put_product` never overwrites stock, so a stale catalog edit cannot undo a sale |
| Checkout abuse | Max 10 units per checkout, max 5 checkouts per buyer phone per hour, 20 REST checkouts per IP per hour, strict length and format checks on buyer data; payment-creation failures release the hold |
| Dashboard sign-in | One-time codes sent to the seller's chosen channel (WhatsApp by default): 5-minute TTL, 5 tries per code, 60 s resend cooldown, 5 codes per number per hour, lockout after 10 wrong tries per hour across codes, per-IP limits, no number enumeration; HMAC-signed session tokens |
| Dashboard front end | All seller-provided text is HTML-escaped (including the buyer's return note and photo links, shown only as `https` links with `rel=noopener noreferrer nofollow`); Content-Security-Policy with `frame-ancestors 'none'`, `nosniff`, `no-referrer` |
| Public store link (`/stores/…`, `/pay/…`) | Server-rendered HTML with every seller and buyer string escaped; JSON-LD escapes `<`. Each response sends `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; font-src 'self'; img-src 'self' https: data:; script-src 'sha256-<hash of our one script>'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'`. The only script is the small constant in `gateway/pages.py` (pending button, live order status, payment countdown); the policy pins it by hash, so nothing a seller or a buyer typed can ever run, and a test checks that every page carries exactly that script and no inline event handlers. `Cache-Control: no-store`, and `noindex` on everything except the catalog page. **Referrer policy is `same-origin`**: the API-wide `no-referrer` made browsers send `Origin: null` on the form post, which our cross-site check refused, so no real purchase worked (found in a real browser, covered by a test and an end-to-end step); `same-origin` keeps store and order URLs away from every other site. Form posts must come from our own host (`Origin` must match `Host`; `null` and foreign origins get 403); bodies are limited to 8 KB and every field is clipped. The order id is the only secret on the pay page (capability URL), so the page shows no phone number or address; returns still need the buyer's phone. Same rate limits as the REST checkout; the seller's WhatsApp number is never published |
| Instagram Login | Short-lived signed `state` (10 minutes), not a session token |
| Connector API | Per-connection keys hashed at rest; validated, size-limited payloads; order webhooks only to public `https` hosts (no private, loopback or metadata addresses, checked again at send time, no redirects); HMAC-signed bodies |
| Media | Agents get public `/media/<file>` URLs, never server paths; the media route serves file names only from the media and samples folders; the console messenger only reads files under those folders |
| AI safety | The model cannot accept orders or move money; tools validate inputs; tenant id comes from session state, not from the model; product text is labelled as seller content for buyer agents |
| Secrets | Secret Manager in Cloud Run (the deploy script creates random secrets once and never prints them); nothing in the repo (`.env.example` only) |
| Requests | 2 MB body limit, security headers on every response |

## Impersonation and phishing

The attacks that matter happen outside our systems: someone writes to a seller from another number as "Etalase support" and asks for the sign-in code, sends a look-alike
"verification" link, or clones a pay page on a similar domain. We cannot stop another number from writing to a seller, so the design makes the real thing recognisable and
keeps money and identity out of the chat. Sign-up in chat (built, off by default, [ADR 0010](adr/0010-chat-signup-address-first.md)) makes this matter more: anyone can now knock on the number, and the first thing a buyer reads is
the address a stranger chose. The verification link it is designed to end with is not built.

| Threat | What exists in the prototype | What is missing |
|---|---|---|
| A fake "support" asks for the sign-in code | The code message says "Never share this code"; a code lasts 5 minutes and allows 5 tries; relinking a shop to a new chat is announced to the old channel and the WhatsApp number | The message does not say that Etalase will never ask for the code, and there is no first-message notice of what Etalase never asks |
| A look-alike verification or payment link | Text typed by a stranger is never turned into a clickable link inside our messages (`seller/format.py`); shop addresses that start with `xn--` (punycode look-alikes) are refused | No fixed-domain policy: the hackathon URL is a `*.run.app` address, and no look-alike domains are registered |
| Another number says it is Etalase | One WhatsApp number sends everything; the first sign-up message says what Etalase never asks for | Meta business verification and display-name approval for the number are not done, and whether a verified badge is open to us is unchecked |
| A hijacked dashboard session | The dashboard has no route that changes the payout account (bank details live at the provider) | Sensitive changes do not yet ask for a fresh code |
| A scam shop under a known brand | A reserved list of names; names and addresses made of a bank's, a marketplace's, "official"/"resmi" or Etalase's word are refused, with links, @handles and phone numbers in shop names; a new shop is invisible until verified, and its address is given back after 72 hours if it is not; the return policy, track record and payout hold are visible to buyers | The word list is short and does not catch a misspelling or a lookalike of a seller's brand; no report button, no abuse process; nobody owns the list ([ADR 0010](adr/0010-chat-signup-address-first.md)) |
| A pay page cloned on another domain | Pay pages are served from our host and show the QR there | Same as the domain row |

Design rules to adopt before a pilot (none is built):

1. One official number with a verified business name.
2. Links only on one domain we own. The verification link is a fixed page of ours that leads to the provider, with no destination parameter, so it cannot become an open
   redirect.
3. The first message and the dashboard say, in one fixed sentence, that Etalase never asks for a code, PIN or password in chat and never asks anyone to transfer money. The
   sign-in code message adds that Etalase will not ask for it.
4. Bank or payout changes only in the signed-in dashboard, with a fresh code and a notice to the old channel.
5. A real domain, with the obvious look-alikes registered; buyers pay only on pages of that domain.
6. A report path (`SUPPORT_CONTACT` already exists as a setting) and a short rule set for shop names and prohibited goods.

## Known gaps before a pilot

- Rate limits and OTP counters are in memory per instance; move them to Memorystore or Firestore
  before running more than one instance.
- Persistent ADK sessions and Firestore as the default store (run the Firestore store against the
  emulator in CI; composite index on reservations `(status, expires_ts)`).
- Seller identity: `kyc_status = verified` is set by a dev endpoint, by the mock provider when a chat sign-up finishes, or by an operator by hand
  (`POST /tasks/verify-seller`). Real verification at the payment provider is not built, so the operator's judgment, the return policy, the payout hold and the
  track record are the only trust signals agents get.
- Returns have no per-buyer limit, no fraud scoring and no human-support tooling beyond the two operator endpoints.
- Xendit refunds and xenPlatform settlement are wired as manual steps until the account setup is
  confirmed; Biteship tracking endpoint and payloads to be verified against the live API.
- Instagram access tokens should be encrypted with Cloud KMS before storage.
- Signed URLs and malware scanning for uploaded media; Cloud Storage instead of the local media folder.
- Formal data retention policy and DPIA.
- Impersonation and phishing outside our channel: see [Impersonation and phishing](#impersonation-and-phishing); the rules there are designs, none is built.
