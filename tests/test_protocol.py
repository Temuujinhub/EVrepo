"""OCPP frame задлах/угсрах — цэвэр логикийн тест (DB шаардлагагүй)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hub.ocpp import protocol
from hub.ocpp.handlers import parse_meter_values
from hub.auth import parse_basic


def test_parse_call():
    mtype, mid, action, payload = protocol.parse(
        '[2,"abc","Heartbeat",{}]')
    assert mtype == protocol.CALL
    assert (mid, action, payload) == ("abc", "Heartbeat", {})


def test_parse_call_result():
    mtype, mid, action, payload = protocol.parse('[3,"abc",{"x":1}]')
    assert mtype == protocol.CALLRESULT
    assert action is None and payload == {"x": 1}


def test_parse_call_error():
    mtype, mid, code, payload = protocol.parse(
        '[4,"abc","NotImplemented","desc",{}]')
    assert mtype == protocol.CALLERROR
    assert code == "NotImplemented"
    assert payload["description"] == "desc"


@pytest.mark.parametrize("raw", [
    "not json", "{}", "[]", "[9]", '[2,"id"]', '[5,"id","x",{}]',
])
def test_parse_bad_frames(raw):
    with pytest.raises(protocol.OcppProtocolError):
        protocol.parse(raw)


def test_call_roundtrip():
    mid, text = protocol.call("BootNotification", {"a": 1})
    msg = json.loads(text)
    assert msg == [2, mid, "BootNotification", {"a": 1}]
    assert json.loads(protocol.call_result("id1", {"ok": True})) == [3, "id1", {"ok": True}]
    err = json.loads(protocol.call_error("id2", "InternalError", "x"))
    assert err[:4] == [4, "id2", "InternalError", "x"]


def test_meter_values_wh():
    rows = parse_meter_values({"meterValue": [{
        "timestamp": "2026-08-27T05:00:00Z",
        "sampledValue": [
            {"value": "12345", "measurand": "Energy.Active.Import.Register", "unit": "Wh"},
            {"value": "38.5", "measurand": "Power.Active.Import", "unit": "kW"},
            {"value": "77", "measurand": "SoC"},
        ]}]})
    assert rows[0]["energy_wh"] == 12345
    assert rows[0]["power_w"] == 38500.0
    assert rows[0]["soc"] == 77.0


def test_meter_values_kwh_conversion():
    rows = parse_meter_values({"meterValue": [{
        "sampledValue": [
            {"value": "12.4", "measurand": "Energy.Active.Import.Register", "unit": "kWh"},
        ]}]})
    assert rows[0]["energy_wh"] == 12400


def test_meter_values_default_measurand():
    # measurand заагаагүй бол Energy.Active.Import.Register гэж үзнэ (OCPP default)
    rows = parse_meter_values({"meterValue": [{"sampledValue": [{"value": "500"}]}]})
    assert rows[0]["energy_wh"] == 500


def test_meter_values_garbage():
    rows = parse_meter_values({"meterValue": [{"sampledValue": [
        {"value": "abc", "measurand": "Energy.Active.Import.Register"}]}]})
    assert rows[0]["energy_wh"] is None
    assert parse_meter_values({}) == []


def test_parse_basic_auth():
    import base64
    token = base64.b64encode(b"CP01:secret:with:colons").decode()
    assert parse_basic(f"Basic {token}") == ("CP01", "secret:with:colons")
    assert parse_basic(None) is None
    assert parse_basic("Bearer xyz") is None
    assert parse_basic("Basic not-b64!!!") is None
