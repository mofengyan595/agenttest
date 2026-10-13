"""Contract parsing must leave the event loop responsive and reject excess work."""

import asyncio
import threading
from pathlib import Path

import httpx
import pytest

from agenttest import main


@pytest.mark.asyncio
async def test_contract_import_does_not_block_health(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = main.load_openapi

    def parse(*args, **kwargs):
        entered.set()
        assert release.wait(5), "Parser was not released"
        return original(*args, **kwargs)

    monkeypatch.setattr(main, "load_openapi", parse)
    transport = httpx.ASGITransport(app=main.create_app(tmp_path), client=("127.0.0.1", 1))
    raw = Path("tests/fixtures/contract.yaml").read_bytes()
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        importing = asyncio.create_task(client.post(
            "/api/spec/summary", content=raw, headers={"content-type": "application/yaml"}))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            assert not importing.done()
            response = await asyncio.wait_for(client.get("/health"), 2)
            assert response.status_code == 200
            assert not release.is_set() and not importing.done()
        finally:
            release.set()
            results = await asyncio.gather(importing, return_exceptions=True)
        assert isinstance(results[0], httpx.Response) and results[0].status_code == 200


@pytest.mark.asyncio
async def test_cancelled_import_keeps_capacity_until_parser_finishes(tmp_path, monkeypatch):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    lock = threading.Lock()
    counts = {"started": 0, "finished": 0}
    original = main.load_openapi

    def parse(*args, **kwargs):
        with lock:
            counts["started"] += 1
            if counts["started"] == main.MAX_CONCURRENT_IMPORTS:
                entered.set()
        try:
            assert release.wait(5), "Parser was not released"
            return original(*args, **kwargs)
        finally:
            with lock:
                counts["finished"] += 1
                if counts["finished"] == main.MAX_CONCURRENT_IMPORTS:
                    finished.set()

    monkeypatch.setattr(main, "load_openapi", parse)
    transport = httpx.ASGITransport(app=main.create_app(tmp_path), client=("127.0.0.1", 1))
    raw = Path("tests/fixtures/contract.yaml").read_bytes()
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        importing = [asyncio.create_task(client.post(
            "/api/spec/summary", content=raw, headers={"content-type": "application/yaml"}))
            for _ in range(main.MAX_CONCURRENT_IMPORTS)]
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            importing[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await importing[0]
            consumed = []

            async def excess_body():
                consumed.append(True)
                yield raw

            rejected = await asyncio.wait_for(client.post(
                "/api/spec/summary", content=excess_body(),
                headers={"content-type": "application/yaml"}), 2)
            assert rejected.status_code == 429 and rejected.headers["retry-after"] == "1"
            assert not consumed and counts["started"] == main.MAX_CONCURRENT_IMPORTS
            assert (await client.get("/health")).status_code == 200
        finally:
            release.set()
            await asyncio.gather(*importing, return_exceptions=True)
            assert await asyncio.to_thread(finished.wait, 5)
        accepted = await client.post("/api/spec/summary", content=raw,
                                     headers={"content-type": "application/yaml"})
        assert accepted.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["oversize", "invalid", "cancel_read"])
async def test_rejected_or_cancelled_body_does_not_leak_capacity(tmp_path, failure):
    transport = httpx.ASGITransport(app=main.create_app(tmp_path), client=("127.0.0.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        for _ in range(main.MAX_CONCURRENT_IMPORTS + 1):
            if failure == "cancel_read":
                reading = asyncio.Event()

                async def interrupted_body():
                    reading.set()
                    await asyncio.Event().wait()
                    yield b"unused"

                importing = asyncio.create_task(client.post(
                    "/api/spec/summary", content=interrupted_body(),
                    headers={"content-type": "application/yaml"}))
                try:
                    await asyncio.wait_for(reading.wait(), 5)
                finally:
                    importing.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await importing
            else:
                raw = b"x" * (main.MAX_DOCUMENT_BYTES + 1) if failure == "oversize" else b"bad"
                response = await client.post("/api/spec/summary", content=raw,
                                             headers={"content-type": "application/yaml"})
                assert response.status_code == (413 if failure == "oversize" else 422)
        response = await client.post(
            "/api/spec/summary", content=Path("tests/fixtures/contract.yaml").read_bytes(),
            headers={"content-type": "application/yaml"})
        assert response.status_code == 200
