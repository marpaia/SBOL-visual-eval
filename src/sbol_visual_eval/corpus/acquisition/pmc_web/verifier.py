"""Isolated, resource-limited verification of browser-supplied PDF bytes."""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
from typing import Any

from ...provenance import pdf

PDF_VERIFY_TIMEOUT_SECONDS = 45
PDF_VERIFY_CPU_SECONDS = 30
PDF_VERIFY_MEMORY_BYTES = 1024 * 1024 * 1024
MAX_VERIFIER_RESULT_BYTES = 64 * 1024


def _apply_pdf_worker_limits() -> None:
    """Best-effort Unix resource limits for parsing browser-supplied PDFs."""

    try:
        import resource
    except ImportError:  # pragma: no cover - Windows fallback still has wall timeout
        return

    for resource_name, requested_limit in (
        ("RLIMIT_CPU", PDF_VERIFY_CPU_SECONDS),
        ("RLIMIT_AS", PDF_VERIFY_MEMORY_BYTES),
        ("RLIMIT_DATA", PDF_VERIFY_MEMORY_BYTES),
    ):
        resource_id = getattr(resource, resource_name, None)
        if resource_id is None:
            continue
        try:
            _, hard_limit = resource.getrlimit(resource_id)
            soft_limit = (
                requested_limit
                if hard_limit == resource.RLIM_INFINITY
                else min(requested_limit, hard_limit)
            )
            resource.setrlimit(resource_id, (soft_limit, soft_limit))
        except (OSError, ValueError):
            continue


def _pdf_verification_worker(
    connection: Any,
    path: str,
    record: dict[str, Any],
) -> None:
    _apply_pdf_worker_limits()
    try:
        result = pdf.verify_pdf(Path(path), record)
    except BaseException as error:  # noqa: BLE001 - isolate parser failures in child process
        message = {
            "ok": False,
            "error_type": type(error).__name__,
            "error": str(error)[:500],
        }
    else:
        message = {"ok": True, "result": result}
    try:
        encoded_message = json.dumps(message, ensure_ascii=True).encode()
        if len(encoded_message) > MAX_VERIFIER_RESULT_BYTES:
            encoded_message = json.dumps(
                {"ok": False, "error_type": "ResultTooLarge", "error": "result exceeded limit"}
            ).encode()
        connection.send_bytes(encoded_message)
    finally:
        connection.close()


def _verify_uploaded_pdf(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    """Parse an untrusted browser upload in a bounded child process."""

    context = multiprocessing.get_context("spawn")
    receiving_connection, sending_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_pdf_verification_worker,
        args=(sending_connection, str(path), record),
        daemon=True,
    )
    process.start()
    sending_connection.close()
    try:
        if not receiving_connection.poll(PDF_VERIFY_TIMEOUT_SECONDS):
            process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            raise ValueError("PDF verification exceeded the isolated parser time limit")
        try:
            encoded_message = receiving_connection.recv_bytes(MAX_VERIFIER_RESULT_BYTES)
        except (EOFError, OSError) as error:
            raise ValueError("isolated PDF verifier exited without a result") from error
    finally:
        receiving_connection.close()
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    try:
        message = json.loads(encoded_message)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("isolated PDF verifier returned invalid JSON") from error
    if not isinstance(message, dict) or message.get("ok") is not True:
        error_type = (
            str(message.get("error_type") or "PDFError")
            if isinstance(message, dict)
            else "PDFError"
        )
        error_text = (
            str(message.get("error") or "verification failed")
            if isinstance(message, dict)
            else "verification failed"
        )
        raise ValueError(f"isolated PDF verification failed ({error_type}): {error_text}")
    result = message.get("result")
    if not isinstance(result, dict):
        raise TypeError("isolated PDF verifier returned an invalid result")
    expected_keys = {"artifact_type", "page_count", "identity_check", "title_similarity"}
    if set(result) != expected_keys:
        raise ValueError("isolated PDF verifier returned unexpected fields")
    page_count = result.get("page_count")
    title_similarity = result.get("title_similarity")
    if (
        result.get("artifact_type") != "article_pdf"
        or not isinstance(page_count, int)
        or isinstance(page_count, bool)
        or page_count < 1
        or result.get("identity_check") not in {"doi", "title"}
        or not isinstance(title_similarity, (int, float))
        or isinstance(title_similarity, bool)
        or not 0 <= title_similarity <= 1
    ):
        raise ValueError("isolated PDF verifier returned invalid identity metadata")
    return result
