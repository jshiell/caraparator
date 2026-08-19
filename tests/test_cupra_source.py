import pytest

from carparator.sources.cupra import parse_cupra_title


@pytest.mark.parametrize(
    "title, battery_kwh, doors",
    [
        ("CUPRA Tavascan 250kW VZ2 77kWh AWD 5dr Auto", 77.0, 5),
        ("CUPRA Born 170kW e-Boost V2 59kWh 5dr Auto", 59.0, 5),
        ("CUPRA Born 140kW V2 58kWh 5dr Auto", 58.0, 5),
        ("CUPRA Tavascan 250kW VZ2 77kWh AWD 5dr Auto *Premium Metallic*", 77.0, 5),
        # AFV titles space the unit and carry no door count.
        ("CUPRA Tavascan VZ1 77 kWh AFV 340 Auto", 77.0, None),
        ("CUPRA Tavascan V2 77 kWh AFV 286 Auto", 77.0, None),
        ("CUPRA Born VZ First Edition 79 kWh AFV 326 Auto", 79.0, None),
        # Neither figure present.
        ("CUPRA Formentor 2.5 TSI 390 VZ5 5dr DSG 4Drive", None, 5),
        ("CUPRA Leon", None, None),
    ],
)
def test_parse_cupra_title_reads_battery_and_doors(title, battery_kwh, doors):
    parsed = parse_cupra_title(title)

    assert parsed.battery_kwh == battery_kwh
    assert parsed.doors == doors


def test_power_in_kilowatts_is_never_mistaken_for_battery_capacity():
    assert parse_cupra_title("CUPRA Tavascan 250kW VZ2 AWD 5dr Auto").battery_kwh is None
