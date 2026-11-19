"""Comparable synchronous request/response adapters for the AHISF prototype.

Both adapters expose the same operation: receive (request_id, payload_bytes),
compute SHA-256 and payload length, and return those values.  Only the wire
encoding differs: REST/JSON with Base64 versus gRPC/Protocol Buffers.
"""
from __future__ import annotations

import base64
import hashlib
import socket
import threading
import time
import multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

import grpc
import requests
import uvicorn
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _build_proto_classes():
    """Build tiny protobuf message types dynamically; no protoc is required."""
    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = "ahisf_echo.proto"
    file_proto.package = "ahisf"
    file_proto.syntax = "proto3"

    req = file_proto.message_type.add()
    req.name = "EchoRequest"
    for name, number, field_type in (
        ("request_id", 1, descriptor_pb2.FieldDescriptorProto.TYPE_UINT64),
        ("payload", 2, descriptor_pb2.FieldDescriptorProto.TYPE_BYTES),
    ):
        field = req.field.add()
        field.name = name
        field.number = number
        field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
        field.type = field_type

    resp = file_proto.message_type.add()
    resp.name = "EchoResponse"
    for name, number, field_type in (
        ("request_id", 1, descriptor_pb2.FieldDescriptorProto.TYPE_UINT64),
        ("payload_size", 2, descriptor_pb2.FieldDescriptorProto.TYPE_UINT64),
        ("sha256", 3, descriptor_pb2.FieldDescriptorProto.TYPE_BYTES),
    ):
        field = resp.field.add()
        field.name = name
        field.number = number
        field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
        field.type = field_type

    pool = descriptor_pool.DescriptorPool()
    pool.Add(file_proto)
    request_desc = pool.FindMessageTypeByName("ahisf.EchoRequest")
    response_desc = pool.FindMessageTypeByName("ahisf.EchoResponse")
    return (
        message_factory.GetMessageClass(request_desc),
        message_factory.GetMessageClass(response_desc),
    )


EchoRequest, EchoResponse = _build_proto_classes()


@dataclass
class AdapterStats:
    name: str
    encoded_request_bytes: int
    encoded_response_bytes: int


class RestJsonServer:
    def __init__(self, host: str = "127.0.0.1", port: int | None = None):
        self.host = host
        self.port = port or _free_port()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

        async def echo(request: Request):
            try:
                body = await request.json()
                request_id = int(body["request_id"])
                payload = base64.b64decode(body["payload_b64"], validate=True)
                digest = hashlib.sha256(payload).hexdigest()
                return JSONResponse(
                    {"request_id": request_id, "payload_size": len(payload), "sha256": digest}
                )
            except Exception as exc:  # Return a protocol error, not an unhandled server traceback.
                return JSONResponse({"error": str(exc)}, status_code=400)

        app = Starlette(routes=[Route("/echo", echo, methods=["POST"])])
        config = uvicorn.Config(
            app,
            host=self.host,
            port=self.port,
            log_level="critical",
            access_log=False,
            loop="asyncio",
            lifespan="off",
            timeout_keep_alive=30,
        )
        self._server = uvicorn.Server(config)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/echo"

    def start(self, timeout: float = 8.0) -> None:
        assert self._server is not None
        self._thread = threading.Thread(target=self._server.run, daemon=True, name="ahisf-rest-server")
        self._thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._server.started:
                return
            if not self._thread.is_alive():
                raise RuntimeError("REST server thread exited before startup")
            time.sleep(0.02)
        raise TimeoutError("REST server did not become ready")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


class GrpcServer:
    def __init__(self, host: str = "127.0.0.1", port: int | None = None, workers: int = 16):
        self.host = host
        self.port = port or _free_port()
        self._executor = ThreadPoolExecutor(max_workers=workers)
        self._server = grpc.server(self._executor, maximum_concurrent_rpcs=None)

        def echo(req, context):
            digest = hashlib.sha256(req.payload).digest()
            return EchoResponse(
                request_id=req.request_id,
                payload_size=len(req.payload),
                sha256=digest,
            )

        handler = grpc.method_handlers_generic_handler(
            "ahisf.Echo",
            {
                "Echo": grpc.unary_unary_rpc_method_handler(
                    echo,
                    request_deserializer=EchoRequest.FromString,
                    response_serializer=lambda msg: msg.SerializeToString(),
                )
            },
        )
        self._server.add_generic_rpc_handlers((handler,))
        self._server.add_insecure_port(f"{self.host}:{self.port}")

    def start(self, timeout: float = 8.0) -> None:
        self._server.start()
        deadline = time.monotonic() + timeout
        target = f"{self.host}:{self.port}"
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((self.host, self.port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.02)
        raise TimeoutError(f"gRPC server did not become ready at {target}")

    def stop(self) -> None:
        self._server.stop(grace=0.2).wait(timeout=3)
        self._executor.shutdown(wait=True, cancel_futures=True)


class RestJsonClient:
    name = "rest_json"

    def __init__(self, url: str):
        self.url = url
        self._local = threading.local()

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=1, pool_maxsize=1, max_retries=0)
            session.mount("http://", adapter)
            self._local.session = session
        return session

    def call(self, request_id: int, payload: bytes) -> tuple[int, int, bytes, int, int]:
        encoded = base64.b64encode(payload).decode("ascii")
        body = {"request_id": request_id, "payload_b64": encoded}
        session = self._session()
        response = session.post(self.url, json=body, timeout=15)
        response.raise_for_status()
        data = response.json()
        digest = bytes.fromhex(data["sha256"])
        request_size = len(requests.models.complexjson.dumps(body, allow_nan=False).encode("utf-8"))
        response_size = len(response.content)
        return int(data["request_id"]), int(data["payload_size"]), digest, request_size, response_size

    def close(self) -> None:
        session = getattr(self._local, "session", None)
        if session is not None:
            session.close()
            del self._local.session


