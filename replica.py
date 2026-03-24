"""
replica.py — Total-Order Multicast Replica
CECS 327 Assignment

Each Replica maintains:
  - A Lamport logical clock
  - A holdback queue ordered by (ts, sender_id)
  - max_seen[k]: largest timestamp seen in any message FROM replica k
  - A replicated key-value store (the "application")

Message types exchanged:
  TOBCAST(update_id, op, ts, sender_id)   — multicast a new update
  ACK(update_id, orig_ts, orig_sender,     — acknowledge: tells all replicas that
      ack_ts, ack_sender)                    ack_sender's clock is now ack_ts > orig_ts
"""

import heapq
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Message dataclass — comparable by (ts, sender_id) for min-heap ordering
# ---------------------------------------------------------------------------

@dataclass
class TOBMessage:
    ts: int
    sender_id: int
    update_id: str
    op: Tuple

    # Tie-break: lower sender_id wins (deterministic total order)
    def __lt__(self, other):
        return (self.ts, self.sender_id) < (other.ts, other.sender_id)

    def __eq__(self, other):
        return (self.ts, self.sender_id) == (other.ts, other.sender_id)

    def __le__(self, other):
        return self < other or self == other

    def __repr__(self):
        return f"TOBMsg({self.update_id}, ts=({self.ts},{self.sender_id}), op={self.op})"


# ---------------------------------------------------------------------------
# Replica
# ---------------------------------------------------------------------------

