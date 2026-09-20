"""Regression tests for the local-endpoint auxiliary timeout floor.

A local model server (llama.cpp / LlamaForge, Ollama, LM Studio, vLLM) serves
one set of weights on one GPU, so concurrent callers are *queued*, not
parallelised.  An auxiliary call issued while the user's own chat turn is still
decoding waits for that turn to finish before it starts — routinely longer than
the 30 s ``_DEFAULT_AUX_TIMEOUT``, which assumes a cloud endpoint that answers a
small request immediately.

Observed symptom (agent.log, localhost:1234): ``Auxiliary title_generation:
using lmstudio ... at http://localhost:1234/v1/`` → ``Request timed out`` after
30 s → two same-provider retries → fallback to another model → ``Title
generation failed: Request timed out``.  The session was left untitled purely
because the naming request stood in line behind the conversation it was naming.

The fix layers a bounded floor on config-derived auxiliary timeouts when the
resolved route is a local endpoint, under the same constraints as the
compression floor:

  * Only local routes get the floor (cloud routes keep their own timeouts).
  * An explicit per-call ``timeout=`` override is **not** floored.
  * The floor is a minimum — a config value already above it is unchanged.
  * ``agent.local_aux_timeout`` in config.yaml overrides it; a non-positive
    value disables it.
  * Both the sync (``call_llm``) and async (``async_call_llm``) paths are
    covered.

These tests exercise the real ``call_llm`` / ``async_call_llm`` production
paths with a mocked LLM client and assert the timeout that actually reaches
``client.chat.completions.create``.
"""

from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from agent.auxiliary_client import call_llm, async_call_llm

# The committed bounded floor for config-derived timeouts on a local route.
# Behaviour contract (see AGENTS.md): an auxiliary call on a local endpoint
# must be allowed to queue for at least this long before it is abandoned.
LOCAL_TIMEOUT_FLOOR = 300.0

# The shipped default auxiliary timeout — what title generation ran into.
DEFAULT_AUX_TIMEOUT = 30.0

LOCAL_BASE_URL = "http://localhost:1234/v1"
CLOUD_BASE_URL = "https://api.openai.com/v1"


def _ok_response():
    return {"ok": True}


def _client_sync(base_url):
    client = MagicMock()
    client.base_url = base_url
    client.chat.completions.create.return_value = _ok_response()
    return client


def _client_async(base_url):
    client = MagicMock()
    client.base_url = base_url
    client.chat.completions.create = AsyncMock(return_value=_ok_response())
    return client


def _patches(client, *, task_timeout, base_url):
    """Common mocks: provider resolution, cached client, response validation,
    and the config-derived task timeout."""
    return (
        patch("agent.auxiliary_client._resolve_task_provider_model",
              return_value=("lmstudio", "wd-thinking", base_url, None, None)),
        patch("agent.auxiliary_client._get_cached_client",
              return_value=(client, "wd-thinking")),
        patch("agent.auxiliary_client._validate_llm_response",
              side_effect=lambda resp, _task, **_kw: resp),
        patch("agent.auxiliary_client._get_task_timeout",
              return_value=task_timeout),
    )


def _sent_timeout(client):
    return client.chat.completions.create.call_args.kwargs["timeout"]


class TestLocalTimeoutFloorSync:
    """Sync ``call_llm`` applies the floor to config-derived local timeouts."""

    def test_title_generation_on_localhost_is_raised_to_floor(self):
        """The reported repro: a title request against localhost must be
        allowed to queue rather than time out at the 30 s default."""
        client = _client_sync(LOCAL_BASE_URL)
        with_patches = _patches(client, task_timeout=DEFAULT_AUX_TIMEOUT,
                                base_url=LOCAL_BASE_URL)
        with with_patches[0], with_patches[1], with_patches[2], with_patches[3]:
            call_llm(
                task="title_generation",
                messages=[{"role": "user", "content": "name this chat"}],
            )
        timeout = _sent_timeout(client)
        assert timeout >= LOCAL_TIMEOUT_FLOOR, (
            f"local timeout {timeout} should be >= floor {LOCAL_TIMEOUT_FLOOR}"
        )
        assert timeout > DEFAULT_AUX_TIMEOUT, (
            "the too-low default must not pass through unchanged on a local route"
        )

    def test_cloud_route_is_not_floored(self):
        """Only local routes get the floor — a cloud endpoint keeps its own
        timeout so a dead cloud call still fails fast."""
        client = _client_sync(CLOUD_BASE_URL)
        with_patches = _patches(client, task_timeout=DEFAULT_AUX_TIMEOUT,
                                base_url=CLOUD_BASE_URL)
        with with_patches[0], with_patches[1], with_patches[2], with_patches[3]:
            call_llm(
                task="title_generation",
                messages=[{"role": "user", "content": "x"}],
            )
        assert _sent_timeout(client) == DEFAULT_AUX_TIMEOUT

    def test_explicit_per_call_timeout_is_not_floored(self):
        """An explicit per-call deadline is always honoured."""
        client = _client_sync(LOCAL_BASE_URL)
        explicit = 45.0
        with_patches = _patches(client, task_timeout=DEFAULT_AUX_TIMEOUT,
                                base_url=LOCAL_BASE_URL)
        with with_patches[0], with_patches[1], with_patches[2], with_patches[3]:
            call_llm(
                task="title_generation",
                messages=[{"role": "user", "content": "x"}],
                timeout=explicit,
            )
        assert _sent_timeout(client) == explicit

    def test_higher_config_timeout_is_kept(self):
        """The floor is a minimum, not an assignment."""
        client = _client_sync(LOCAL_BASE_URL)
        higher = LOCAL_TIMEOUT_FLOOR * 2
        with_patches = _patches(client, task_timeout=higher,
                                base_url=LOCAL_BASE_URL)
        with with_patches[0], with_patches[1], with_patches[2], with_patches[3]:
            call_llm(
                task="title_generation",
                messages=[{"role": "user", "content": "x"}],
            )
        assert _sent_timeout(client) == higher

    def test_route_detected_from_client_base_url_when_config_has_none(self):
        """On an ``auto`` route the base_url is only known once the client is
        built — the floor must read the client, not just the config."""
        client = _client_sync(LOCAL_BASE_URL)
        with_patches = _patches(client, task_timeout=DEFAULT_AUX_TIMEOUT,
                                base_url=None)
        with with_patches[0], with_patches[1], with_patches[2], with_patches[3]:
            call_llm(
                task="title_generation",
                messages=[{"role": "user", "content": "x"}],
            )
        assert _sent_timeout(client) >= LOCAL_TIMEOUT_FLOOR

    def test_config_local_timeout_overrides_the_floor(self):
        """``agent.local_aux_timeout`` is the user-facing knob."""
        client = _client_sync(LOCAL_BASE_URL)
        with_patches = _patches(client, task_timeout=DEFAULT_AUX_TIMEOUT,
                                base_url=LOCAL_BASE_URL)
        with with_patches[0], with_patches[1], with_patches[2], with_patches[3], \
                patch("agent.auxiliary_client._local_aux_timeout_floor",
                      return_value=90.0):
            call_llm(
                task="title_generation",
                messages=[{"role": "user", "content": "x"}],
            )
        assert _sent_timeout(client) == 90.0

    def test_non_positive_config_local_timeout_disables_the_floor(self):
        """Setting ``agent.local_aux_timeout: 0`` restores the plain default."""
        client = _client_sync(LOCAL_BASE_URL)
        with_patches = _patches(client, task_timeout=DEFAULT_AUX_TIMEOUT,
                                base_url=LOCAL_BASE_URL)
        with with_patches[0], with_patches[1], with_patches[2], with_patches[3], \
                patch("agent.auxiliary_client._local_aux_timeout_floor",
                      return_value=0.0):
            call_llm(
                task="title_generation",
                messages=[{"role": "user", "content": "x"}],
            )
        assert _sent_timeout(client) == DEFAULT_AUX_TIMEOUT


