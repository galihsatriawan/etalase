"""Gemini adapter contract: what we send (model, media types, config), what we accept back, and what happens on failure.

The Gemini API itself is not called; the client is replaced by a recorder. `make doctor` makes the one real call.
"""

import asyncio
from types import SimpleNamespace

import pytest

from etalase.catalog import extractor as ex
from etalase.catalog.extractor import (ExtractedCatalog, ExtractedProduct, ExtractionError, FallbackExtractor,
                                       GeminiExtractor, HeuristicExtractor)
from etalase.config import Settings


class Recorder:
    """Stands in for genai.Client().models: remembers the request and answers with a canned response."""

    def __init__(self, response):
        self.response, self.calls = response, []
        self.models = self

    def generate_content(self, **kw):
        self.calls.append(kw)
        return self.response


def answer(text=None, parsed=None, block=None, finish=None):
    return SimpleNamespace(text=text, parsed=parsed, prompt_feedback=SimpleNamespace(block_reason=block),
                           candidates=[SimpleNamespace(finish_reason=finish)] if finish else [])


GOOD_JSON = ('{"products": [{"title": "Voal Hijab", "price": 85000, "variants": '
             '[{"color": "Black", "stock": 5}]}], "questions": []}')


def extractor(model="gemini-3.8-flash", response=None):
    g = GeminiExtractor(model, api_key="test-key")
    g.client = Recorder(response if response is not None else answer(text=GOOD_JSON))
    return g


# ---------------------------------------------------------------- settings
def test_default_model_is_one_new_projects_can_use():
    assert Settings(_env_file=None).gemini_model == "gemini-3.8-flash"


@pytest.mark.parametrize("name", ["GOOGLE_API_KEY", "GEMINI_API_KEY"])
def test_both_spellings_of_the_api_key_are_accepted(monkeypatch, name):
    monkeypatch.setenv(name, "abc")
    assert Settings(_env_file=None).google_api_key == "abc"


def test_google_api_key_wins_when_both_are_set(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "first")
    monkeypatch.setenv("GEMINI_API_KEY", "second")
    assert Settings(_env_file=None).google_api_key == "first"


@pytest.mark.parametrize("name", ["GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_ENTERPRISE"])
def test_both_spellings_of_the_vertex_switch_are_accepted(monkeypatch, name):
    monkeypatch.setenv(name, "true")
    assert Settings(_env_file=None).google_genai_use_vertexai is True


def test_keyword_construction_still_works():
    assert Settings(_env_file=None, google_api_key="k").gemini_enabled


def test_env_file_values_are_read_through_the_aliases(tmp_path):
    f = tmp_path / ".env"
    f.write_text("GEMINI_API_KEY=from-file\nGEMINI_MODEL=gemini-3.5-flash-lite\n")
    s = Settings(_env_file=str(f))
    assert s.google_api_key == "from-file" and s.gemini_model == "gemini-3.5-flash-lite"


# ---------------------------------------------------------------- request
def test_request_uses_json_schema_and_the_configured_model():
    g = extractor()
    g.extract(["Voal hijab black 5 85k"])
    call = g.client.calls[0]
    assert call["model"] == "gemini-3.8-flash"
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_schema is ExtractedCatalog


def test_gemini_3_runs_at_its_default_temperature_older_models_stay_deterministic():
    new, old = extractor("gemini-3.8-flash"), extractor("gemini-2.5-flash")
    new.extract(["x"])
    old.extract(["x"])
    assert new.client.calls[0]["config"].temperature is None
    assert old.client.calls[0]["config"].temperature == 0.1


def test_retries_are_configured_on_the_client(monkeypatch):
    seen = {}

    class FakeClient:
        def __init__(self, **kw):
            seen.update(kw)

    from google import genai
    monkeypatch.setattr(genai, "Client", FakeClient)
    GeminiExtractor("gemini-3.8-flash", api_key="k")
    retry = seen["http_options"].retry_options
    assert retry.attempts >= 3
    assert {429, 503} <= set(retry.http_status_codes)
    assert seen["api_key"] == "k"


