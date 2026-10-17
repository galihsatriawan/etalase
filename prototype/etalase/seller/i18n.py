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
        "en": "Done! {n} new product(s) published. {total} products in your shop can now be found by AI agents.\n"
              "Share this link with buyers (Instagram bio, WhatsApp status): {url}",
        "id": "Beres! {n} produk baru terbit. Total {total} produk tokomu sekarang bisa ditemukan agen AI.\n"
              "Bagikan link ini ke pembeli (bio Instagram, status WhatsApp): {url}",
    },
    "store_link": {"en": "Your shop link, for buyers and AI agents: {url}",
                   "id": "Link tokomu, untuk pembeli dan agen AI: {url}"},
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
        "en": "Tracking {waybill} saved. {amount} is paid out {hours} hours after the buyer receives the parcel, "
              "unless they open a return.",
        "id": "Resi {waybill} tersimpan. {amount} dicairkan {hours} jam setelah paket diterima pembeli, "
              "kecuali pembeli mengajukan retur.",
    },
    "sales": {
        "en": "{n} order(s) from AI agents, {gross} in total. {paid} paid out; the rest is released once the buyers' "
              "return window has passed.",
        "id": "{n} pesanan dari agen AI, total {gross}. Sudah cair {paid}, sisanya cair setelah masa retur pembeli lewat.",
    },
    "help": {
        "en": ("I'm your Etalase shop assistant. You can:\n• send photos + a description to build your catalog\n"
               "• 'navy sold out' / 'navy 2 left' / 'restock mocca 20'\n• 'voal hijab price 90k'\n"
               "• 'tracking JNE123456' after shipping\n• 'how many sales this week?'\n• 'shop link'\n"
               "• 'accept returns for change of mind' / 'no returns for change of mind'"),
        "id": ("Aku asisten tokomu di Etalase. Kamu bisa:\n• kirim foto + keterangan produk untuk membuat katalog\n"
               "• 'navy habis' / 'navy tinggal 2' / 'restock mocca 20'\n• 'harga hijab voal jadi 90rb'\n"
               "• 'resi JNE123456' setelah kirim paket\n• 'laku berapa minggu ini?'\n• 'link toko'\n"
               "• 'terima retur berubah pikiran' / 'tidak terima retur berubah pikiran'"),
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
    "refunded_extra": {"en": " The buyer is being refunded.", "id": " Dana pembeli sedang dikembalikan."},
    "payout": {
        "en": "Parcel delivered ✅ {amount} is on its way to your account (commission {commission}).",
        "id": "Paket sudah diterima pembeli ✅ {amount} dicairkan ke rekeningmu (komisi {commission}).",
    },
    "delivered_hold": {
        "en": "Parcel delivered ✅ {amount} will be paid out on {date} if the buyer does not open a return.",
        "id": "Paket sudah diterima pembeli ✅ {amount} dicairkan tanggal {date} kalau pembeli tidak mengajukan retur.",
    },
    "parcel_returned": {
        "en": "The courier brought the parcel for {items} back to you. The buyer is being refunded and the stock is back.",
        "id": "Kurir mengembalikan paket {items} ke kamu. Dana pembeli sedang dikembalikan dan stok dikembalikan.",
    },
    "parcel_lost": {
        "en": "The courier reports the parcel for {items} as lost or destroyed. The buyer is being refunded in full, so no payout "
              "follows for this order. If you insured the parcel, file the claim with the courier.",
        "id": "Kurir melaporkan paket {items} hilang atau rusak total. Dana pembeli dikembalikan penuh, jadi tidak ada pencairan "
              "untuk pesanan ini. Kalau paketnya kamu asuransikan, ajukan klaim ke kurir.",
    },
    "return_requested": {
        "en": "Return request ↩️\n{items}\nReason: {reason}\nBuyer's note: {note}\nApprove or dispute within {hours} hours; "
              "no answer counts as approval.",
        "id": "Pengajuan retur ↩️\n{items}\nAlasan: {reason}\nCatatan pembeli: {note}\nSetujui atau sengketakan dalam {hours} jam; "
              "kalau tidak dijawab dianggap disetujui.",
    },
    "btn_return_ok": {"en": "Approve return", "id": "Setujui retur"},
    "btn_return_dispute": {"en": "Dispute", "id": "Sengketakan"},
    "return_approved": {
        "en": "Return approved for {items}. When the parcel is back with you, tap the button and the buyer's refund starts. "
              "If nothing arrives within {days} days the return lapses.",
        "id": "Retur {items} disetujui. Kalau paketnya sudah kembali ke kamu, tekan tombol dan refund pembeli diproses. "
              "Kalau tidak ada paket dalam {days} hari, retur hangus.",
    },
    "btn_return_got": {"en": "Item received", "id": "Barang diterima"},
    "return_disputed": {
        "en": "Noted. The return for {items} goes to our team, who review it with the buyer's photos. "
              "The payout stays on hold until then.",
        "id": "Dicatat. Retur {items} diperiksa tim kami bersama foto pembeli. "
              "Pencairan ditahan sampai ada keputusan.",
    },
    "return_refunded": {
        "en": "Return completed for {items}. Refund to the buyer: {amount}.{restock}",
        "id": "Retur {items} selesai. Refund untuk pembeli: {amount}.{restock}",
    },
    "restock_extra": {"en": " The unit is back in stock.", "id": " Barangnya kembali ke stok."},
    "return_shipped_back": {
        "en": "The buyer says the parcel for {items} is on its way back (tracking {waybill}).",
        "id": "Pembeli bilang paket {items} sedang dikirim balik (resi {waybill}).",
    },
    "return_policy_on": {
        "en": "OK. Buyers can now return items for a change of mind (they pay the return shipping; the shipping fee is not "
              "refunded). Damaged, wrong or not-as-described items can always be returned within {hours} hours of delivery.",
        "id": "Oke. Pembeli sekarang boleh retur karena berubah pikiran (ongkir balik ditanggung pembeli; ongkir awal tidak "
              "dikembalikan). Barang rusak, salah, atau tidak sesuai deskripsi selalu bisa diretur dalam {hours} jam setelah diterima.",
    },
    "return_policy_off": {
        "en": "OK. No returns for a change of mind. Damaged, wrong or not-as-described items can still be returned within "
              "{hours} hours of delivery.",
        "id": "Oke. Tidak menerima retur karena berubah pikiran. Barang rusak, salah, atau tidak sesuai deskripsi tetap bisa "
              "diretur dalam {hours} jam setelah diterima.",
    },
    "reason_damaged": {"en": "damaged", "id": "rusak"},
    "reason_wrong_item": {"en": "wrong item", "id": "barang salah"},
    "reason_not_as_described": {"en": "not as described", "id": "tidak sesuai deskripsi"},
    "reason_changed_mind": {"en": "changed mind", "id": "berubah pikiran"},
    "no_note": {"en": "(no note)", "id": "(tanpa catatan)"},
    "return_photos": {"en": "({n} photo link(s) from the buyer are on your dashboard)",
                      "id": "({n} link foto dari pembeli ada di dashboard-mu)"},
    "ig_imported": {
        "en": "Imported from your Instagram ({skipped} non-product post(s) skipped). Variants without a stock "
              "count will ask you to confirm stock before each sale; reply e.g. 'sage 4' to set it.",
        "id": "Diimpor dari Instagram-mu ({skipped} postingan non-produk dilewati). Varian tanpa jumlah stok "
              "akan minta konfirmasi sebelum terjual; balas misalnya 'sage 4' untuk mengisinya.",
    },
    "ig_nothing_new": {"en": "No new product posts found on your Instagram.",
                       "id": "Tidak ada postingan produk baru di Instagram-mu."},
    "telegram_linked": {
        "en": "Connected! Orders and stock checks for {shop} will arrive here from now on. Send 'help' to see what I can do.",
        "id": "Terhubung! Pesanan dan cek stok untuk {shop} sekarang masuk ke sini. Kirim 'help' untuk melihat yang bisa kubantu.",
    },
    "telegram_not_linked": {
        "en": "This chat is not linked to an Etalase shop yet. Open your dashboard, go to Settings and tap Connect Telegram.",
        "id": "Chat ini belum terhubung ke toko Etalase. Buka dashboard, masuk ke Pengaturan, lalu ketuk Hubungkan Telegram.",
    },
    "telegram_link_invalid": {
        "en": "That link has expired or is not valid. Open the dashboard and tap Connect Telegram again.",
        "id": "Link itu sudah kedaluwarsa atau tidak valid. Buka dashboard dan ketuk Hubungkan Telegram lagi.",
    },
    "telegram_replaced": {
        "en": "A different Telegram chat was just linked to {shop}, so this one no longer gets orders. If that was not you, "
              "sign in to the dashboard and check Settings.",
        "id": "Chat Telegram lain baru saja dihubungkan ke {shop}, jadi chat ini tidak lagi menerima pesanan. Kalau itu bukan "
              "kamu, masuk ke dashboard dan periksa Pengaturan.",
    },
    "channel_switched": {
        "en": "OK. Orders, stock checks and payouts will be sent here from now on.",
        "id": "Siap. Pesanan, cek stok, dan pencairan sekarang dikirim ke sini.",
    },
    "otp": {
        "en": "Your Etalase sign-in code: {code}\nValid for 5 minutes. Never share this code.",
        "id": "Kode masuk Etalase: {code}\nBerlaku 5 menit. Jangan bagikan kode ini ke siapa pun.",
    },
    # ---- sign-up in chat (seller/signup.py)
    "signup_welcome": {
        "en": "Hi! I'm Etalase. I'll set up your online shop right here in chat, it takes about 2 minutes. What is your shop's name?\n\n"
              "One thing to know: Etalase never asks for a code, PIN or password in chat, and never asks you to transfer money.",
        "id": "Halo! Aku Etalase. Aku bantu bikin toko online-mu di chat ini, sekitar 2 menit. Apa nama tokomu?\n\n"
              "Satu hal: Etalase tidak pernah meminta kode, PIN, atau kata sandi lewat chat, dan tidak pernah meminta kamu transfer uang.",
    },
    "signup_busy": {
        "en": "A lot of shops are signing up right now. Please try again in an hour.",
        "id": "Lagi banyak toko yang mendaftar. Coba lagi satu jam lagi ya.",
    },
    "signup_name_bad": {
        "en": "{reason}", "id": "{reason}",
    },
    "signup_slug_offer": {
        "en": "Nice, {name}! Your shop address can be:\n{url}\nTap Use it, or type the name you want instead (letters, numbers and hyphens).",
        "id": "Mantap, {name}! Alamat tokomu bisa jadi:\n{url}\nKetuk Pakai ini, atau ketik nama lain yang kamu mau (huruf, angka, dan tanda hubung).",
    },
    "signup_slug_taken": {
        "en": "{slug} is already taken. How about:\n{url}\nTap Use it, or type another name.",
        "id": "{slug} sudah dipakai. Bagaimana kalau:\n{url}\nKetuk Pakai ini, atau ketik nama lain.",
    },
    "signup_slug_bad": {
        "en": "{reason} Type another name, or tap Use it for {url}.",
        "id": "{reason} Ketik nama lain, atau ketuk Pakai ini untuk {url}.",
    },
    "btn_use_it": {"en": "Use it", "id": "Pakai ini"},
    "signup_address": {
        "en": "{saved}Where will you ship from? Send the street, city and 5-digit postal code in one message, "
              "e.g. 'Jl. Dago 10, Bandung 40135'. Buyers only ever see the city.",
        "id": "{saved}Kamu kirim barang dari mana? Kirim jalan, kota, dan kode pos 5 angka dalam satu pesan, "
              "misalnya 'Jl. Dago 10, Bandung 40135'. Pembeli hanya melihat kotanya.",
    },
    "signup_saved": {"en": "Saved: {url}\n", "id": "Tersimpan: {url}\n"},
    "signup_address_bad": {
        "en": "I couldn't read that. Send the street, city and 5-digit postal code, e.g. 'Jl. Dago 10, Bandung 40135'.",
        "id": "Aku belum bisa membacanya. Kirim jalan, kota, dan kode pos 5 angka, misalnya 'Jl. Dago 10, Bandung 40135'.",
    },
    "signup_done_open": {
        "en": "Your shop is ready: {url}\nSend photos and a description of your products (a voice note works too) and I'll build "
              "your catalog. Reminder: Etalase never asks for a code, PIN or password in chat, and never asks you to transfer money.",
        "id": "Tokomu siap: {url}\nKirim foto dan keterangan produkmu (voice note juga boleh) dan aku buatkan katalognya. "
              "Ingat: Etalase tidak pernah meminta kode, PIN, atau kata sandi lewat chat, dan tidak pernah meminta kamu transfer uang.",
    },
    "signup_done_pending": {
        "en": "Your shop {name} is set up: {url}\nIt is not open to buyers yet. It opens once your identity is verified, and I'll "
              "tell you here. Meanwhile send photos and a description of your products and I'll build your catalog.{hold}",
        "id": "Toko {name} sudah dibuat: {url}\nTokomu belum dibuka untuk pembeli. Tokomu dibuka setelah identitasmu diverifikasi, "
              "dan aku kabari di sini. Sementara itu, kirim foto dan keterangan produkmu dan aku buatkan katalognya.{hold}",
    },
    "signup_hold_note": {
        "en": " Your address is held for {hours} hours.", "id": " Alamatmu ditahan selama {hours} jam.",
    },
    "signup_released": {
        "en": "Your shop address {slug} was given back because the shop was not verified within {hours} hours. "
              "Send any message here to choose an address again.",
        "id": "Alamat tokomu {slug} dikembalikan karena toko belum diverifikasi dalam {hours} jam. "
              "Kirim pesan apa saja di sini untuk memilih alamat lagi.",
    },
    "shop_opened": {
        "en": "Good news: your shop is verified and open. Buyers and AI agents can find your products now: {url}",
        "id": "Kabar baik: tokomu sudah terverifikasi dan dibuka. Pembeli dan agen AI sekarang bisa menemukan produkmu: {url}",
    },
    "published_pending": {
        "en": "Done! {n} new product(s) saved ({total} in your shop). Your shop is not open to buyers yet: it opens once your "
              "identity is verified, and I'll tell you here.",
        "id": "Beres! {n} produk baru tersimpan ({total} di tokomu). Tokomu belum dibuka untuk pembeli: baru dibuka setelah "
              "identitasmu diverifikasi, dan aku kabari di sini.",
    },
}


class Translator:
    def __init__(self, locale: str = "en") -> None:
        self.locale = locale if locale in ("en", "id") else "en"

    def __call__(self, key: str, **kwargs) -> str:
        entry = MESSAGES[key]
        return entry.get(self.locale, entry["en"]).format(**kwargs)
