"""SSH key generation and a paramiko transport for dulwich with pinned GitHub host keys."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from contextlib import contextmanager
import io
import threading
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from dulwich import client as dulwich_client
from dulwich.client import DEFAULT_GIT_PROTOCOL_VERSION_FETCH, SSHVendor
import paramiko

from .errors import AUTH_FAILED, HOST_KEY_MISMATCH, GitConfigError
from .github_host_keys import GITHUB_HOST_KEYS

GITHUB_HOST = "github.com"
_KEY_CLASSES: dict[str, type[paramiko.PKey]] = {
    "ssh-ed25519": paramiko.Ed25519Key,
    "ecdsa-sha2-nistp256": paramiko.ECDSAKey,
    "ssh-rsa": paramiko.RSAKey,
}
_CONNECT_TIMEOUT = 30


def generate_keypair() -> tuple[str, str]:
    """Return (OpenSSH private key, OpenSSH public key) for a new ed25519 key."""
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        Encoding.PEM, PrivateFormat.OpenSSH, NoEncryption()
    ).decode()
    public = key.public_key().public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH)
    return private, public.decode() + " home-assistant-git-config"


class _RejectUnknownHostKey(paramiko.MissingHostKeyPolicy):
    """Refuse any host key that is not pinned."""

    def missing_host_key(
        self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey
    ) -> None:
        raise GitConfigError(HOST_KEY_MISMATCH, hostname)


class _ChannelWrapper:
    """File-like view of an SSH exec channel, as dulwich expects."""

    def __init__(self, client: paramiko.SSHClient, channel: paramiko.Channel) -> None:
        self._client = client
        self._channel = channel
        self.read = channel.makefile("rb").read
        self.stderr = channel.makefile_stderr("rb")

    def write(self, data: bytes) -> int:
        self._channel.sendall(data)
        return len(data)

    def can_read(self) -> bool:
        return self._channel.recv_ready()

    def close(self, timeout: int | None = None) -> None:
        try:
            self._channel.shutdown_write()
        except (OSError, paramiko.SSHException):
            pass
        self._channel.close()
        self._client.close()


class PinnedSSHVendor(SSHVendor):
    """Authenticate with an in-memory key and trust only GitHub's published host keys."""

    def __init__(self, private_key: str) -> None:
        self._pkey = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key))

    def run_command(
        self,
        host: str,
        command: bytes,
        username: str | None = None,
        port: int | None = None,
        password: str | None = None,
        key_filename: str | None = None,
        ssh_command: str | None = None,
        protocol_version: int | None = None,
    ) -> _ChannelWrapper:
        if host != GITHUB_HOST:
            raise GitConfigError(HOST_KEY_MISMATCH, host)

        client = paramiko.SSHClient()
        host_keys = client.get_host_keys()
        for key_type, data in GITHUB_HOST_KEYS:
            host_keys.add(host, key_type, _KEY_CLASSES[key_type](data=base64.b64decode(data)))
        client.set_missing_host_key_policy(_RejectUnknownHostKey())

        try:
            client.connect(
                host,
                port=port or 22,
                username=username,
                pkey=self._pkey,
                look_for_keys=False,
                allow_agent=False,
                timeout=_CONNECT_TIMEOUT,
                banner_timeout=_CONNECT_TIMEOUT,
                auth_timeout=_CONNECT_TIMEOUT,
            )
            transport = client.get_transport()
            assert transport is not None
            channel = transport.open_session(timeout=_CONNECT_TIMEOUT)
            version = (
                DEFAULT_GIT_PROTOCOL_VERSION_FETCH
                if protocol_version is None
                else protocol_version
            )
            if version > 0:
                try:
                    channel.set_environment_variable("GIT_PROTOCOL", f"version={version}")
                except paramiko.SSHException:
                    pass
            channel.exec_command(command)
        except paramiko.BadHostKeyException as err:
            client.close()
            raise GitConfigError(HOST_KEY_MISMATCH, host) from err
        except paramiko.AuthenticationException as err:
            client.close()
            raise GitConfigError(AUTH_FAILED, str(err)) from err
        except BaseException:
            client.close()
            raise
        return _ChannelWrapper(client, channel)


_vendor_lock = threading.Lock()


@contextmanager
def use_vendor(vendor: SSHVendor) -> Iterator[None]:
    """Make dulwich use the given SSH vendor; dulwich has no per-call hook."""
    with _vendor_lock:
        original: Any = dulwich_client.get_ssh_vendor
        dulwich_client.get_ssh_vendor = lambda: vendor
        try:
            yield
        finally:
            dulwich_client.get_ssh_vendor = original
