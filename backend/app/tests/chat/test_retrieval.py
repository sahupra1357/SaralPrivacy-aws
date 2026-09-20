"""BM25 + Pinecone retrieval. Ported from frontend/lib/chat/retrieve.test.ts, plus the
Pinecone paths that used to live in pinecone.ts.

The real corpus (app/data/chat-index.json) is read — it is a checked-in build artefact,
not a network call, and the confidence floor is only meaningful against real content.
"""

from typing import Any

import httpx
import pytest

from app.services import retrieval
from app.services.retrieval import ChatChunk, RetrievalResult, RetrievedChunk


def test_stem_folds_related_word_forms() -> None:
    assert retrieval.stem("deletion") == retrieval.stem("delete")
    assert retrieval.stem("notices") == retrieval.stem("notice")


def test_tokenize_drops_stopwords_and_single_characters() -> None:
    assert retrieval.tokenize("What is the consent?") == [retrieval.stem("consent")]


def test_tokenize_returns_nothing_for_a_stopword_only_query() -> None:
    assert retrieval.tokenize("what is the") == []


def test_index_loads_the_whole_corpus() -> None:
    engine = retrieval.load_index()
    assert len(engine.chunks) > 300
    assert all(c.url.startswith("/") for c in engine.chunks)


def test_retrieve_is_confident_about_a_core_question() -> None:
    result = retrieval.retrieve("What must my privacy notice say under DPDPA?")
    assert result.confidence == "high"
    assert result.hits
    assert result.hits[0].score > 0


def test_retrieve_is_not_confident_about_an_off_corpus_question() -> None:
    result = retrieval.retrieve("how do I bake sourdough bread at home")
    assert result.confidence == "low"


def test_retrieve_returns_nothing_for_an_empty_query() -> None:
    result = retrieval.retrieve("   ")
    assert result.hits == []
    assert result.confidence == "low"
    assert result.matched_term_ratio == 0.0


def test_retrieve_respects_top_k() -> None:
    assert len(retrieval.retrieve("consent notice rights", top_k=3).hits) <= 3


def test_platform_tour_puts_the_overview_first_and_is_always_confident() -> None:
    result = retrieval.platform_tour()
    assert result.confidence == "high"
    assert result.hits[0].chunk.id == "tool:platform-overview"
    assert all(h.chunk.id.startswith("tool:") for h in result.hits)


def _chunk(cid: str, **kw: Any) -> ChatChunk:
    base: dict[str, Any] = {
        "id": cid,
        "url": f"/{cid}",
        "title": cid,
        "tier": 1,
        "topic_tags": [],
        "section": "s",
        "text": "t",
    }
    base.update(kw)
    return ChatChunk(**base)


def test_boost_factor_prefers_the_matching_industry_and_penalises_others() -> None:
    mine = _chunk("a", industry="ca-firms")
    theirs = _chunk("b", industry="pharmacies")
    assert retrieval.boost_factor(mine, industry="ca-firms") > retrieval.boost_factor(
        theirs, industry="ca-firms"
    )


def test_boost_factor_keeps_tier_1_above_tier_4() -> None:
    assert retrieval.boost_factor(_chunk("a", tier=1)) > retrieval.boost_factor(_chunk("b", tier=4))


def test_fuse_retrieval_falls_back_to_lexical_when_there_is_no_vector_result() -> None:
    lexical = RetrievalResult(hits=[RetrievedChunk(_chunk("a"), 1.0)], confidence="high")
    assert retrieval.fuse_retrieval(None, lexical) is lexical
    assert retrieval.fuse_retrieval(RetrievalResult(hits=[]), lexical) is lexical


