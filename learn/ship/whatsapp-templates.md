# WhatsApp templates

Free-form messages and reply buttons only work while the seller has written to the business number in the **last 24 hours**
(the customer service window). Outside it, Meta rejects the send with error **131047** and only an **approved template** goes
through. Etalase sends seven kinds of alert that can arrive outside the window: a stock check (the reminder after 15 minutes reuses it), a note that a stock check ran out of time, a new paid order, a "ship now"
reminder, the payout notice, a return request and a return approval (the two return alerts have buttons). Each one is tried free-form first and, on error 131047 only, re-sent as the template below.

You create the templates **once**, in the same WhatsApp Business Account as your phone number. Until a template is approved,
an alert that needs it fails and is logged; checkout is not affected, the order stays saved and visible in the seller dashboard, and only the WhatsApp alert is lost (it is not retried).

::: tip Create the language that matches `SELLER_LOCALE`
The adapter sends each template in the language of `SELLER_LOCALE` (`en` by default, `id` for Indonesian). Create at least that
one. Creating both is harmless.
:::

## How to create them

1. Open **WhatsApp Manager** for your business account → **Message templates** → **Create template**.
2. Category **Utility**. Name: exactly the name shown below (lowercase, underscores). Language: `en` or `id`.
3. Paste the body. Keep the `{{1}}`, `{{2}}` placeholders as written and add the sample values when Meta asks for examples.
4. Where a template lists buttons, add them as **Quick reply** buttons, in that order, with exactly that text.
5. Submit. Review usually takes minutes, sometimes up to a day. The status turns to **Active** when approved.

The Etalase code sends the button payloads (`confirm:<order>`, `oos:<order>`, `accept:<order>`, `return_ok:<order>`, `return_no:<order>`, `return_got:<order>`) itself, so there is nothing to
configure for them in the template.

Meta's menu names change; if one has moved, search WhatsApp Manager for "message templates". `make doctor` cannot check templates,
so the first test is a real one: message the business number from the seller phone, wait 24 hours (or use a second seller number
that never wrote), then place a test order.

## The definitions

Regenerate this list at any time with `cd prototype && python -m etalase.adapters.whatsapp_templates`; the code is the source
of truth, and a unit test keeps it in step with what the notifications send.

::: v-pre

### `etalase_stock_check` / `en`

- Body: Quick stock check. {{1}} for a buyer in {{2}}. Is it still available?
- Buttons (type Quick reply): `Available`, `Sold out`
- Sample values: `Voal Hijab (Black) x 2`, `Bandung`

### `etalase_stock_check` / `id`

- Body: Cek stok dulu ya. {{1}} untuk pembeli di {{2}}. Barangnya masih ada?
- Buttons (type Quick reply): `Ada`, `Habis`
- Sample values: `Voal Hijab (Black) x 2`, `Bandung`

### `etalase_stock_check_expired` / `en`

- Body: The stock check for {{1}} (buyer in {{2}}) ran out of time because there was no answer. The buyer was not charged.
- Sample values: `Voal Hijab (Black) x 2`, `Bandung`

### `etalase_stock_check_expired` / `id`

- Body: Cek stok untuk {{1}} (pembeli di {{2}}) kedaluwarsa karena tidak ada jawaban. Pembeli tidak ditagih.
- Sample values: `Voal Hijab (Black) x 2`, `Bandung`

### `etalase_new_paid_order` / `en`

- Body: New paid order. {{1}}. {{2}}, ship to {{3}} via {{4}}.
- Buttons (type Quick reply): `Accept`, `Out of stock`
- Sample values: `Voal Hijab (Black) x 2`, `Rp170.000`, `Bandung`, `JNE REG`

### `etalase_new_paid_order` / `id`

- Body: Pesanan baru (sudah dibayar). {{1}}. {{2}}, kirim ke {{3}} via {{4}}.
- Buttons (type Quick reply): `Terima`, `Stok habis`
- Sample values: `Voal Hijab (Black) x 2`, `Rp170.000`, `Bandung`, `JNE REG`

### `etalase_paid_ship_now` / `en`

- Body: Payment received. {{1}}. Ship to {{2}} via {{3}}, then reply 'tracking' and the number.
- Sample values: `Voal Hijab (Black) x 2`, `Dewi, Jl. Dago 10, Bandung 40135`, `JNE REG`

### `etalase_paid_ship_now` / `id`

- Body: Pembayaran diterima. {{1}}. Kirim ke {{2}} via {{3}}, lalu balas 'resi' dan nomornya.
- Sample values: `Voal Hijab (Black) x 2`, `Dewi, Jl. Dago 10, Bandung 40135`, `JNE REG`

### `etalase_payout` / `en`

- Body: Parcel delivered. {{1}} is on its way to your account (commission {{2}}).
- Sample values: `Rp163.200`, `Rp6.800`

### `etalase_payout` / `id`

- Body: Paket sudah diterima pembeli. {{1}} dicairkan ke rekeningmu (komisi {{2}}).
- Sample values: `Rp163.200`, `Rp6.800`

### `etalase_return_requested` / `en`

- Body: Return request for {{1}}. Reason: {{2}}. Buyer's note: {{3}}. Approve or dispute within {{4}} hours; no answer counts as approval.
- Buttons (type Quick reply): `Approve return`, `Dispute`
- Sample values: `Voal Hijab (Black) x 2`, `damaged`, `The seam is torn`, `48`

### `etalase_return_requested` / `id`

- Body: Pengajuan retur untuk {{1}}. Alasan: {{2}}. Catatan pembeli: {{3}}. Setujui atau sengketakan dalam {{4}} jam; kalau tidak dijawab dianggap disetujui.
- Buttons (type Quick reply): `Setujui retur`, `Sengketakan`
- Sample values: `Voal Hijab (Black) x 2`, `damaged`, `The seam is torn`, `48`

### `etalase_return_approved` / `en`

- Body: Return approved for {{1}}. When the parcel is back with you, tap the button to start the buyer's refund. If nothing arrives within {{2}} days the return lapses.
- Buttons (type Quick reply): `Item received`
- Sample values: `Voal Hijab (Black) x 2`, `7`

### `etalase_return_approved` / `id`

- Body: Retur {{1}} disetujui. Kalau paketnya sudah kembali ke kamu, tekan tombol untuk memproses refund pembeli. Kalau tidak ada paket dalam {{2}} hari, retur hangus.
- Buttons (type Quick reply): `Barang diterima`
- Sample values: `Voal Hijab (Black) x 2`, `7`

:::

Variable text may not contain line breaks, tabs or runs of four spaces; the adapter cleans values before sending.
