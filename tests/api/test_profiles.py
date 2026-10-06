"""Tests for the device profile catalog."""

from pathlib import Path
from uuid import UUID

import pytest

from custom_components.tion.api.profiles import (
    DeviceProfile,
    DPAccess,
    DPSpec,
    DPType,
    decode_profile,
    decode_profiles,
)
from custom_components.tion.api.protobuf import (
    encode_bytes,
    encode_string,
    encode_varint,
)

# A real GetDeviceProfiles response (stage 3D recording): the device schema
# Tion serves to every account, without personal data.
FIXTURE = Path(__file__).parent / "fixtures" / "device_profiles.bin"


@pytest.fixture(scope="module")
def profiles() -> dict[UUID, DeviceProfile]:
    """Decode the recorded catalog once."""
    return decode_profiles(FIXTURE.read_bytes())


def _by_product(profiles: dict[UUID, DeviceProfile], product_id: str) -> DeviceProfile:
    return next(p for p in profiles.values() if p.product_id == product_id)


def test_catalog_has_every_model(profiles: dict[UUID, DeviceProfile]) -> None:
    """The catalog lists all eleven Tion products."""
    assert sorted(p.product_id for p in profiles.values()) == [
        "b4s_ble",
        "br3s_rf",
        "bs3xx_rf",
        "bs4xx_rf",
        "clever_rf",
        "deco2_rf",
        "iq2xx_rf",
        "iq4xx_rf",
        "ir_tablet",
        "o2_rf",
        "thco2_rf",
    ]


@pytest.mark.parametrize(
    ("profile_id", "product_id", "model", "is_gateway"),
    [
        # Ids also seen as DeviceInfo.ProfileId in the stage 3C structure.
        pytest.param(
            "01a0cd78-0d3e-78b5-a357-a0b7d1c7af0b", "b4s_ble", 32771, False, id="4s"
        ),
        pytest.param(
            "01a08a28-c009-7883-9fc3-dffd5092d10d", "bs3xx_rf", 16384, True, id="bs310"
        ),
    ],
)
def test_profile_identity(
    profiles: dict[UUID, DeviceProfile],
    profile_id: str,
    product_id: str,
    model: int,
    is_gateway: bool,
) -> None:
    """Profile ids decode from .NET Guids to the ids devices reference."""
    profile = profiles[UUID(profile_id)]

    assert (profile.product_id, profile.model, profile.is_gateway) == (
        product_id,
        model,
        is_gateway,
    )


@pytest.mark.parametrize(
    ("product_id", "code", "expected"),
    [
        pytest.param(
            "b4s_ble",
            "target_temperature",
            DPSpec(
                130,
                "target_temperature",
                DPType.VALUE,
                DPAccess.READ_WRITE,
                minimum=-1000,
                maximum=1000,
                step=1,
                scale=1,
                unit="°C",
            ),
            id="4s_target_temperature",
        ),
        pytest.param(
            "b4s_ble",
            "heater_on_off",
            DPSpec(
                84,
                "heater_on_off",
                DPType.VALUE,
                DPAccess.READ_WRITE,
                minimum=0,
                maximum=10,
                step=1,
            ),
            id="4s_heater",
        ),
        pytest.param(
            "br3s_rf",
            "heater_on_off",
            DPSpec(
                72,
                "heater_on_off",
                DPType.BOOL,
                DPAccess.READ_WRITE,
                labels={1: "", 0: ""},
            ),
            id="3s_heater",
        ),
        pytest.param(
            "b4s_ble",
            "on_off",
            DPSpec(
                70,
                "on_off",
                DPType.BOOL,
                DPAccess.READ_WRITE,
                labels={1: "вкл", 0: "выкл"},
            ),
            id="4s_power",
        ),
        pytest.param(
            "o2_rf",
            "temp_outdoor",
            DPSpec(
                100,
                "temp_outdoor",
                DPType.VALUE,
                DPAccess.READ_ONLY,
                minimum=-500,
                maximum=500,
                step=1,
                scale=1,
                unit="°C",
            ),
            id="o2_negative_minimum",
        ),
    ],
)
def test_datapoint_specs(
    profiles: dict[UUID, DeviceProfile], product_id: str, code: str, expected: DPSpec
) -> None:
    """Datapoint specs carry id, type, access, range, scale and labels."""
    assert _by_product(profiles, product_id).by_code(code) == expected


def test_flags_labels(profiles: dict[UUID, DeviceProfile]) -> None:
    """Flag datapoints name their bits."""
    climatic = _by_product(profiles, "b4s_ble").spec(11)

    assert climatic is not None
    assert climatic.type is DPType.FLAGS
    assert climatic.labels == {
        0: "heaterInstall",
        1: "heaterOn",
        2: "filterNeedReplace",
        3: "reserved",
    }


@pytest.mark.parametrize(
    ("product_id", "code"),
    [
        pytest.param("br3s_rf", "beeper_on_off", id="3s_no_sound"),
        pytest.param("o2_rf", "brightness_onoff", id="o2_no_backlight"),
        pytest.param("bs3xx_rf", "pm2p5", id="bs310_no_pm"),
    ],
)
def test_missing_datapoints(
    profiles: dict[UUID, DeviceProfile], product_id: str, code: str
) -> None:
    """Models without a feature have no datapoint for it."""
    assert _by_product(profiles, product_id).by_code(code) is None


def test_writable(profiles: dict[UUID, DeviceProfile]) -> None:
    """Read-only datapoints do not accept commands."""
    breezer = _by_product(profiles, "b4s_ble")

    assert breezer.by_code("fan_speed_level").writable
    assert not breezer.by_code("temp_outdoor").writable


def test_empty_catalog() -> None:
    """A response without profiles decodes to nothing."""
    assert decode_profiles(b"") == {}


def test_unknown_dp_type_is_skipped() -> None:
    """A datapoint of a type newer than this code does not break the profile."""
    future_dp = (
        encode_varint(1, 300)
        + encode_string(2, "future")
        + encode_varint(4, 99)
        + encode_varint(5, 1)
    )
    known_dp = (
        encode_varint(1, 70)
        + encode_string(2, "on_off")
        + encode_varint(4, 1)
        + encode_varint(5, 1)
    )
    source = (
        encode_string(1, "b4s_ble")
        + encode_bytes(13, future_dp)
        + encode_bytes(13, known_dp)
    )

    profile = decode_profile(UUID(int=1), source)

    assert [spec.code for spec in profile.dps] == ["on_off"]
