"""Behavioral contracts of the browser-side runner script."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sbol_visual_eval.corpus.acquisition import pmc_web


def test_browser_runner_has_fixed_one_request_per_second_floor() -> None:
    script = (Path(__file__).resolve().parents[4] / "scripts" / "pmc_browser_runner.js").read_text()

    match = re.search(r"minimumRequestIntervalMs\s*=\s*(\d+)", script)
    assert match is not None
    assert int(match.group(1)) >= 1000
    assert pmc_web.PMC_CHALLENGE_RETRY_SECONDS >= 60
    assert "window.prompt" in script
    assert "REPLACE_WITH_BRIDGE_TOKEN" not in script
    assert script.count('credentials: "same-origin"') == 1
    assert script.count('credentials: "omit"') == 5
    assert 'redirect: "error"' in script
    assert "AbortController" in script
    assert "arrayBuffer" not in script
    assert "maximumPdfBytes" in script
    assert "X-SBOL-Bridge-Token" in script
    assert "X-SBOL-Queue-Nonce" in script
    assert "token=" not in script
    assert "/transient?doi=" in script
    assert "/release?doi=" in script
    assert "pmc_discovery_challenge" in script
    assert "pmc_pdf_challenge" in script
    assert "captcha" in script.casefold()


def test_browser_runner_stops_on_first_html_pdf_challenge_and_releases_queue() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the browser-runner state-machine test")
    script_path = Path(__file__).resolve().parents[4] / "scripts" / "pmc_browser_runner.js"
    harness = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const item = {
  doi: "10.1234/browser-harness",
  pmcid: "PMC900",
  article_url: "https://pmc.ncbi.nlm.nih.gov/articles/PMC900/",
  url: "https://pmc.ncbi.nlm.nih.gov/articles/PMC900/pdf/nihms-900.pdf",
  queue_nonce: "one-shot-nonce",
};
const calls = [];
global.window = {
  location: { origin: "https://pmc.ncbi.nlm.nih.gov" },
  prompt: () => "ephemeral-token",
};
global.fetch = async (rawUrl, options = {}) => {
  const url = String(rawUrl);
  calls.push({ url, options });
  if (url.includes("/next?limit=1")) {
    assert.equal(options.credentials, "omit");
    return new Response(JSON.stringify({ items: [item] }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }
  if (url === item.url) {
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.redirect, "error");
    const challengePrefix =
      "<html><head><title>Checking your browser - reCAPTCHA</title></head>" +
      "<body><div class='g-recaptcha'></div></body></html>";
    const response = new Response(
      challengePrefix.padEnd(21 * 1024, " "),
      { status: 200, headers: { "content-type": "text/html" } },
    );
    Object.defineProperty(response, "url", { value: item.url });
    return response;
  }
  if (url.includes("/transient?doi=")) {
    assert.equal(options.credentials, "omit");
    assert.equal(options.headers["X-SBOL-Queue-Nonce"], item.queue_nonce);
    const body = JSON.parse(options.body);
    assert.equal(body.source_url, item.url);
    return new Response(
      JSON.stringify({ status: "transient_challenge", retry_after_seconds: 900 }),
      { status: 200, headers: { "content-type": "application/json" } },
    );
  }
  throw new Error(`unexpected fetch ${url}`);
};
let source = fs.readFileSync(process.argv[1], "utf8");
source = source.replace(
  "const minimumRequestIntervalMs = 1100;",
  "const minimumRequestIntervalMs = 0;",
);
const state = vm.runInThisContext(source, { filename: process.argv[1] });
(async () => {
  const deadline = Date.now() + 5000;
  while (!state.finishedAt && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.ok(state.finishedAt, "runner did not finish");
  assert.equal(state.running, false);
  assert.equal(state.stopReason, "pmc_pdf_challenge");
  assert.equal(state.transientStops, 1);
  assert.equal(state.failed, 0);
  assert.equal(state.completed, 1);
  assert.equal(state.retryAfterSeconds, 900);
  assert.equal(calls.length, 3);
  assert.equal(calls.filter((call) => call.url.includes("/next?")).length, 1);
  assert.equal(calls.filter((call) => call.url === item.url).length, 1);
  assert.equal(calls.filter((call) => call.url.includes("/transient?")).length, 1);
  assert.equal(calls.filter((call) => call.url.includes("/upload?")).length, 0);
  assert.equal(calls.filter((call) => call.url.includes("/failure?")).length, 0);
  assert.equal(calls.filter((call) => call.url.includes("/release?")).length, 0);
  console.log(JSON.stringify({ stopReason: state.stopReason, calls: calls.length }));
})().catch((error) => {
  console.error(error.stack || String(error));
  process.exitCode = 1;
});
"""

    completed = subprocess.run(
        [node, "-e", harness, str(script_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"stopReason": "pmc_pdf_challenge", "calls": 3}


def test_browser_runner_user_stop_aborts_pdf_and_releases_without_failure() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the browser-runner state-machine test")
    script_path = Path(__file__).resolve().parents[4] / "scripts" / "pmc_browser_runner.js"
    harness = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const item = {
  doi: "10.1234/browser-user-stop",
  pmcid: "PMC901",
  article_url: "https://pmc.ncbi.nlm.nih.gov/articles/PMC901/",
  url: "https://pmc.ncbi.nlm.nih.gov/articles/PMC901/pdf/nihms-901.pdf",
  queue_nonce: "user-stop-nonce",
};
const calls = [];
global.window = {
  location: { origin: "https://pmc.ncbi.nlm.nih.gov" },
  prompt: () => "ephemeral-token",
};
global.fetch = async (rawUrl, options = {}) => {
  const url = String(rawUrl);
  calls.push({ url, options });
  if (url.includes("/next?limit=1")) {
    assert.equal(options.credentials, "omit");
    return new Response(JSON.stringify({ items: [item] }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }
  if (url === item.url) {
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.redirect, "error");
    assert.ok(options.signal);
    return new Promise((resolve, reject) => {
      options.signal.addEventListener(
        "abort",
        () => {
          const error = new Error("PDF request aborted by user");
          error.name = "AbortError";
          reject(error);
        },
        { once: true },
      );
      setTimeout(() => window.sbolPmcRunner.stop(), 0);
    });
  }
  if (url.includes("/release?doi=")) {
    assert.equal(options.credentials, "omit");
    assert.equal(options.headers["X-SBOL-Queue-Nonce"], item.queue_nonce);
    const body = JSON.parse(options.body);
    assert.equal(body.source_url, item.url);
    assert.equal(body.resolved_url, null);
    assert.equal(body.article_url, item.article_url);
    assert.match(body.reason, /browser-user-stop/);
    return new Response(JSON.stringify({ status: "released_by_user" }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }
  throw new Error(`unexpected fetch ${url}`);
};
let source = fs.readFileSync(process.argv[1], "utf8");
source = source.replace(
  "const minimumRequestIntervalMs = 1100;",
  "const minimumRequestIntervalMs = 0;",
);
const state = vm.runInThisContext(source, { filename: process.argv[1] });
(async () => {
  const deadline = Date.now() + 5000;
  while (!state.finishedAt && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.ok(state.finishedAt, "runner did not finish");
  assert.equal(state.running, false);
  assert.equal(state.stopReason, "user_stopped");
  assert.equal(state.released, 1);
  assert.equal(state.failed, 0);
  assert.equal(state.transientStops, 0);
  assert.equal(state.completed, 1);
  assert.equal(state.requiresBridgeRestart, false);
  assert.equal(calls.length, 3);
  assert.equal(calls.filter((call) => call.url.includes("/next?")).length, 1);
  assert.equal(calls.filter((call) => call.url === item.url).length, 1);
  assert.equal(calls.filter((call) => call.url.includes("/release?")).length, 1);
  assert.equal(calls.filter((call) => call.url.includes("/transient?")).length, 0);
  assert.equal(calls.filter((call) => call.url.includes("/upload?")).length, 0);
  assert.equal(calls.filter((call) => call.url.includes("/failure?")).length, 0);
  console.log(JSON.stringify({ stopReason: state.stopReason, calls: calls.length }));
})().catch((error) => {
  console.error(error.stack || String(error));
  process.exitCode = 1;
});
"""

    completed = subprocess.run(
        [node, "-e", harness, str(script_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"stopReason": "user_stopped", "calls": 3}