def test_vertex_client_gets_project_and_location(monkeypatch):
    seen = {}

    class FakeClient:
        def __init__(self, **kw):
            seen.update(kw)

    from google import genai
    monkeypatch.setattr(genai, "Client", FakeClient)
    GeminiExtractor("gemini-3.8-flash", use_vertex=True, project="p", location="global")
    assert seen["vertexai"] is True and seen["project"] == "p" and seen["location"] == "global"
    assert "api_key" not in seen


def part_kinds(call):
    return [(getattr(p, "inline_data", None) and p.inline_data.mime_type) or "text" for p in call["contents"][0].parts]


def test_whatsapp_voice_note_is_sent_as_ogg(tmp_path):
    voice = tmp_path / "note.ogg"
    voice.write_bytes(b"OggS" + b"\0" * 100)
    g = extractor()
    g.extract(["hi"], audio_paths=[str(voice)])
    assert "audio/ogg" in part_kinds(g.client.calls[0])


@pytest.mark.parametrize("suffix,mime", [(".mp3", "audio/mpeg"), (".m4a", "audio/m4a"), (".wav", "audio/wav"),
                                         (".opus", "audio/opus"), (".png", "image/png"), (".jpg", "image/jpeg"),
                                         (".webp", "image/webp")])
def test_supported_media_types_map_to_the_documented_mime(tmp_path, suffix, mime):
    f = tmp_path / f"file{suffix}"
    f.write_bytes(b"x" * 10)
    g = extractor()
    kw = {"image_paths": [str(f)]} if mime.startswith("image") else {"audio_paths": [str(f)]}
    g.extract(["hi"], **kw)
    assert mime in part_kinds(g.client.calls[0])


def test_unsupported_audio_is_skipped_and_the_seller_is_told(tmp_path):
    amr = tmp_path / "voice.amr"
    amr.write_bytes(b"#!AMR\n" + b"\0" * 50)
    g = extractor()
    result = g.extract(["hi"], audio_paths=[str(amr)])
    assert part_kinds(g.client.calls[0]) == ["text", "text"]          # nothing was uploaded
    assert any("voice.amr" in q for q in result.questions)


def test_files_beyond_the_request_size_budget_are_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "MAX_INLINE_BYTES", 100)
    small, big = tmp_path / "a.png", tmp_path / "b.png"
    small.write_bytes(b"x" * 60)
    big.write_bytes(b"x" * 60)                                         # 60 + 60 > 100
    g = extractor()
    result = g.extract(["hi"], image_paths=[str(small), str(big)])
    assert part_kinds(g.client.calls[0]).count("image/png") == 1
    assert any("b.png" in q for q in result.questions)


# ---------------------------------------------------------------- response
def test_parsed_object_is_used_when_the_sdk_provides_one():
    obj = ExtractedCatalog(products=[ExtractedProduct(title="X", price=1000)])
    assert extractor(response=answer(parsed=obj)).extract(["x"]) is obj


def test_json_text_is_validated_into_a_catalog():
    result = extractor().extract(["x"])
    assert result.products[0].title == "Voal Hijab" and result.products[0].price == 85000


def test_json_wrapped_in_a_code_fence_is_accepted():
    result = extractor(response=answer(text="```json\n" + GOOD_JSON + "\n```")).extract(["x"])
    assert result.products[0].title == "Voal Hijab"


def test_blocked_or_empty_answer_raises_a_readable_error():
    with pytest.raises(ExtractionError, match="SAFETY"):
        extractor(response=answer(text=None, block="SAFETY")).extract(["x"])
    with pytest.raises(ExtractionError, match="MAX_TOKENS"):
        extractor(response=answer(text="", finish="MAX_TOKENS")).extract(["x"])


def test_json_that_does_not_match_the_schema_raises():
    with pytest.raises(ExtractionError):
        extractor(response=answer(text='{"products": "none"}')).extract(["x"])


# ---------------------------------------------------------------- fallback
class Broken:
    def extract(self, texts, image_paths=None, audio_paths=None):
        raise RuntimeError("429 RESOURCE_EXHAUSTED")


def test_fallback_uses_the_offline_rules_when_gemini_fails():
    result = FallbackExtractor(Broken(), HeuristicExtractor()).extract(["Voal hijab black 5 navy 3 price 85k"])
    assert result.products and result.products[0].price == 85000


