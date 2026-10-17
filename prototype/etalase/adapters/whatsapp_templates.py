"""The WhatsApp message templates Etalase needs, in one place.

WhatsApp only lets a business send free-form messages inside the 24 hours after the customer last wrote to it.
Everything else (a new paid order, a stock check, a return request, a payout notice) must be a pre-approved template, otherwise the
Cloud API answers error 131047 and the seller is never told. WhatsAppCloudMessenger tries the free-form message first
and falls back to these templates only when the window is closed.

Create each template once in WhatsApp Manager (Message templates > Create template), category Utility, with the exact
name, language and text below; review can take up to 24 hours. Print them ready to copy with:

    python -m etalase.adapters.whatsapp_templates

Variables ({{1}}, {{2}}, ...) are filled in order. WhatsApp rejects variables that contain line breaks, tabs or runs of
more than four spaces, so values are flattened before sending (see clean_param).
"""

from __future__ import annotations

NAME_PREFIX = "etalase_"
LANGUAGES = ("en", "id")

TEMPLATES: dict[str, dict] = {
    "stock_check": {
        "body": {"en": "Quick stock check. {{1}} for a buyer in {{2}}. Is it still available?",
                 "id": "Cek stok dulu ya. {{1}} untuk pembeli di {{2}}. Barangnya masih ada?"},
        "buttons": {"en": ["Available", "Sold out"], "id": ["Ada", "Habis"]},
        "example": ["Voal Hijab (Black) x 2", "Bandung"],
    },
    "stock_check_expired": {
        "body": {"en": "The stock check for {{1}} (buyer in {{2}}) ran out of time because there was no answer. The buyer was not charged.",
                 "id": "Cek stok untuk {{1}} (pembeli di {{2}}) kedaluwarsa karena tidak ada jawaban. Pembeli tidak ditagih."},
        "buttons": {"en": [], "id": []},
        "example": ["Voal Hijab (Black) x 2", "Bandung"],
    },
    "new_paid_order": {
        "body": {"en": "New paid order. {{1}}. {{2}}, ship to {{3}} via {{4}}.",
                 "id": "Pesanan baru (sudah dibayar). {{1}}. {{2}}, kirim ke {{3}} via {{4}}."},
        "buttons": {"en": ["Accept", "Out of stock"], "id": ["Terima", "Stok habis"]},
        "example": ["Voal Hijab (Black) x 2", "Rp170.000", "Bandung", "JNE REG"],
    },
    "paid_ship_now": {
        "body": {"en": "Payment received. {{1}}. Ship to {{2}} via {{3}}, then reply 'tracking' and the number.",
                 "id": "Pembayaran diterima. {{1}}. Kirim ke {{2}} via {{3}}, lalu balas 'resi' dan nomornya."},
        "buttons": {"en": [], "id": []},
        "example": ["Voal Hijab (Black) x 2", "Dewi, Jl. Dago 10, Bandung 40135", "JNE REG"],
    },
    "payout": {
        "body": {"en": "Parcel delivered. {{1}} is on its way to your account (commission {{2}}).",
                 "id": "Paket sudah diterima pembeli. {{1}} dicairkan ke rekeningmu (komisi {{2}})."},
        "buttons": {"en": [], "id": []},
        "example": ["Rp163.200", "Rp6.800"],
    },
    "return_requested": {
        "body": {"en": "Return request for {{1}}. Reason: {{2}}. Buyer's note: {{3}}. Approve or dispute within {{4}} hours; no answer counts as approval.",
                 "id": "Pengajuan retur untuk {{1}}. Alasan: {{2}}. Catatan pembeli: {{3}}. Setujui atau sengketakan dalam {{4}} jam; kalau tidak dijawab dianggap disetujui."},
        "buttons": {"en": ["Approve return", "Dispute"], "id": ["Setujui retur", "Sengketakan"]},
        "example": ["Voal Hijab (Black) x 2", "damaged", "The seam is torn", "48"],
    },
    "return_approved": {
        "body": {"en": "Return approved for {{1}}. When the parcel is back with you, tap the button to start the buyer's refund. If nothing arrives within {{2}} days the return lapses.",
                 "id": "Retur {{1}} disetujui. Kalau paketnya sudah kembali ke kamu, tekan tombol untuk memproses refund pembeli. Kalau tidak ada paket dalam {{2}} hari, retur hangus."},
        "buttons": {"en": ["Item received"], "id": ["Barang diterima"]},
        "example": ["Voal Hijab (Black) x 2", "7"],
    },
}


def clean_param(value: str) -> str:
    """WhatsApp template variables may not contain newlines, tabs or more than four spaces in a row."""
    return " ".join(str(value).split())[:512]


def template_name(key: str) -> str:
    return f"{NAME_PREFIX}{key}"


def render_markdown() -> str:
    out = ["# WhatsApp templates to create (category: Utility)\n"]
    for key, spec in TEMPLATES.items():
        for lang in LANGUAGES:
            out.append(f"## `{template_name(key)}` / `{lang}`\n")
            out.append(f"- Body: {spec['body'][lang]}")
            if spec["buttons"][lang]:
                out.append("- Buttons (type Quick reply): " + ", ".join(f"`{b}`" for b in spec["buttons"][lang]))
            out.append("- Sample values: " + ", ".join(f"`{e}`" for e in spec["example"]) + "\n")
    return "\n".join(out)


if __name__ == "__main__":
    print(render_markdown())
