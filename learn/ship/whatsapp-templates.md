# WhatsApp templates

Free-form messages and reply buttons only work while the seller has written to the business number in the **last 24 hours**
(the customer service window). Outside it, Meta rejects the send with error **131047** and only an **approved template** goes
through. Etalase sends four kinds of alert that can arrive outside the window: a stock check, a new paid order, a "ship now"
reminder and the payout notice. Each one is tried free-form first and, on error 131047 only, re-sent as the template below.

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

The Etalase code sends the button payloads (`confirm:<order>`, `oos:<order>`, `accept:<order>`) itself, so there is nothing to
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

:::

Variable text may not contain line breaks, tabs or runs of four spaces; the adapter cleans values before sending.
