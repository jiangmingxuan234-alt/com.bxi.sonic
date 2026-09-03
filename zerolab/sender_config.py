"""Resolve the ZeroLab UDP sender allowlist without ROS dependencies."""

from collections.abc import Mapping
from ipaddress import IPv4Address


ALLOWED_SENDER_ENV = "ZEROLAB_ALLOWED_SENDER"


def resolve_allowed_sender(
    manifest_sender: object,
    environ: Mapping[str, str],
) -> str | None:
    candidate = environ.get(ALLOWED_SENDER_ENV, manifest_sender)
    if not isinstance(candidate, str):
        raise ValueError("allowed sender must be an IPv4 address or empty")
    if candidate == "":
        return None
    if candidate != candidate.strip():
        raise ValueError("allowed sender must not contain surrounding whitespace")
    try:
        address = IPv4Address(candidate)
    except ValueError as error:
        raise ValueError(
            "allowed sender must be an IPv4 address or empty"
        ) from error
    return str(address)
