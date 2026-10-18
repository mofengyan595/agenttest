"""Arm a real asyncio deadline at the intended boundary, independent of runner speed."""

import asyncio


def controlled_workflow_timeout(monkeypatch, seconds):
    original = asyncio.timeout
    timers = []

    def deferred(delay):
        if delay == seconds:
            timer = original(None)
            timers.append(timer)
            return timer
        return original(delay)

    monkeypatch.setattr(asyncio, "timeout", deferred)

    def expire():
        assert len(timers) == 1
        timers[0].reschedule(asyncio.get_running_loop().time())

    return expire
