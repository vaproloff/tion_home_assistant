"""Tests for the device datapoint messages."""

import pytest

from custom_components.tion.api.datapoints import (
    DPKind,
    DPStateReport,
    DPUpdateResponse,
    DPValue,
    decode_dp_value,
    decode_state_report,
    decode_update_response,
    device_reports_subject,
    device_subject,
    encode_dp_value,
    encode_state_query,
    encode_update_request,
    location_events_subject,
    new_command_id,
    parse_device_subject,
    parse_location_event,
)
from custom_components.tion.api.protobuf import (
    ProtoMessage,
    encode_bytes,
    encode_string,
    encode_varint,
)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(DPValue(70, DPKind.BOOL, False), id="bool_false"),
        pytest.param(DPValue(70, DPKind.BOOL, True), id="bool_true"),
        pytest.param(DPValue(140, DPKind.INT, 3), id="int"),
        pytest.param(DPValue(17, DPKind.INT, -70), id="negative_int"),
        pytest.param(DPValue(84, DPKind.ENUM, 2), id="enum"),
        pytest.param(DPValue(20, DPKind.STRING, "1.2"), id="string"),
        pytest.param(DPValue(30, DPKind.RAW, b"\x03\xfb"), id="raw"),
        pytest.param(DPValue(10, DPKind.FLAGS, 0x13), id="flags"),
        pytest.param(DPValue(7, DPKind.FLOAT, 21.5), id="float"),
        pytest.param(DPValue(70, DPKind.BOOL, True, valid=0), id="not_valid"),
    ],
)
def test_dp_value_roundtrip(value: DPValue) -> None:
    """Every value kind survives encode and decode."""
    assert decode_dp_value(ProtoMessage.parse(encode_dp_value(value))) == value


def test_raw_dp_value_must_be_bytes() -> None:
    """A RAW value is sent as given, never made up from a number."""
    with pytest.raises(TypeError):
        encode_dp_value(DPValue(30, DPKind.RAW, 2))


def test_dp_value_ignores_gateway_timestamp() -> None:
    """Field 9 is a gateway counter, not a clock; it is not kept."""
    payload = encode_varint(2, 387) + encode_varint(8, 110) + encode_varint(9, 96608568)

    assert decode_dp_value(ProtoMessage.parse(payload)) == DPValue(
        110, DPKind.INT, 387, valid=0
    )


def test_dp_value_without_member_is_skipped() -> None:
    """A DPValue carrying no value member decodes to None."""
    assert decode_dp_value(ProtoMessage.parse(encode_varint(8, 70))) is None


@pytest.mark.parametrize(
    ("command_id", "timestamp", "value", "recorded"),
    [
        pytest.param(
            2188847386,
            1790865786,
            DPValue(70, DPKind.BOOL, False),
            "109ad2dc930818fae2f9d5062206080040465001",
            id="power_off",
        ),
        pytest.param(
            717914668,
            1790865844,
            DPValue(140, DPKind.INT, 3),
            "10ac84aad60218b4e3f9d50622071003408c015001",
            id="speed_3",
        ),
    ],
)
def test_update_request_matches_app(
    command_id: int, timestamp: int, value: DPValue, recorded: str
) -> None:
    """Commands are byte-identical to the Tion app's (stage 3C recording)."""
    assert encode_update_request(command_id, timestamp, [value]) == bytes.fromhex(
        recorded
    )


@pytest.mark.parametrize(
    ("dp_ids", "command_id", "expected"),
    [
        # DPStateQuery{dp_ids_to_query=[10]} as recorded from the Tion app.
        pytest.param([10], 0, "1a010a", id="recorded"),
        pytest.param([10, 140], 5, "1a030a8c012005", id="with_command_id"),
    ],
)
def test_state_query_bytes(dp_ids: list[int], command_id: int, expected: str) -> None:
    """Queries carry packed ids and an optional command id, no device id."""
    assert encode_state_query(dp_ids, command_id) == bytes.fromhex(expected)


def test_recorded_update_response_decodes() -> None:
    """The device's answer to the recorded power-off command."""
    response = decode_update_response(bytes.fromhex("109ad2dc930818013206080040465001"))

    assert response == DPUpdateResponse(
        original_command_id=2188847386,
        success=True,
        error_code=0,
        error_message="",
        dps=(DPValue(70, DPKind.BOOL, False),),
    )


def test_failed_update_response_decodes() -> None:
    """A refused command carries the error code and message."""
    payload = encode_varint(2, 9) + encode_varint(4, 5) + encode_string(5, "busy")

    assert decode_update_response(payload) == DPUpdateResponse(
        original_command_id=9,
        success=False,
        error_code=5,
        error_message="busy",
        dps=(),
    )


def test_state_report_decodes() -> None:
    """Reports name the device and skip values without a member."""
    payload = (
        encode_string(1, "DEV0000001")
        + encode_varint(2, 96608447)
        + encode_bytes(4, encode_dp_value(DPValue(10, DPKind.FLAGS, 0x13)))
        + encode_bytes(4, encode_varint(8, 99))
        + encode_bytes(4, encode_dp_value(DPValue(110, DPKind.INT, 387)))
        + encode_varint(5, 42)
    )

    assert decode_state_report(payload) == DPStateReport(
        device_id="DEV0000001",
        original_command_id=42,
        dps=(DPValue(10, DPKind.FLAGS, 0x13), DPValue(110, DPKind.INT, 387)),
    )


def test_subjects() -> None:
    """Subjects follow hw.<rx|tx>.<location sid>.<service>.<device id>."""
    assert device_subject("LOC0000001", "dpu", "DEV0000001") == (
        "hw.rx.LOC0000001.dpu.DEV0000001"
    )
    assert device_reports_subject("LOC0000001") == "hw.tx.LOC0000001.>"
    assert location_events_subject("LOC0000001") == "app.location.LOC0000001.*"


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        pytest.param(
            "hw.tx.LOC0000001.dps.DEV0000001",
            ("LOC0000001", "dps", "DEV0000001"),
            id="report",
        ),
        pytest.param("hw.rx.LOC0000001.dps.DEV0000001", None, id="request"),
        pytest.param("hw.tx.LOC0000001.dps", None, id="short"),
        pytest.param("app.location.LOC0000001.Changed", None, id="event"),
    ],
)
def test_parse_device_subject(
    subject: str, expected: tuple[str, str, str] | None
) -> None:
    """Only device-to-app subjects are parsed."""
    assert parse_device_subject(subject) == expected


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        pytest.param(
            "app.location.LOC0000001.AutoControlChanged",
            ("LOC0000001", "AutoControlChanged"),
            id="event",
        ),
        pytest.param("hw.tx.LOC0000001.dps.DEV0000001", None, id="device"),
    ],
)
def test_parse_location_event(subject: str, expected: tuple[str, str] | None) -> None:
    """Only location events are parsed."""
    assert parse_location_event(subject) == expected


def test_new_command_id_is_nonzero_uint32() -> None:
    """Command ids fit uint32 and are never the 'no command' zero."""
    ids = {new_command_id() for _ in range(200)}

    assert all(0 < command_id < 2**32 for command_id in ids)
    assert len(ids) > 1
