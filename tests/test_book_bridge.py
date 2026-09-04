import base64
import unittest
from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from autonomous_kernel.book_bridge import BookBridgeError, ZLJBookSigner, canonical_json


NOW = datetime(2026, 9, 2, 18, 45, tzinfo=timezone.utc)


class ZLJBookBridgeTests(unittest.TestCase):
    def setUp(self):
        self.signer = ZLJBookSigner(key_id="zlj-k1", private_key=Ed25519PrivateKey.generate())

    def test_public_identity_reserves_only_zlj_namespace(self):
        identity = self.signer.public_identity
        self.assertEqual("ZLJ", identity.producer)
        self.assertEqual("zlj-k1", identity.key_id)
        self.assertEqual(("ZLJ.",), identity.allowed_event_prefixes)
        self.assertTrue(identity.public_key_b64)

    def test_v2_envelope_signature_verifies_with_public_identity(self):
        envelope = self.signer.sign_v2_envelope(
            receipt_id="ZLJ-R1",
            event_type="ZLJ.INTELLIGENCE",
            evidence_class="ANALYTICAL",
            subject_id="INTEL-001",
            occurred_at=NOW,
            known_at=NOW,
            produced_at=NOW,
            payload_digest="0" * 64,
            payload_ref="vault://zlj/intelligence/INTEL-001",
            correlation_id="CASE-001",
        )
        signature = base64.b64decode(str(envelope.pop("signature")), validate=True)
        public_bytes = base64.b64decode(self.signer.public_identity.public_key_b64, validate=True)
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, canonical_json(envelope))

    def test_zlj_signer_cannot_impersonate_benjamin(self):
        with self.assertRaises(BookBridgeError):
            self.signer.sign_v2_envelope(
                receipt_id="FORGED-R1",
                event_type="BENJAMIN.DECISION",
                evidence_class="ECONOMIC",
                subject_id="DEC-001",
                occurred_at=NOW,
                known_at=NOW,
                produced_at=NOW,
                payload_digest="0" * 64,
            )

    def test_private_key_material_is_not_in_public_identity(self):
        identity = self.signer.public_identity.wire()
        self.assertNotIn("private_key", identity)
        self.assertNotIn("private_key_b64", identity)


if __name__ == "__main__":
    unittest.main()
