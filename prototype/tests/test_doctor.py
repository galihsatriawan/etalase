"""The setup doctor: every service reports skip / ok / fail with a hint, and never leaks a secret."""

import httpx

from etalase import doctor
from etalase.catalog.extractor import ExtractedCatalog, ExtractedProduct, ExtractedVariant
from etalase.config import Settings


def settings(**kw) -> Settings:
    return Settings(_env_file=None, etalase_env="dev", **kw)


class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload or {}, text

    def json(self):
        return self._payload


def by_name(checks):
    return {c.name: c for c in checks}


def good_extractor(_settings):
    class E:
        def extract(self, texts, image_paths=None, audio_paths=None):
            return ExtractedCatalog(products=[ExtractedProduct(
                title="Voal Hijab", price=85000,
                variants=[ExtractedVariant(color="Black", stock=5), ExtractedVariant(color="Navy", stock=3)])])
    return E()


def test_nothing_configured_runs_in_offline_mode_without_failures():
    checks = by_name(doctor.run_checks(settings(), live=True))
    assert checks["startup"].status == doctor.OK
    for name in ("gemini", "whatsapp", "xendit", "biteship", "instagram"):
        assert checks[name].status == doctor.SKIP, name
    assert checks["store"].status == doctor.OK


def test_gemini_ok_when_the_sample_is_read_correctly():
    s = settings(google_api_key="k")
    check = doctor.check_gemini(s, live=True, extractor_factory=good_extractor)
    assert check.status == doctor.OK and "85.000" in check.detail


def test_gemini_misread_is_a_warning_and_exceptions_are_failures_with_hints():
    class Wrong:
        def extract(self, *a, **k):
            return ExtractedCatalog(products=[ExtractedProduct(title="Hijab", price=8500)])
    s = settings(google_api_key="k")
    assert doctor.check_gemini(s, True, lambda _s: Wrong()).status == doctor.WARN

    def boom(_s):
        raise RuntimeError("400 API key not valid. Please pass a valid API key.")
    failed = doctor.check_gemini(s, True, boom)
    assert failed.status == doctor.FAIL and "aistudio" in failed.hint


def test_403_hint_depends_on_the_mode():
    def forbidden(_s):
        raise RuntimeError("403 Forbidden")
    api_key = doctor.check_gemini(settings(google_api_key="k"), True, forbidden)
    vertex = doctor.check_gemini(settings(google_genai_use_vertexai=True, google_cloud_project="p"), True, forbidden)
    assert "restrictions" in api_key.hint and "aiplatform" in vertex.hint


def test_vertex_without_project_fails_early_and_offline_flag_skips_network():
    assert doctor.check_gemini(settings(google_genai_use_vertexai=True), True).status == doctor.FAIL
    c = doctor.check_gemini(settings(google_api_key="k"), live=False)
    assert c.status == doctor.INFO and "not tested" in c.detail


def test_whatsapp_needs_all_three_values_and_a_custom_verify_token():
    assert doctor.check_whatsapp(settings(whatsapp_token="t"), False).status == doctor.FAIL
    full = dict(whatsapp_token="t", whatsapp_phone_number_id="1", whatsapp_app_secret="s")
    assert "VERIFY" in doctor.check_whatsapp(settings(**full), False).detail
    ok = settings(**full, whatsapp_verify_token="a-long-random-verify-token")
    assert doctor.check_whatsapp(ok, live=False).status == doctor.INFO


def test_whatsapp_live_check_uses_the_graph_api(monkeypatch):
    seen = {}

    def fake_get(url, **kw):
        seen["url"], seen["auth"] = url, kw["headers"]["Authorization"]
        return FakeResponse(200, {"display_phone_number": "+62 812", "verified_name": "Etalase"})
    monkeypatch.setattr(httpx, "get", fake_get)
    s = settings(whatsapp_token="TOKEN", whatsapp_phone_number_id="123", whatsapp_app_secret="s",
                 whatsapp_verify_token="a-long-random-verify-token")
    check = doctor.check_whatsapp(s, live=True)
    assert check.status == doctor.OK and "+62 812" in check.detail
    assert seen["url"].endswith("/123") and seen["auth"] == "Bearer TOKEN"

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(401, text="Invalid OAuth access token"))
    bad = doctor.check_whatsapp(s, live=True)
    assert bad.status == doctor.FAIL and "System User" in bad.hint
    assert "TOKEN" not in bad.detail  # secrets never appear in output


def test_xendit_requires_callback_token_flags_live_keys_and_checks_balance(monkeypatch):
    assert doctor.check_xendit(settings(xendit_secret_key="xnd_development_x"), False).status == doctor.FAIL
    live_key = settings(xendit_secret_key="xnd_production_x", xendit_callback_token="c")
    assert doctor.check_xendit(live_key, False).status == doctor.WARN
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(200, {"balance": 0}))
    ok = settings(xendit_secret_key="xnd_development_x", xendit_callback_token="c")
    check = doctor.check_xendit(ok, True)
    assert check.status == doctor.OK and "test" in check.detail
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(401, text="invalid key"))
    assert doctor.check_xendit(ok, True).status == doctor.FAIL


def test_biteship_uses_the_real_adapter(monkeypatch):
    def fake_post(url, **kw):
        assert url.endswith("/v1/rates/couriers")
        return FakeResponse(200, {"pricing": [{"courier_code": "jne", "courier_service_code": "reg", "price": 12000,
                                               "duration": "2-3 days"}]})
    fake = FakeResponse(200)
    fake.raise_for_status = lambda: None
    fake.json = lambda: {"pricing": [{"courier_code": "jne", "courier_service_code": "reg", "price": 12000,
                                      "duration": "2-3 days"}]}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: fake)
    check = doctor.check_biteship(settings(biteship_api_key="k"), live=True)
    assert check.status == doctor.OK and "jne" in check.detail

    def broken(*a, **k):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(httpx, "post", broken)
    assert doctor.check_biteship(settings(biteship_api_key="k"), live=True).status == doctor.FAIL


def test_instagram_needs_both_values():
    assert doctor.check_instagram(settings(instagram_app_id="1")).status == doctor.FAIL
    assert doctor.check_instagram(settings(instagram_app_id="1", instagram_app_secret="2")).status == doctor.INFO


def test_insecure_public_config_is_reported_by_the_startup_check():
    s = settings(public_base_url="https://etalase.example")
    check = doctor.check_startup(s)
    assert check.status == doctor.FAIL and "DEV_TOKEN" in check.detail


def test_render_summarises_and_exit_code_follows_failures(capsys):
    checks = [doctor.Check("a", doctor.OK, "fine"), doctor.Check("b", doctor.FAIL, "broken", "fix it")]
    text = doctor.render(checks)
    assert "[FAIL] b" in text and "-> fix it" in text and "1 failing" in text
    assert doctor.main(["--offline", "--json"]) == 0
    assert '"name": "startup"' in capsys.readouterr().out