def test_fuse_retrieval_lets_either_retriever_carry_confidence() -> None:
    vector = RetrievalResult(hits=[RetrievedChunk(_chunk("a"), 0.9)], confidence="high")
    lexical = RetrievalResult(hits=[RetrievedChunk(_chunk("b"), 4.0)], confidence="low")
    fused = retrieval.fuse_retrieval(vector, lexical)
    assert fused.confidence == "high"
    assert {h.chunk.id for h in fused.hits} == {"a", "b"}
    # Semantic leads: rank 1 of the weighted list outranks rank 1 of the corroborator.
    assert fused.hits[0].chunk.id == "a"


# ── Pinecone ──────────────────────────────────────────────────────────────────
def test_pinecone_is_unconfigured_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retrieval.settings, "PINECONE_API_KEY", "")
    assert retrieval.is_pinecone_configured() is False
    assert retrieval.pinecone_search("consent") is None
    assert retrieval.pinecone_stats() is None


def test_pinecone_search_maps_hits_and_applies_the_confidence_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(retrieval.settings, "PINECONE_API_KEY", "pc-test-key-123456")
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kw: Any) -> httpx.Response:
        captured["url"] = url
        captured["json"] = kw.get("json")
        return httpx.Response(
            200,
            json={
                "result": {
                    "hits": [
                        {
                            "_id": "learn:consent:lead",
                            "_score": 0.71,
                            "fields": {
                                "url": "/learn/consent",
                                "title": "Consent under DPDPA",
                                "section": "Summary",
                                "tier": 1,
                                "topicTags": ["consent"],
                                "chunk_text": "Consent must be free and specific.",
                                "extraction": "typed",
                            },
                        }
                    ]
                }
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(retrieval.httpx, "post", fake_post)
    result = retrieval.pinecone_search("do I need consent", industry="ca-firms")

    assert result is not None
    assert result.confidence == "high"
    assert result.hits[0].chunk.url == "/learn/consent"
    assert result.hits[0].chunk.topic_tags == ["consent"]
    assert captured["url"].endswith("/records/namespaces/content/search")
    # The REST API is snake_case, and sector questions must still see cross-cutting pages.
    assert captured["json"]["query"]["top_k"] == 24
    assert captured["json"]["rerank"]["top_n"] == 6
    assert captured["json"]["query"]["filter"]["$or"][1] == {"industry": {"$exists": False}}


def test_pinecone_search_below_the_floor_is_low_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retrieval.settings, "PINECONE_API_KEY", "pc-test-key-123456")
    monkeypatch.setattr(
        retrieval.httpx,
        "post",
        lambda url, **kw: httpx.Response(
            200,
            json={"result": {"hits": [{"_id": "x", "_score": 0.01, "fields": {"url": "/faq"}}]}},
            request=httpx.Request("POST", url),
        ),
    )
    result = retrieval.pinecone_search("something vague")
    assert result is not None
    assert result.confidence == "low"


def test_pinecone_search_returns_none_on_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retrieval.settings, "PINECONE_API_KEY", "pc-test-key-123456")

    def boom(_url: str, **_kw: Any) -> httpx.Response:
        raise httpx.ConnectTimeout("nope")

    monkeypatch.setattr(retrieval.httpx, "post", boom)
    assert retrieval.pinecone_search("consent") is None


def test_pinecone_search_returns_none_on_an_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retrieval.settings, "PINECONE_API_KEY", "pc-test-key-123456")
    monkeypatch.setattr(
        retrieval.httpx,
        "post",
        lambda url, **kw: httpx.Response(401, text="bad key", request=httpx.Request("POST", url)),
    )
    assert retrieval.pinecone_search("consent") is None


def test_pinecone_stats_reads_the_content_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retrieval.settings, "PINECONE_API_KEY", "pc-test-key-123456")
    monkeypatch.setattr(
        retrieval.httpx,
        "post",
        lambda url, **kw: httpx.Response(
            200,
            json={"namespaces": {"content": {"recordCount": 406}}},
            request=httpx.Request("POST", url),
        ),
    )
    assert retrieval.pinecone_stats() == {"vectorCount": 406}
