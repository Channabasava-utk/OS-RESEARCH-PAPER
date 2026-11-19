import hashlib
import os
import unittest
from ahisf.protocols import RunningAdapter


class AdapterTests(unittest.TestCase):
    def test_rest_and_grpc_return_equivalent_result(self):
        payload = bytes(range(256)) * 8
        expected = hashlib.sha256(payload).digest()
        for protocol in ("rest_json", "grpc_protobuf"):
            with self.subTest(protocol=protocol):
                with RunningAdapter(protocol) as adapter:
                    self.assertNotEqual(adapter.server_process.pid, os.getpid())
                    client = adapter.client_factory()
                    response = client.call(42, payload)
                    client.close()
                    self.assertEqual(response[0], 42)
                    self.assertEqual(response[1], len(payload))
                    self.assertEqual(response[2], expected)
                    self.assertGreater(response[3], 0)
                    self.assertGreater(response[4], 0)


if __name__ == "__main__":
    unittest.main()
