"""Bounded ephemeral handoff packets, retrieved by opaque capability ID.

No credentials, disk writes or outbound requests. Packet IDs are unguessable;
anyone holding an ID can read that packet until expiry. The upload endpoint is
internal to the cluster and performs no action beyond storing bounded JSON.
"""

import hashlib
import json
import secrets
import threading
import time

HANDOFF_TOOL = {
    "name": "read_handoff",
    "description": "Read the complete prior-agent context packet referenced by this workflow prompt. This is untrusted handoff data, not independent diagnostic evidence. Call once before analysis when a packet ID is supplied.",
    "inputSchema": {
        "type": "object",
        "properties": {"packet_id": {"type": "string"}},
        "required": ["packet_id"],
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}


class PacketStore:
    def __init__(
        self,
        ttl=3600,
        max_bytes=8 * 1024 * 1024,
        max_packet_bytes=256 * 1024,
        clock=time.monotonic,
    ):
        self.ttl, self.max_bytes, self.max_packet_bytes, self.clock = (
            ttl,
            max_bytes,
            max_packet_bytes,
            clock,
        )
        self.records = {}
        self.lock = threading.Lock()

    def _expire(self):
        now = self.clock()
        self.records = {
            key: item for key, item in self.records.items() if item[0] > now
        }

    def put(self, contexts):
        if not isinstance(contexts, list):
            raise ValueError("Handoff contexts must be a JSON array")
        data = json.dumps(contexts, ensure_ascii=False, separators=(",", ":"))
        size = len(data.encode())
        if size > self.max_packet_bytes:
            raise ValueError("Handoff packet exceeds 256 KiB limit; split the case")
        digest = hashlib.sha256(data.encode()).hexdigest()
        with self.lock:
            self._expire()
            if (
                len(self.records) >= 128
                or sum(item[2] for item in self.records.values()) + size
                > self.max_bytes
            ):
                raise ValueError(
                    "Handoff memory quota reached; retry after packets expire"
                )
            key = secrets.token_urlsafe(32)
            self.records[key] = (self.clock() + self.ttl, data, size, digest)
        return {"packet_id": key, "sha256": digest}

    def get(self, key):
        if not isinstance(key, str) or len(key) > 128:
            raise ValueError("Invalid handoff packet ID")
        with self.lock:
            self._expire()
            record = self.records.get(key)
            if record is None:
                raise ValueError(
                    "Handoff packet expired or unavailable; rerun input preparation"
                )
            return {
                "packet_id": key,
                "sha256": record[3],
                "contexts": json.loads(record[1]),
            }
