"""Single-owner worker, injectable transport, explicit global/capability gates.

No production sender is configured by this module. Default literal reminders do
not invoke a model; an optional generator is subjected to the same late checks.
"""

import asyncio
from datetime import datetime, timezone

from filelock import FileLock, Timeout

from .delivery import DeliveryStore


class DefinitelyNotAccepted(Exception):
    """Use only with transport evidence proving there was no accepted submission."""


class DeliveryWorker:
    def __init__(
        self,
        core: DeliveryStore,
        *,
        enabled,
        capability,
        sender,
        generator=None,
        clock=None,
    ):
        self.core = core
        self.enabled, self.capability, self.sender = enabled, capability, sender
        self.generator = generator
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def tick(self):
        if not self.enabled():
            self.core.stop_all(self.clock())
            return
        self.core.recover(self.clock())
        for execution in self.core.begin_due(self.clock()):
            if not self.enabled():
                self.core.stop_all(self.clock())
                return
            try:
                content = (
                    await asyncio.wait_for(self.generator(execution), 35)
                    if self.generator
                    else execution["content"]
                )
                if not self.enabled():
                    self.core.stop_all(self.clock())
                    return
                self.core.complete_generation(execution["id"], content, self.clock())
            except Exception:
                self.core.fail_generation(execution["id"])
        for row in self.core.queued():
            if not self.enabled():
                self.core.stop_all(self.clock())
                return
            claimed = self.core.claim_send(
                row["id"], self.clock(), capability=bool(self.capability(row))
            )
            if not claimed:
                continue
            # Last possible local check. A request already in the network may
            # still be accepted after a concurrent revocation; record it honestly.
            if not self.enabled():
                self.core.finish_send(row["id"], "rejected", self.clock())
                self.core.stop_all(self.clock())
                return
            if not self.core._valid(claimed) or not self.capability(claimed):
                self.core.finish_send(row["id"], "rejected", self.clock())
                continue
            try:
                result = await asyncio.wait_for(
                    self.sender(
                        claimed["target"],
                        claimed["content"],
                        claimed["idempotency_key"],
                    ),
                    30,
                )
                outcome = (
                    result
                    if result in ("accepted_by_platform", "rejected")
                    else "unknown"
                )
            except DefinitelyNotAccepted:
                outcome = "rejected"
            except Exception:
                # A timeout or general SDK error does not prove non-acceptance.
                outcome = "unknown"
            self.core.finish_send(row["id"], outcome, self.clock())

    async def run(self, stop: asyncio.Event, *, poll_seconds=5) -> bool:
        """Only one long-running loop owns the local lock; DB claims add protection."""
        lock = FileLock(str(self.core.memory.path) + ".scheduler.lock")
        try:
            lock.acquire(timeout=0)
        except Timeout:
            return False
        try:
            while not stop.is_set():
                await self.tick()
                try:
                    await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
                except asyncio.TimeoutError:
                    pass
        finally:
            lock.release()
        return True