class Replica:
    """
    One replica in the replicated key-value store.

    Thread-safety: a single lock (self.lock) serialises all state mutations.
    The network layer calls receive() from separate delivery threads.
    """

    def __init__(self, replica_id: int, num_replicas: int, network):
        self.id = replica_id
        self.N = num_replicas
        self.network = network          # SimulatedNetwork — used for sending

        # --- Lamport logical clock ---
        self.clock: int = 0
        self.lock = threading.Lock()

        # --- Holdback queue (min-heap by (ts, sender_id)) ---
        self.holdback: List[TOBMessage] = []

        # --- max_seen[k]: max timestamp of any message *from* replica k we've seen ---
        # Initialised to 0; any real message has ts >= 1.
        self.max_seen: Dict[int, int] = {i: 0 for i in range(num_replicas)}

        # --- Application state ---
        self.store: Dict[str, Any] = {}

        # --- Audit trail (for correctness verification) ---
        self.delivered_log: List[str] = []   # update_ids in delivery order
        self.log: List[str] = []             # human-readable trace

    # -----------------------------------------------------------------------
    # Lamport clock helpers
    # -----------------------------------------------------------------------

    def _tick(self, received_ts: Optional[int] = None) -> int:
        """Increment clock, optionally syncing with a received timestamp first."""
        if received_ts is not None:
            self.clock = max(self.clock, received_ts) + 1
        else:
            self.clock += 1
        return self.clock

    # -----------------------------------------------------------------------
    # Public: called by the client / test harness
    # -----------------------------------------------------------------------

    def client_update(self, update_id: str, op: Tuple):
        """
        A client sends an update to this replica.
        Steps:
          1. Stamp with incremented Lamport clock → ts
          2. Insert into own holdback queue
          3. TOBCAST to all other replicas
          4. Issue self-ACK (clock ts+1) to let the delivery condition advance for self
          5. Send ACK to all other replicas
          6. Try to deliver anything at the head of the holdback queue
        """
        with self.lock:
            ts = self._tick()                       # Step 1
            msg = TOBMessage(ts=ts, sender_id=self.id,
                             update_id=update_id, op=op)
            heapq.heappush(self.holdback, msg)      # Step 2
            self.log.append(
                f"[R{self.id}] TOBCAST  {update_id}  op={op}  ts=({ts},{self.id})"
            )

            # Step 3 — broadcast TOBCAST
            for rid in range(self.N):
                if rid != self.id:
                    self.network.send(self.id, rid, {
                        'type': 'TOBCAST',
                        'update_id': update_id,
                        'op': op,
                        'ts': ts,
                        'sender_id': self.id,
                    })

            # Step 4 — self-ACK: our clock advances past ts so max_seen[self] > ts
            ack_ts = self._tick()                   # = ts + 1
            self.max_seen[self.id] = max(self.max_seen[self.id], ack_ts)

            # Step 5 — broadcast ACK to others
            for rid in range(self.N):
                if rid != self.id:
                    self.network.send(self.id, rid, {
                        'type': 'ACK',
                        'update_id': update_id,
                        'orig_ts': ts,
                        'orig_sender': self.id,
                        'ack_ts': ack_ts,
                        'ack_sender': self.id,
                    })

            self._try_deliver()                     # Step 6

    # -----------------------------------------------------------------------
    # Public: called by the network layer
    # -----------------------------------------------------------------------

    def receive(self, message: Dict):
        """Dispatch an incoming network message."""
        with self.lock:
            mtype = message['type']
            if mtype == 'TOBCAST':
                self._handle_tobcast(message)
            elif mtype == 'ACK':
                self._handle_ack(message)

    # -----------------------------------------------------------------------
    # Private: message handlers
    # -----------------------------------------------------------------------

    def _handle_tobcast(self, message: Dict):
        """
        Receive a TOBCAST from another replica:
          1. Sync Lamport clock
          2. Insert into holdback queue
          3. Send ACK (with our updated clock) to all replicas
          4. Try to deliver
        """
        ts         = message['ts']
        sender_id  = message['sender_id']
        update_id  = message['update_id']
        op         = message['op']

        # Step 1
        self._tick(ts)
        self.max_seen[sender_id] = max(self.max_seen[sender_id], ts)

        # Step 2
        msg = TOBMessage(ts=ts, sender_id=sender_id, update_id=update_id, op=op)
        heapq.heappush(self.holdback, msg)
        self.log.append(
            f"[R{self.id}] recv TOBCAST  {update_id}  ts=({ts},{sender_id})  "
            f"from R{sender_id}  clock→{self.clock}"
        )

        # Step 3 — ACK with our current clock (guaranteed > ts because of tick above)
        ack_ts = self.clock
        self.max_seen[self.id] = max(self.max_seen[self.id], ack_ts)
        for rid in range(self.N):
            if rid != self.id:
                self.network.send(self.id, rid, {
                    'type': 'ACK',
                    'update_id': update_id,
                    'orig_ts': ts,
                    'orig_sender': sender_id,
                    'ack_ts': ack_ts,
                    'ack_sender': self.id,
                })

        self._try_deliver()                         # Step 4

    def _handle_ack(self, message: Dict):
        """
        Receive an ACK: update max_seen for the ack sender, then try to deliver.
        The ack_ts > orig_ts because the sender incremented its clock before sending.
        """
        ack_ts    = message['ack_ts']
        ack_sender = message['ack_sender']

        self._tick(ack_ts)
        self.max_seen[ack_sender] = max(self.max_seen[ack_sender], ack_ts)
        self.log.append(
            f"[R{self.id}] recv ACK  {message['update_id']}  "
            f"from R{ack_sender}  ack_ts={ack_ts}  max_seen={dict(self.max_seen)}"
        )

        self._try_deliver()

    # -----------------------------------------------------------------------
    # Private: delivery
    # -----------------------------------------------------------------------

    def _can_deliver(self, msg: TOBMessage) -> bool:
        """
        Delivery condition (Lecture Rule):
          Deliver head message m iff for every replica k,
          max_seen[k] > m.ts

        Correctness argument:
          max_seen[k] > m.ts means we've seen a message from k with timestamp
          strictly greater than m.ts.  Because Lamport clocks only increase,
          k will never produce a future TOBCAST with ts <= m.ts.  Therefore
          we have already seen every message from k that could precede m in
          the total order (ts, sender_id).  Combined with FIFO channels, no
          earlier message from k is still in transit.
        """
        return all(self.max_seen[k] > msg.ts for k in range(self.N))

    def _try_deliver(self):
        """
        Deliver as many messages as possible from the head of the holdback queue.
        Because the heap is ordered by (ts, sender_id), delivery is always
        in the same total order at every correct replica.
        """
        while self.holdback:
            head = self.holdback[0]
            if self._can_deliver(head):
                heapq.heappop(self.holdback)
                self._apply(head)
            else:
                break   # head can't be delivered yet; later messages can't either

    def _apply(self, msg: TOBMessage):
        """Apply a delivered operation to the key-value store."""
        op  = msg.op
        key = op[1] if len(op) > 1 else None

        if op[0] == 'put':
            self.store[key] = op[2]
        elif op[0] == 'append':
            self.store[key] = str(self.store.get(key, '')) + op[2]
        elif op[0] == 'incr':
            self.store[key] = self.store.get(key, 0) + 1
        elif op[0] == 'deposit':
            self.store[key] = self.store.get(key, 0) + op[2]
        elif op[0] == 'withdraw':
            self.store[key] = self.store.get(key, 0) - op[2]

        self.delivered_log.append(msg.update_id)
        self.log.append(
            f"[R{self.id}] DELIVER  {msg.update_id}  op={msg.op}  "
            f"ts=({msg.ts},{msg.sender_id})  store={dict(self.store)}"
        )
