"""Loopback HTTP endpoints, CORS policy, bind restrictions, and the CLI."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from sbol_visual_eval import cli
from sbol_visual_eval.corpus.acquisition import pmc_web
from sbol_visual_eval.corpus.acquisition.pmc_web import (
    bridge as pmc_web_bridge,
)
from sbol_visual_eval.corpus.acquisition.pmc_web import (
    server as pmc_web_server,
)
from sbol_visual_eval.corpus.layout import Layout
from sbol_visual_eval.corpus.util.storage import (
    write_jsonl,
)

from .helpers import (
    FakeClient,
    _article_html,
    _challenge_html,
    _pmc_manifest,
    _record,
    _response,
)


def test_http_bridge_restricts_cors_to_pmc_origin(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    write_jsonl(layout.processed / "papers.jsonl", [])
    bridge = pmc_web.PmcWebBridge(layout, token="local-token", client=FakeClient({}))
    server = ThreadingHTTPServer(("127.0.0.1", 0), pmc_web_server._handler_class(bridge))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        connection = HTTPConnection(host, port)
        connection.request(
            "GET",
            "/status",
            headers={
                "Origin": "https://evil.test",
                "X-SBOL-Bridge-Token": "local-token",
            },
        )
        rejected = connection.getresponse()
        assert rejected.status == 403
        assert rejected.getheader("Access-Control-Allow-Origin") is None
        rejected.read()
        connection.close()

        connection = HTTPConnection(host, port)
        connection.request(
            "GET",
            "/status?token=local-token",
            headers={"Origin": pmc_web.PMC_ORIGIN},
        )
        query_token_rejected = connection.getresponse()
        assert query_token_rejected.status == 403
        query_token_rejected.read()
        connection.close()

        connection = HTTPConnection(host, port)
        connection.request(
            "GET",
            "/status",
            headers={
                "Origin": pmc_web.PMC_ORIGIN,
                "X-SBOL-Bridge-Token": "local-token",
            },
        )
        accepted = connection.getresponse()
        assert accepted.status == 200
        assert accepted.getheader("Access-Control-Allow-Origin") == pmc_web.PMC_ORIGIN
        accepted.read()
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_next_surfaces_discovery_challenge_without_scanning_ahead(
    tmp_path: Path,
) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    first = _record("10.1234/http-challenge", "PMC803")
    second = _record("10.1234/http-next", "PMC804")
    write_jsonl(layout.processed / "papers.jsonl", [first, second])
    first_url = f"{pmc_web.PMC_ORIGIN}/articles/PMC803/"
    second_url = f"{pmc_web.PMC_ORIGIN}/articles/PMC804/"
    client = FakeClient(
        {
            first_url: _response(first_url, _challenge_html()),
            second_url: _response(second_url, _article_html("PMC804")),
        }
    )
    bridge = pmc_web.PmcWebBridge(layout, token="local-token", client=client)
    server = ThreadingHTTPServer(("127.0.0.1", 0), pmc_web_server._handler_class(bridge))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        connection = HTTPConnection(host, port)
        connection.request(
            "GET",
            "/next?limit=1",
            headers={
                "Origin": pmc_web.PMC_ORIGIN,
                "X-SBOL-Bridge-Token": "local-token",
            },
        )
        response = connection.getresponse()
        body = json.loads(response.read())
        assert response.status == 503
        assert int(response.getheader("Retry-After")) >= 60
        assert body["error"] == "pmc_discovery_challenge"
        assert body["retryable"] is True
        assert body["requires_manual_restart"] is True
        connection.close()
        assert client.requests == [first_url]
        assert bridge.results == {}
        assert bridge.attempted == set()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_next_floors_short_pmc_retry_after_to_challenge_cooldown(
    tmp_path: Path,
) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record("10.1234/http-short-retry", "PMC807")
    write_jsonl(layout.processed / "papers.jsonl", [record])
    article_url = f"{pmc_web.PMC_ORIGIN}/articles/PMC807/"
    client = FakeClient(
        {
            article_url: _response(
                article_url,
                b"rate limited",
                status=429,
                headers={"Retry-After": "1"},
            )
        }
    )
    bridge = pmc_web.PmcWebBridge(layout, token="local-token", client=client)
    server = ThreadingHTTPServer(("127.0.0.1", 0), pmc_web_server._handler_class(bridge))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        connection = HTTPConnection(host, port)
        connection.request(
            "GET",
            "/next?limit=1",
            headers={
                "Origin": pmc_web.PMC_ORIGIN,
                "X-SBOL-Bridge-Token": "local-token",
            },
        )
        response = connection.getresponse()
        body = json.loads(response.read())
        retry_after = int(response.getheader("Retry-After"))
        assert response.status == 503
        assert retry_after >= pmc_web.PMC_CHALLENGE_RETRY_SECONDS
        assert body["retry_after_seconds"] >= pmc_web.PMC_CHALLENGE_RETRY_SECONDS
        assert bridge.status()["challenge_retry_after_seconds"] > 0
        connection.close()
        assert client.requests == [article_url]
        assert bridge.results == {}
        assert bridge.attempted == set()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_transient_release_is_one_shot_and_does_not_record_failure(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record("10.1234/http-release", "PMC805")
    write_jsonl(layout.processed / "papers.jsonl", [record])
    _pmc_manifest(layout, record)
    monkeypatch.setattr(pmc_web_bridge, "HTML_REQUEST_INTERVAL_SECONDS", 0)
    article_url = f"{pmc_web.PMC_ORIGIN}/articles/PMC805/"
    bridge = pmc_web.PmcWebBridge(
        layout,
        token="local-token",
        client=FakeClient({article_url: _response(article_url, _article_html("PMC805"))}),
    )
    [item] = bridge.next_items()
    server = ThreadingHTTPServer(("127.0.0.1", 0), pmc_web_server._handler_class(bridge))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    payload = json.dumps(
        {
            "source_url": item["url"],
            "resolved_url": item["url"],
            "article_url": item["article_url"],
            "error": "PMC returned a reCAPTCHA HTML page",
        }
    )
    headers = {
        "Origin": pmc_web.PMC_ORIGIN,
        "X-SBOL-Bridge-Token": "local-token",
        "X-SBOL-Queue-Nonce": item["queue_nonce"],
        "Content-Type": "application/json",
    }
    try:
        connection = HTTPConnection(host, port)
        connection.request(
            "POST",
            f"/transient?doi={record['doi']}",
            body=payload,
            headers=headers,
        )
        released = connection.getresponse()
        released_body = json.loads(released.read())
        assert released.status == 200
        assert released_body["status"] == "transient_challenge"
        connection.close()
        assert bridge.results == {}
        assert bridge.attempted == set()
        assert bridge.queued_items == {}

        connection = HTTPConnection(host, port)
        connection.request(
            "POST",
            f"/transient?doi={record['doi']}",
            body=payload,
            headers=headers,
        )
        replay = connection.getresponse()
        assert replay.status == 409
        replay.read()
        connection.close()
        attempts = [
            json.loads(line)
            for line in (layout.reports / "pmc_web_attempts.jsonl").read_text().splitlines()
        ]
        assert [attempt["status"] for attempt in attempts] == ["transient_challenge"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_user_release_is_one_shot_and_immediately_requeues(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record("10.1234/http-user-release", "PMC808")
    write_jsonl(layout.processed / "papers.jsonl", [record])
    _pmc_manifest(layout, record)
    monkeypatch.setattr(pmc_web_bridge, "HTML_REQUEST_INTERVAL_SECONDS", 0)
    article_url = f"{pmc_web.PMC_ORIGIN}/articles/PMC808/"
    bridge = pmc_web.PmcWebBridge(
        layout,
        token="local-token",
        client=FakeClient({article_url: _response(article_url, _article_html("PMC808"))}),
    )
    [item] = bridge.next_items()
    server = ThreadingHTTPServer(("127.0.0.1", 0), pmc_web_server._handler_class(bridge))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    payload = json.dumps(
        {
            "source_url": item["url"],
            "resolved_url": None,
            "article_url": item["article_url"],
            "reason": "browser runner stopped by user",
        }
    )
    headers = {
        "Origin": pmc_web.PMC_ORIGIN,
        "X-SBOL-Bridge-Token": "local-token",
        "X-SBOL-Queue-Nonce": item["queue_nonce"],
        "Content-Type": "application/json",
    }
    try:
        connection = HTTPConnection(host, port)
        connection.request(
            "POST",
            f"/release?doi={record['doi']}",
            body=payload,
            headers=headers,
        )
        released = connection.getresponse()
        released_body = json.loads(released.read())
        assert released.status == 200
        assert released_body["status"] == "released_by_user"
        connection.close()
        assert bridge.results == {}
        assert bridge.attempted == set()
        assert bridge.queued_items == {}
        assert bridge.status()["challenge_retry_after_seconds"] == 0

        connection = HTTPConnection(host, port)
        connection.request(
            "POST",
            f"/release?doi={record['doi']}",
            body=payload,
            headers=headers,
        )
        replay = connection.getresponse()
        assert replay.status == 409
        replay.read()
        connection.close()

        [retried] = bridge.next_items()
        assert retried["doi"] == record["doi"]
        assert retried["queue_nonce"] != item["queue_nonce"]
        attempts = [
            json.loads(line)
            for line in (layout.reports / "pmc_web_attempts.jsonl").read_text().splitlines()
        ]
        assert [attempt["status"] for attempt in attempts] == ["released_by_user"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_server_rejects_non_loopback_bind(tmp_path: Path) -> None:
    layout = Layout(tmp_path)

    with pytest.raises(ValueError, match="only to 127.0.0.1"):
        pmc_web.serve_pmc_web_bridge(layout, host="0.0.0.0")


def test_cli_exposes_pmc_web_bridge_with_safe_defaults(tmp_path: Path, monkeypatch: Any) -> None:
    args = cli._argument_parser().parse_args(["serve-pmc-web-bridge"])

    assert args.command == "serve-pmc-web-bridge"
    assert args.host == "127.0.0.1"
    assert args.port == 8766

    calls: list[tuple[Layout, str, int]] = []
    monkeypatch.setattr(
        pmc_web,
        "serve_pmc_web_bridge",
        lambda layout, *, host, port: calls.append((layout, host, port)),
    )
    cli.main(
        [
            "--root",
            str(tmp_path),
            "serve-pmc-web-bridge",
            "--host",
            "127.0.0.1",
            "--port",
            "9876",
        ]
    )
    assert calls == [(Layout(tmp_path), "127.0.0.1", 9876)]