class TestLocalTimeoutFloorAsync:
    """Async ``async_call_llm`` mirrors the sync floor."""

    @pytest.mark.asyncio
    async def test_async_local_timeout_is_raised_to_floor(self):
        client = _client_async(LOCAL_BASE_URL)
        with_patches = _patches(client, task_timeout=DEFAULT_AUX_TIMEOUT,
                                base_url=LOCAL_BASE_URL)
        with with_patches[0], with_patches[1], with_patches[2], with_patches[3]:
            await async_call_llm(
                task="title_generation",
                messages=[{"role": "user", "content": "x"}],
            )
        assert _sent_timeout(client) >= LOCAL_TIMEOUT_FLOOR

    @pytest.mark.asyncio
    async def test_async_cloud_route_is_not_floored(self):
        client = _client_async(CLOUD_BASE_URL)
        with_patches = _patches(client, task_timeout=DEFAULT_AUX_TIMEOUT,
                                base_url=CLOUD_BASE_URL)
        with with_patches[0], with_patches[1], with_patches[2], with_patches[3]:
            await async_call_llm(
                task="title_generation",
                messages=[{"role": "user", "content": "x"}],
            )
        assert _sent_timeout(client) == DEFAULT_AUX_TIMEOUT

    @pytest.mark.asyncio
    async def test_async_explicit_per_call_timeout_is_not_floored(self):
        client = _client_async(LOCAL_BASE_URL)
        explicit = 45.0
        with_patches = _patches(client, task_timeout=DEFAULT_AUX_TIMEOUT,
                                base_url=LOCAL_BASE_URL)
        with with_patches[0], with_patches[1], with_patches[2], with_patches[3]:
            await async_call_llm(
                task="title_generation",
                messages=[{"role": "user", "content": "x"}],
                timeout=explicit,
            )
        assert _sent_timeout(client) == explicit


class TestLocalTimeoutFloorConfig:
    """``_local_aux_timeout_floor`` reads ``agent.local_aux_timeout`` from
    config.yaml, not an env var (AGENTS.md: behavioural settings live in
    config.yaml).  Sibling of ``agent.local_stream_stale_timeout``."""

    def _floor_with_config(self, cfg):
        from agent import auxiliary_client
        with patch("hermes_cli.config.load_config_readonly", return_value=cfg):
            return auxiliary_client._local_aux_timeout_floor()

    def test_unset_uses_the_committed_default(self):
        assert self._floor_with_config({}) == LOCAL_TIMEOUT_FLOOR
        assert self._floor_with_config({"agent": {}}) == LOCAL_TIMEOUT_FLOOR

    def test_configured_value_wins(self):
        assert self._floor_with_config(
            {"agent": {"local_aux_timeout": 600}}
        ) == 600.0
        assert self._floor_with_config(
            {"agent": {"local_aux_timeout": "120"}}
        ) == 120.0

    def test_non_positive_disables(self):
        assert self._floor_with_config({"agent": {"local_aux_timeout": 0}}) == 0.0
        assert self._floor_with_config({"agent": {"local_aux_timeout": -5}}) == 0.0

    def test_invalid_value_falls_back_to_default(self):
        assert self._floor_with_config(
            {"agent": {"local_aux_timeout": "abc"}}
        ) == LOCAL_TIMEOUT_FLOOR
