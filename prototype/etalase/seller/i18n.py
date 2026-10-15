"""Seller-facing message templates. English is the default (demo and judging);
Bahasa Indonesia is what Indonesian sellers get in production (SELLER_LOCALE=id)."""

from __future__ import annotations

MESSAGES: dict[str, dict[str, str]] = {
    "unregistered": {
        "en": "Hi! This number is not registered with Etalase yet. Sign up at {url}",
        "id": "Halo! Nomor ini belum terdaftar di Etalase. Daftar dulu di {url} ya.",
    },
    "files_received": {
        "en": "{n} file(s) received. Now send the details: product name, colours & stock, price. A voice note works too.",
        "id": "{n} file diterima. Kirim keterangannya ya: nama produk, warna & stok, harga. Boleh voice note.",
    },
    "catalog_not_understood": {
        "en": "I couldn't read the product yet. Example: 'Voal hijab, black 5, navy 3, price 85k'.",
        "id": "Aku belum menangkap produknya. Contoh: 'Hijab voal, hitam 5, navy 3, harga 85rb'.",
    },
    "summary_head": {"en": "I found {n} product(s):", "id": "Aku dapat {n} produk:"},
    "summary_readiness": {"en": "  Readiness {score}/100", "id": "  Skor kesiapan {score}/100"},
    "summary_missing": {"en": " (missing: {missing})", "id": " (kurang: {missing})"},
    "summary_no_price": {"en": "no price yet", "id": "harga belum ada"},
    "summary_question": {"en": "Publish to AI shopping agents?", "id": "Terbitkan ke AI shopping agent?"},
    "btn_publish": {"en": "Publish", "id": "Terbitkan"},
    "btn_edit": {"en": "Edit", "id": "Ubah"},
    "published": {
        "en": "Done! {n} new product(s) published. {total} products in your shop can now be found by AI agents.",
        "id": "Beres! {n} produk baru terbit. Total {total} produk tokomu sekarang bisa ditemukan agen AI.",
    },
    "edit_hint": {
        "en": "Send the correction, e.g. 'voal hijab price 90k' or 'navy 2 left'.",
        "id": "Kirim koreksinya, misalnya 'harga hijab voal jadi 90rb' atau 'navy tinggal 2'.",
    },
    "confirmed": {"en": "Great, the buyer has been sent the QRIS payment code.",
                  "id": "Siap, kode pembayaran QRIS sudah dikirim ke pembeli."},
    "order_closed": {"en": "This order is already {status}, so nothing was sent to the buyer.",
                     "id": "Pesanan ini sudah {status}, jadi tidak ada yang dikirim ke pembeli."},
    "which_product": {"en": "Which product? {color} is in: {titles}. Example: '<product> {color} 2 left'.",
                      "id": "Produk yang mana? Warna {color} ada di: {titles}. Contoh: '<produk> {color} tinggal 2'."},
    "slow_down": {"en": "You are sending messages faster than I can handle. Please wait a few minutes and try again.",
                  "id": "Pesannya terlalu cepat untuk kuproses. Tunggu beberapa menit lalu coba lagi ya."},
    "agent_unavailable": {"en": "I could not process that just now. Please try again in a minute.",
                          "id": "Maaf, belum bisa memproses itu. Coba lagi sebentar lagi ya."},
    "cannot_process": {"en": "Couldn't do that: {error}", "id": "Tidak bisa diproses: {error}"},
    "stock_updated": {"en": "OK, {color} stock is now {after}.", "id": "Oke, stok {color} sekarang {after}."},
    "variant_not_found": {"en": "I couldn't find a {color} variant in your shop.",
                          "id": "Aku belum menemukan varian {color} di tokomu."},
    "price_updated": {"en": "{titles} now costs {price}.", "id": "Harga {titles} jadi {price}."},
    "price_product_missing": {
        "en": "I couldn't find that product. Name it, e.g. 'voal hijab price 90k'.",
        "id": "Produknya belum ketemu. Sebutkan nama produknya, misalnya 'harga hijab voal jadi 90rb'.",
    },
    "tracking_saved": {
        "en": "Tracking {waybill} saved. {amount} will be paid out once the buyer receives the parcel.",
        "id": "Resi {waybill} tersimpan. {amount} dicairkan setelah paket diterima pembeli.",
    },
    "sales": {
        "en": "{n} order(s) from AI agents, {gross} in total. {paid} paid out; the rest is released after delivery.",
        "id": "{n} pesanan dari agen AI, total {gross}. Sudah cair {paid}, sisanya cair setelah paket diterima.",
    },
    "help": {
        "en": ("I'm your Etalase shop assistant. You can:\n• send photos + a description to build your catalog\n"
               "• 'navy sold out' / 'navy 2 left' / 'restock mocca 20'\n• 'voal hijab price 90k'\n"
               "• 'tracking JNE123456' after shipping\n• 'how many sales this week?'"),
        "id": ("Aku asisten tokomu di Etalase. Kamu bisa:\n• kirim foto + keterangan produk untuk membuat katalog\n"
               "• 'navy habis' / 'navy tinggal 2' / 'restock mocca 20'\n• 'harga hijab voal jadi 90rb'\n"
               "• 'resi JNE123456' setelah kirim paket\n• 'laku berapa minggu ini?'"),
    },
    "stock_check": {
        "en": "Quick stock check 🙏\n{items}\nBuyer in {city}. Is it still available?",
        "id": "Cek stok dulu ya 🙏\n{items}\nPembeli di {city}. Barangnya masih ada?",
    },
    "btn_available": {"en": "Available", "id": "Ada"},
    "btn_sold_out": {"en": "Sold out", "id": "Habis"},
    "new_paid_order": {
        "en": "New paid order 🎉\n{items}\n{subtotal} · ship to {city} via {courier}",
        "id": "Pesanan baru (sudah dibayar) 🎉\n{items}\n{subtotal} · kirim ke {city} via {courier}",
    },
    "btn_accept": {"en": "Accept", "id": "Terima"},
    "btn_out_of_stock": {"en": "Out of stock", "id": "Stok habis"},
    "paid_ship_now": {
        "en": "Payment received 🎉\n{items}\nShip to {address} via {courier}, then reply 'tracking <number>'.",
        "id": "Pembayaran diterima 🎉\n{items}\nKirim ke {address} via {courier}, lalu balas 'resi <nomor>'.",
    },
    "accepted": {
        "en": "Great! Ship to {address}, then reply 'tracking <number>'.",
        "id": "Siap! Kirim ke {address}, lalu balas 'resi <nomor>'.",
    },
    "cancelled": {
        "en": "Order {items} was cancelled and its stock set to 0.{extra}",
        "id": "Pesanan {items} dibatalkan dan stoknya diset 0.{extra}",
    },
    "refunded_extra": {"en": " The buyer has been refunded.", "id": " Dana sudah dikembalikan ke pembeli."},
    "payout": {
        "en": "Parcel delivered ✅ {amount} is on its way to your account (commission {commission}).",
        "id": "Paket sudah diterima pembeli ✅ {amount} dicairkan ke rekeningmu (komisi {commission}).",
    },
    "ig_imported": {
        "en": "Imported from your Instagram ({skipped} non-product post(s) skipped). Variants without a stock "
              "count will ask you to confirm stock before each sale; reply e.g. 'sage 4' to set it.",
        "id": "Diimpor dari Instagram-mu ({skipped} postingan non-produk dilewati). Varian tanpa jumlah stok "
              "akan minta konfirmasi sebelum terjual; balas misalnya 'sage 4' untuk mengisinya.",
    },
    "ig_nothing_new": {"en": "No new product posts found on your Instagram.",
                       "id": "Tidak ada postingan produk baru di Instagram-mu."},
    "otp": {
        "en": "Your Etalase sign-in code: {code}\nValid for 5 minutes. Never share this code.",
        "id": "Kode masuk Etalase: {code}\nBerlaku 5 menit. Jangan bagikan kode ini ke siapa pun.",
    },
}


class Translator:
    def __init__(self, locale: str = "en") -> None:
        self.locale = locale if locale in ("en", "id") else "en"

    def __call__(self, key: str, **kwargs) -> str:
        entry = MESSAGES[key]
        return entry.get(self.locale, entry["en"]).format(**kwargs)
