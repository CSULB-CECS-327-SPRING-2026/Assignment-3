"""
network.py — Simulated FIFO-Reliable Network
CECS 327 Assignment — Thanh Tran (033793017)

Design:
  - One background thread per (sender, receiver) pair guarantees
    FIFO delivery on each channel (messages from the same sender
    to the same receiver are delivered in send order).
  - Each message experiences an independent random delay drawn
    from [delay_min, delay_max] seconds, simulating a WAN.
  - Because different (sender, receiver) channels are independent,
    messages from *different* senders may be reordered at the receiver —
    exactly the partial-order scenario the algorithm must handle.
"""

import queue
import random
import threading
import time
from collections import defaultdict
from typing import Any, Dict


class SimulatedNetwork:
    """
    FIFO-per-sender reliable network simulator.

    Usage:
        net = SimulatedNetwork(delay_min=0.002, delay_max=0.05, seed=42)
        for r in replicas:
            net.register(r)
        net.start()
        net.send(src_id, dst_id, message_dict)
        # ... wait ...
        net.stop()
    """

    def __init__(self, delay_min: float = 0.002,
                 delay_max: float = 0.05,
                 seed: int = 42):
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.rng = random.Random(seed)

        self.replicas: Dict[int, Any] = {}          # replica_id → Replica

        # Per-(sender, receiver) FIFO queue
        # Each queue holds (delay_seconds, message) items
        self._channels: Dict[tuple, queue.Queue] = defaultdict(queue.Queue)
        self._workers: Dict[tuple, threading.Thread] = {}
        self._stopped = threading.Event()

        # Counters for diagnostics
        self.sent_count = 0
        self._count_lock = threading.Lock()

    # -----------------------------------------------------------------------
    # Setup
    # -----------------------------------------------------------------------

    def register(self, replica) -> None:
        """Register a replica before calling start()."""
        self.replicas[replica.id] = replica

    def start(self) -> None:
        """Spawn one FIFO-delivery worker thread per (sender, receiver) pair."""
        n = len(self.replicas)
        for s in range(n):
            for r in range(n):
                key = (s, r)
                t = threading.Thread(
                    target=self._channel_worker,
                    args=(key,),
                    daemon=True,
                    name=f"net-{s}->{r}",
                )
                self._workers[key] = t
                t.start()

    def stop(self) -> None:
        """Signal all worker threads to stop (they are daemons, so not strictly required)."""
        self._stopped.set()

    # -----------------------------------------------------------------------
    # Sending
    # -----------------------------------------------------------------------

    def send(self, sender_id: int, receiver_id: int, message: Dict) -> None:
        """
        Enqueue a message for delivery on the (sender_id, receiver_id) channel.
        The actual delivery happens after a random delay on the worker thread,
        preserving FIFO order within each channel.
        """
        delay = self.rng.uniform(self.delay_min, self.delay_max)
        self._channels[(sender_id, receiver_id)].put((delay, message))
        with self._count_lock:
            self.sent_count += 1

    # -----------------------------------------------------------------------
    # Worker
    # -----------------------------------------------------------------------

    def _channel_worker(self, key: tuple) -> None:
        """
        Deliver messages on one (sender, receiver) channel in FIFO order.
        Each message sleeps for its pre-drawn delay before being handed to
        the destination replica's receive() method.
        """
        _, receiver_id = key
        q = self._channels[key]
        while not self._stopped.is_set():
            try:
                delay, message = q.get(timeout=0.05)
            except queue.Empty:
                continue
            time.sleep(delay)
            dest = self.replicas.get(receiver_id)
            if dest is not None:
                dest.receive(message)