def test_fallback_does_not_touch_the_second_extractor_when_gemini_works():
    class Boom:
        def extract(self, *a, **k):
            raise AssertionError("fallback must not run")
    good = ExtractedCatalog(products=[ExtractedProduct(title="Ok", price=5000)])

    class First:
        def extract(self, *a, **k):
            return good
    assert FallbackExtractor(First(), Boom()).extract(["x"]) is good


def test_container_wraps_gemini_in_the_fallback_but_offline_stays_plain():
    from etalase.container import build_context
    assert isinstance(build_context(Settings(etalase_env="dev", _env_file=None)).catalog.extractor, HeuristicExtractor)
    ctx = build_context(Settings(etalase_env="dev", _env_file=None, google_api_key="k"))
    assert isinstance(ctx.catalog.extractor, FallbackExtractor)
    assert isinstance(ctx.catalog.extractor.primary, GeminiExtractor)


# ---------------------------------------------------------------- seller agent failure
def test_seller_hears_something_when_the_agent_fails_and_the_message_is_not_replayed(ctx):
    from tests.conftest import make_seller

    class Failing:
        async def respond(self, seller_id, text):
            raise RuntimeError("503 UNAVAILABLE")

    seller = make_seller(ctx)
    ctx.seller_agent = Failing()
    replayed = []
    inbox = ctx.inbox
    inbox.handle_intent = lambda *a, **k: replayed.append(a)      # would double-apply a restock if it ran
    ctx.messenger.outbox.clear()
    asyncio.run(inbox.handle_text(seller.id, "restock black 5"))
    assert not replayed
    assert "try again" in ctx.messenger.outbox[-1].text


def test_seller_hears_something_when_the_agent_returns_nothing(ctx):
    from tests.conftest import make_seller

    class Silent:
        async def respond(self, seller_id, text):
            return ""

    seller = make_seller(ctx)
    ctx.seller_agent = Silent()
    ctx.messenger.outbox.clear()
    asyncio.run(ctx.inbox.handle_text(seller.id, "hello"))
    assert "try again" in ctx.messenger.outbox[-1].text


# ---------------------------------------------------------------- both auth modes
def _backends(settings):
    """Which Gemini backend the extractor and the ADK agents really end up on (no network call is made)."""
    from google.adk.models.google_llm import Gemini
    from etalase.config import export_google_env
    from etalase.container import build_context

    export_google_env(settings)
    ctx = build_context(settings)
    extractor_vertex = bool(ctx.catalog.extractor.primary.client._api_client.vertexai)      # None means AI Studio
    agent_vertex = bool(Gemini(model=settings.gemini_model).api_client._api_client.vertexai)
    return extractor_vertex, agent_vertex, ctx


def test_api_key_mode_uses_ai_studio_for_extractor_and_agents():
    extractor_vertex, agent_vertex, ctx = _backends(Settings(etalase_env="dev", _env_file=None, google_api_key="k"))
    assert (extractor_vertex, agent_vertex) == (False, False)
    assert ctx.seller_agent is not None


def test_vertex_mode_uses_vertex_for_extractor_and_agents(monkeypatch):
    import google.auth
    from google.auth.credentials import AnonymousCredentials
    monkeypatch.setattr(google.auth, "default", lambda *a, **k: (AnonymousCredentials(), "demo"))   # no ADC here
    s = Settings(etalase_env="dev", _env_file=None, google_genai_use_vertexai=True,
                 google_cloud_project="demo", google_cloud_location="global")
    extractor_vertex, agent_vertex, ctx = _backends(s)
    assert (extractor_vertex, agent_vertex) == (True, True)
    assert ctx.seller_agent is not None


def test_vertex_wins_when_both_are_configured(monkeypatch):
    import google.auth
    from google.auth.credentials import AnonymousCredentials
    monkeypatch.setattr(google.auth, "default", lambda *a, **k: (AnonymousCredentials(), "demo"))
    s = Settings(etalase_env="dev", _env_file=None, google_api_key="k", google_genai_use_vertexai=True,
                 google_cloud_project="demo", google_cloud_location="global")
    assert _backends(s)[:2] == (True, True)
