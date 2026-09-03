import pytest

from zerolab.sender_config import resolve_allowed_sender


def test_absent_override_uses_manifest_sender():
    assert resolve_allowed_sender("192.168.89.171", {}) == "192.168.89.171"


def test_environment_sender_overrides_manifest():
    assert resolve_allowed_sender(
        "192.168.89.171",
        {"ZEROLAB_ALLOWED_SENDER": "192.168.89.200"},
    ) == "192.168.89.200"


def test_explicit_any_environment_sender_disables_filter():
    assert resolve_allowed_sender(
        "192.168.89.171",
        {"ZEROLAB_ALLOWED_SENDER": "any"},
    ) is None


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "ANY",
        " any",
        "any ",
        "customer-pc",
        "2001:db8::1",
        "999.1.1.1",
        " 192.168.89.200",
        "192.168.89.200 ",
    ],
)
def test_invalid_environment_sender_is_rejected(value):
    with pytest.raises(ValueError, match="allowed sender"):
        resolve_allowed_sender(
            "192.168.89.171",
            {"ZEROLAB_ALLOWED_SENDER": value},
        )


def test_invalid_manifest_sender_is_rejected_when_override_is_absent():
    with pytest.raises(ValueError, match="allowed sender"):
        resolve_allowed_sender("customer-pc", {})


def test_non_string_manifest_sender_is_rejected():
    with pytest.raises(ValueError, match="allowed sender"):
        resolve_allowed_sender(171, {})
