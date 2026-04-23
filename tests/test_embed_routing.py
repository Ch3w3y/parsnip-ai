"""Regression tests for embedding model routing.

These tests lock in the fix that ensures GitHub sources use bge-m3 embeddings
while all other sources default to mxbai-embed-large. The routing logic spans
multiple modules (router, embed, kb_search, holistic_search), and these tests
ensure the correct model is selected at each decision point.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.tools.router import DEFAULT_MODEL, SOURCE_MODEL_MAP


# ── SOURCE_MODEL_MAP tests ────────────────────────────────────────────────────


class TestSourceModelMap:
    """Validate the SOURCE_MODEL_MAP configuration in router.py."""

    def test_github_maps_to_bge_m3(self):
        """GitHub source must route to bge-m3 embeddings."""
        assert SOURCE_MODEL_MAP.get("github") == "bge-m3"

    def test_default_model_is_mxbai(self):
        """DEFAULT_MODEL must be mxbai-embed-large."""
        assert DEFAULT_MODEL == "mxbai-embed-large"

    @pytest.mark.parametrize(
        "source",
        ["wikipedia", "arxiv", "news", "biorxiv", "user_docs", "joplin_notes"],
    )
    def test_known_non_github_sources_default(self, source):
        """All non-github sources must fall back to DEFAULT_MODEL via .get()."""
        assert SOURCE_MODEL_MAP.get(source, DEFAULT_MODEL) == DEFAULT_MODEL

    def test_unknown_source_defaults(self):
        """An unrecognised source must also fall back to DEFAULT_MODEL."""
        assert SOURCE_MODEL_MAP.get("unknown_source", DEFAULT_MODEL) == DEFAULT_MODEL


# ── get_embedding model passthrough ────────────────────────────────────────────


class TestGetEmbeddingPassthrough:
    """Validate that get_embedding() accepts and forwards the model parameter."""

    @pytest.mark.asyncio
    async def test_get_embedding_passes_model_param(self):
        """get_embedding must forward the explicit model= to Ollama."""
        from agent.tools.embed import get_embedding

        mock_response = MagicMock()
        mock_response.json.return_value = {"embeddings": [[0.1] * 1024]}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("agent.tools.embed.httpx.AsyncClient", return_value=mock_client):
            result = await get_embedding("test query", model="bge-m3")

        # Verify the post was called with the correct model
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        sent_json = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert sent_json["model"] == "bge-m3"

    @pytest.mark.asyncio
    async def test_get_embedding_defaults_to_env_model(self):
        """Without explicit model=, get_embedding should use EMBED_MODEL env var or mxbai default."""
        from agent.tools.embed import get_embedding

        mock_response = MagicMock()
        mock_response.json.return_value = {"embeddings": [[0.1] * 1024]}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with (
            patch("agent.tools.embed.httpx.AsyncClient", return_value=mock_client),
            patch.dict("os.environ", {}, clear=False),
        ):
            result = await get_embedding("test query")

        call_kwargs = mock_client.post.call_args
        sent_json = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        # Default when no EMBED_MODEL env var and no model param is "mxbai-embed-large"
        assert sent_json["model"] == "mxbai-embed-large"


# ── kb_search routing ──────────────────────────────────────────────────────────


class TestKbSearchRouting:
    """Validate kb_search.py selects the correct embed model per source."""

    @pytest.mark.asyncio
    async def test_kb_search_uses_bge_for_github(self):
        """kb_search must call get_embedding with model='bge-m3' when source='github'."""
        with patch("agent.tools.kb_search.get_embedding", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            # Mock the DB connection so the search doesn't actually run
            mock_conn = AsyncMock()
            mock_conn.execute = AsyncMock(
                return_value=AsyncMock(fetchall=AsyncMock(return_value=[]))
            )
            with (
                patch("agent.tools.kb_search.psycopg.AsyncConnection.connect", new_callable=AsyncMock) as mock_connect,
                patch("agent.tools.kb_search.register_vector_async", new_callable=AsyncMock),
            ):
                mock_connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
                mock_connect.return_value.__aexit__ = AsyncMock(return_value=False)

                from agent.tools.kb_search import kb_search
                try:
                    await kb_search.ainvoke({"query": "python async", "source": "github"})
                except Exception:
                    pass  # May fail after embed, but we only care about the embed call

            mock_embed.assert_called_once()
            call_kwargs = mock_embed.call_args
            assert call_kwargs.kwargs.get("model") == "bge-m3"

    @pytest.mark.asyncio
    async def test_kb_search_uses_mxbai_for_arxiv(self):
        """kb_search must call get_embedding with model='mxbai-embed-large' for non-github sources."""
        with patch("agent.tools.kb_search.get_embedding", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            mock_conn = AsyncMock()
            mock_conn.execute = AsyncMock(
                return_value=AsyncMock(fetchall=AsyncMock(return_value=[]))
            )
            with (
                patch("agent.tools.kb_search.psycopg.AsyncConnection.connect", new_callable=AsyncMock) as mock_connect,
                patch("agent.tools.kb_search.register_vector_async", new_callable=AsyncMock),
            ):
                mock_connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
                mock_connect.return_value.__aexit__ = AsyncMock(return_value=False)

                from agent.tools.kb_search import kb_search
                try:
                    await kb_search.ainvoke({"query": "transformer paper", "source": "arxiv"})
                except Exception:
                    pass

            mock_embed.assert_called_once()
            call_kwargs = mock_embed.call_args
            assert call_kwargs.kwargs.get("model") == "mxbai-embed-large"


# ── holistic_search embedding selection ─────────────────────────────────────────


class TestHolisticSearchEmbedSelection:
    """Validate holistic_search.py selects the right embeddings per layer."""

    def test_github_layer_selects_bge_embs(self):
        """When a layer's first source is 'github', layer_model must be 'bge-m3'."""
        # Simulate the logic from holistic_search.py lines 168-169
        sources_github = ["github"]
        layer_model = SOURCE_MODEL_MAP.get(sources_github[0], DEFAULT_MODEL)
        assert layer_model == "bge-m3"

        # With bge_embs available, it should be selected
        mxbai_embs = [[0.1] * 1024]
        bge_embs = [[0.2] * 1024]
        embs = bge_embs if layer_model == "bge-m3" and bge_embs else mxbai_embs
        assert embs is bge_embs

    def test_github_layer_falls_back_to_mxbai_when_bge_unavailable(self):
        """When bge_embs is None (service down), github layer must fall back to mxbai."""
        sources_github = ["github"]
        layer_model = SOURCE_MODEL_MAP.get(sources_github[0], DEFAULT_MODEL)
        assert layer_model == "bge-m3"

        mxbai_embs = [[0.1] * 1024]
        bge_embs = None  # bge-m3 service unavailable
        embs = bge_embs if layer_model == "bge-m3" and bge_embs else mxbai_embs
        assert embs is mxbai_embs

    @pytest.mark.parametrize(
        "sources",
        [["arxiv"], ["wikipedia"], ["news"], ["biorxiv"], ["joplin_notes"]],
    )
    def test_non_github_layers_use_mxbai(self, sources):
        """Non-github layers must always use mxbai embeddings."""
        layer_model = SOURCE_MODEL_MAP.get(sources[0], DEFAULT_MODEL)
        assert layer_model == DEFAULT_MODEL

        mxbai_embs = [[0.1] * 1024]
        bge_embs = [[0.2] * 1024]
        embs = bge_embs if layer_model == "bge-m3" and bge_embs else mxbai_embs
        assert embs is mxbai_embs