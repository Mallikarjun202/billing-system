"""Live updates: an in-process publish/subscribe hub, streamed to browsers as
Server-Sent Events (GET /api/events).

Events: ``purchase`` (a bill was made), ``email`` (an invoice email changed
status), ``products`` (catalogue or stock changed), ``drawer`` (cash drawer
changed). Works for a single server process; running several workers would
need a shared broker such as Redis.
"""

import asyncio
import json
import signal
import threading
from collections.abc import AsyncIterator, Awaitable, Callable

PING_SECONDS = 15
_CLOSE = object()


class Broker:
    def __init__(self) -> None:
        self._subscribers: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
        self._lock = threading.Lock()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        with self._lock:
            self._subscribers[queue] = asyncio.get_running_loop()
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.pop(queue, None)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, event: str, data: dict | None = None) -> None:
        """Send an event to every open stream. Safe to call from any thread."""
        self._send_all(f"event: {event}\ndata: {json.dumps(data or {})}\n\n")

    def close_all(self) -> None:
        """End every open stream (browsers reconnect by themselves)."""
        self._send_all(_CLOSE)

    def _send_all(self, item) -> None:
        with self._lock:
            targets = list(self._subscribers.items())
        for queue, loop in targets:
            try:
                loop.call_soon_threadsafe(_offer, queue, item)
            except RuntimeError:  # that event loop has closed
                self.unsubscribe(queue)


def _offer(queue: asyncio.Queue, item) -> None:
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        pass  # a stalled client; it re-syncs when it reconnects


broker = Broker()
publish = broker.publish


async def stream(is_disconnected: Callable[[], Awaitable[bool]]) -> AsyncIterator[str]:
    """SSE body: events as they happen, with a comment ping to keep proxies happy."""
    queue = broker.subscribe()
    try:
        yield "retry: 3000\n\n"
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), PING_SECONDS)
            except TimeoutError:
                if await is_disconnected():
                    break
                yield ": ping\n\n"
                continue
            if item is _CLOSE:
                break
            yield item
    finally:
        broker.unsubscribe(queue)


def install_shutdown_hook() -> None:
    """Close open streams as soon as the server is asked to stop (Ctrl+C).
    Otherwise uvicorn waits for every open browser tab to disconnect first.

    Wraps whatever handler the server installed, so call it from startup."""
    if threading.current_thread() is not threading.main_thread():
        return  # e.g. under the test client; signals can only be set from the main thread
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        previous = signal.getsignal(sig) if sig is not None else None
        if not callable(previous) or getattr(previous, "_closes_streams", False):
            continue

        def handler(signum, frame, previous=previous):
            broker.close_all()
            previous(signum, frame)

        handler._closes_streams = True
        signal.signal(sig, handler)