class GrpcClient:
    name = "grpc_protobuf"

    def __init__(self, target: str):
        self.target = target
        self._local = threading.local()

    def _stub(self):
        stub = getattr(self._local, "stub", None)
        if stub is None:
            channel = grpc.insecure_channel(
                self.target,
                options=(
                    ("grpc.keepalive_time_ms", 30_000),
                    ("grpc.max_receive_message_length", 16 * 1024 * 1024),
                    ("grpc.max_send_message_length", 16 * 1024 * 1024),
                ),
            )
            call = channel.unary_unary(
                "/ahisf.Echo/Echo",
                request_serializer=lambda msg: msg.SerializeToString(),
                response_deserializer=EchoResponse.FromString,
            )
            stub = (channel, call)
            self._local.stub = stub
        return stub

    def call(self, request_id: int, payload: bytes) -> tuple[int, int, bytes, int, int]:
        channel, call = self._stub()
        req = EchoRequest(request_id=request_id, payload=payload)
        wire_request = req.SerializeToString()
        response = call(req, timeout=15)
        wire_response = response.SerializeToString()
        return (
            int(response.request_id),
            int(response.payload_size),
            bytes(response.sha256),
            len(wire_request),
            len(wire_response),
        )

    def close(self) -> None:
        stub = getattr(self._local, "stub", None)
        if stub is not None:
            stub[0].close()
            del self._local.stub


def _serve_in_child_process(protocol: str, host: str, port: int, ready_event, stop_event) -> None:
    """Child-process entry point. The server does not share the client process."""
    server = None
    try:
        if protocol == "rest_json":
            server = RestJsonServer(host=host, port=port)
        elif protocol == "grpc_protobuf":
            server = GrpcServer(host=host, port=port)
        else:
            raise ValueError(f"Unsupported protocol: {protocol}")
        server.start()
        ready_event.set()
        while not stop_event.wait(0.1):
            pass
    except BaseException:
        # The parent detects a failed start if the ready event is never set.
        raise
    finally:
        if server is not None:
            try:
                server.stop()
            except Exception:
                pass


class RunningAdapter:
    """Start a server in a distinct OS process and provide thread-local clients."""
    def __init__(self, protocol: str):
        if protocol not in ("rest_json", "grpc_protobuf"):
            raise ValueError(f"Unsupported protocol: {protocol}")
        self.protocol = protocol
        self.host = "127.0.0.1"
        self.port = _free_port()
        if protocol == "rest_json":
            self.target = f"http://{self.host}:{self.port}/echo"
            self.client_factory: Callable[[], RestJsonClient] = lambda: RestJsonClient(self.target)
        else:
            self.target = f"{self.host}:{self.port}"
            self.client_factory = lambda: GrpcClient(self.target)
        self._ctx = mp.get_context("spawn")
        self._ready = self._ctx.Event()
        self._stop = self._ctx.Event()
        self.server_process = self._ctx.Process(
            target=_serve_in_child_process,
            args=(protocol, self.host, self.port, self._ready, self._stop),
            name=f"ahisf-{protocol}-server",
        )

    def __enter__(self):
        self.server_process.start()
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if self._ready.is_set():
                return self
            if not self.server_process.is_alive():
                raise RuntimeError(
                    f"{self.protocol} server process exited during startup "
                    f"(exit code {self.server_process.exitcode})"
                )
            time.sleep(0.025)
        self.__exit__(None, None, None)
        raise TimeoutError(f"{self.protocol} server process did not become ready")

    def __exit__(self, exc_type, exc, tb):
        if self.server_process.is_alive():
            self._stop.set()
            self.server_process.join(timeout=8.0)
        if self.server_process.is_alive():
            self.server_process.terminate()
            self.server_process.join(timeout=3.0)
        if self.server_process.is_alive() and hasattr(self.server_process, "kill"):
            self.server_process.kill()
            self.server_process.join(timeout=1.0)
