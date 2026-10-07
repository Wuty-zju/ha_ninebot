"""Rebuild localized entity/UI names; untouched long text falls back to English.

No runtime dependency or translation service. Existing English and Simplified
Chinese files are the maintained canonical messages; the small locale lexicon
keeps common labels consistent. Do not describe fallback text as translated.
"""

import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "custom_components/ninebot"

CATALOG = json.loads((ROOT / "scripts/localization_labels.json").read_text())

SENSOR_WORDS = {
    "battery": "battery",
    "endurance": "range",
    "remaining_charge_time": "charge_time",
    "charging_power_raw": "power",
    "returned_pack_count": "packs",
    "service_remaining_days_raw": "service_days",
    "odometer_raw": "odometer",
    "battery_present_raw": "presence",
    "seat_lock_raw": "seat_lock",
    "acc_raw": "acc",
    "service_expired_raw": "service",
    "bms_voltage": "voltage",
    "batt_temp": "temperature",
    "bms_cycles": "cycles",
    "health_score": "health",
    "control_availability": "controls",
    "raw_data_summary": "parsed",
    "battery_rated_energy": "rated",
}
COMPOSITE = {
    "month_mileage": ("month", "distance"),
    "month_energy_raw": ("month", "energy"),
    "month_ride_count": ("month", "rides"),
    "month_duration": ("month", "duration"),
    "month_list_coverage": ("month", "coverage"),
    "last_mileage": ("last", "distance"),
    "last_energy_raw": ("last", "energy"),
    "last_ride_duration": ("last", "duration"),
    "last_ride_start": ("last", "start"),
    "last_ride_end": ("last", "end"),
    "last_ride_max_speed": ("last", "max_speed"),
    "last_ride_average_speed": ("last", "avg_speed"),
    "today_mileage": ("today", "distance"),
    "yesterday_mileage": ("yesterday", "distance"),
    "today_ride_count": ("today", "rides"),
    "yesterday_ride_count": ("yesterday", "rides"),
    "today_ride_duration": ("today", "duration"),
    "yesterday_ride_duration": ("yesterday", "duration"),
    "today_ride_energy": ("today", "energy"),
    "yesterday_ride_energy": ("yesterday", "energy"),
    "month_energy_intensity": ("month", "intensity"),
    "last_energy_intensity": ("last", "intensity"),
}


def save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    en = json.loads((DIRECTORY / "translations/en.json").read_text())
    zh = json.loads((DIRECTORY / "translations/zh-Hans.json").read_text())
    allowed = set(SENSOR_WORDS) | set(COMPOSITE)
    for data in (en, zh):
        data["entity"]["sensor"] = {
            k: v for k, v in data["entity"]["sensor"].items() if k in allowed
        }
        data["entity"]["sensor"]["battery_rated_energy"] = {
            "name": "Rated battery energy" if data is en else "电池额定能量"
        }
        if data is en:
            data["entity"]["sensor"]["service_expired_raw"]["name"] = "Smart service status"
        data["entity"]["sensor"]["endurance"]["name"] = (
            "Remaining range" if data is en else "剩余续航"
        )
        for value in data["entity"]["sensor"].values():
            value["name"] = value["name"].removesuffix(" raw").removesuffix("原值").strip()
        data["entity"]["number"]["main_battery_voltage"]["name"] = (
            "Rated battery voltage" if data is en else "电池额定电压"
        )
        data["entity"]["number"]["battery_capacity"]["name"] = (
            "Rated battery capacity" if data is en else "电池额定容量"
        )
        for step in data["config"]["step"].values():
            if "enable_estimation" in step.get("data", {}):
                step["data"]["enable_estimation"] = (
                    "Enable rated battery parameters" if data is en else "启用电池额定参数"
                )
                step.get("data_description", {})["enable_estimation"] = (
                    "Optional rated V and Ah; no SOC-derived charging or energy counters."
                    if data is en
                    else "可选额定电压与容量，不再从SOC变化推算充放电量。"
                )
        init = data["options"]["step"]["init"]
        init["data"]["enable_estimation"] = (
            "Enable rated battery parameters" if data is en else "启用电池额定参数"
        )
        init["data"]["configure_model"] = (
            "Configure rated battery parameters" if data is en else "配置电池额定参数"
        )
        init["description"] = (
            (
                "Rated voltage and capacity are optional specifications, not measured"
                " energy. Coordinates and vehicle controls require explicit opt-in; a"
                " cloud-accepted command does not confirm a physical action."
            )
            if data is en
            else "额定参数并非实测容量。位置与控制须显式开启，云端接受不代表动作完成。"
        )
        init["data_description"]["configure_model"] = (
            (
                "Select the HA vehicle device. Parameters share storage with the two "
                "vehicle configuration controls."
            )
            if data is en
            else "选择HA中的车辆设备；与车辆的两个额定参数配置实体共用存储。"
        )
        data["options"]["step"]["model_vehicle"]["title"] = (
            "Choose vehicle" if data is en else "选择车辆"
        )
        params = data["options"]["step"]["model_parameters"]
        params["description"] = (
            (
                "Enter rated voltage and capacity. Changes update the same entity; "
                "these are not measured capacity or battery health."
            )
            if data is en
            else "填写额定电压与容量。修改参数更新同一个实体；此数值不代表实测容量或电池健康度。"
        )
        data["options"]["error"] = {
            "model_unavailable": "Choose a current vehicle belonging to this integration account."
            if data is en
            else "请选择当前集成账户下可用的车辆设备。",
            ("model_storage_invalid"): (
                "Battery parameter storage is unavailable. Resolve the Repair before saving."
            )
            if data is en
            else "电池参数存储不可写，请先处理修复提示。",
        }
        data["options"]["abort"]["model_unavailable"] = (
            "Load the integration and choose a current vehicle before configuring rated parameters."
            if data is en
            else "请先加载集成，再选择当前账户的车辆配置额定参数。"
        )
        data["exceptions"]["model_storage_invalid"]["message"] = data["options"]["error"][
            "model_storage_invalid"
        ]
        issue = data.get("issues", {}).get("model_storage_invalid")
        if issue:
            issue["title"] = (
                "Battery parameter storage unavailable" if data is en else "电池参数存储不可用"
            )
            issue["description"] = (
                (
                    "Cloud telemetry continues, but the optional battery parameter file "
                    "is invalid or uses an unsupported version. The file has not been "
                    "overwritten. Restore a compatible backup before changing parameters."
                )
                if data is en
                else "遥测继续运行，参数文件暂不可写且未被覆盖。请恢复兼容备份后重试。"
            )
    for key, name in {
        "service_remaining_days_raw": "智能服务剩余天数",
        "odometer_raw": "总里程",
        "battery_present_raw": "电池安装状态",
        "seat_lock_raw": "座桶锁状态",
        "acc_raw": "ACC状态",
        "service_expired_raw": "智能服务状态",
        "returned_pack_count": "电池数量",
        "health_score": "电池健康评分",
        "raw_data_summary": "车辆信息",
    }.items():
        zh["entity"]["sensor"][key]["name"] = name
    save(DIRECTORY / "strings.json", en)
    save(DIRECTORY / "translations/en.json", en)
    save(DIRECTORY / "translations/zh-Hans.json", zh)
    icons = json.loads((DIRECTORY / "icons.json").read_text())
    icons["entity"]["sensor"] = {k: v for k, v in icons["entity"]["sensor"].items() if k in allowed}
    icons["entity"]["sensor"]["battery_rated_energy"] = {"default": "mdi:battery-high"}
    save(DIRECTORY / "icons.json", icons)
    for locale, vocabulary in CATALOG.items():
        words = vocabulary["labels"]
        ui = vocabulary["ui"]
        data = deepcopy(en)
        previous = json.loads((DIRECTORY / "translations" / f"{locale}.json").read_text())
        # Keep curated Repair translations instead of replacing them with English.
        for key in data["issues"]:
            if key in previous.get("issues", {}):
                data["issues"][key] = previous["issues"][key]
        for key, word in SENSOR_WORDS.items():
            data["entity"]["sensor"][key]["name"] = words[word]
        for key, (prefix, word) in COMPOSITE.items():
            data["entity"]["sensor"][key]["name"] = f"{words[prefix]}: {words[word]}"
        for platform, fields in {
            "binary_sensor": {
                "charging": "charging",
                "power": "main_power",
                "unlocked": "unlocked",
                "cycle_support": "cycle_support",
                "battery_find_my_support": "find_support",
            },
            "number": {"main_battery_voltage": "nominal", "battery_capacity": "capacity"},
            "button": {
                "refresh": "refresh",
                "bell": "find",
                "bucket": "open_seat",
                "engine_start": "engine_start",
                "engine_stop": "engine_stop",
            },
            "event": {"ride": "event"},
            "image": {"vehicle_image": "image"},
            "device_tracker": {"location": "location"},
        }.items():
            for key, word in fields.items():
                data["entity"][platform][key]["name"] = words[word]
        data["selector"]["login_method"]["options"] = {"password": ui["login"], "sms": ui["sms"]}
        for step in data["config"]["step"].values():
            for key, word in {
                "account": "account",
                "password": "password",
                "login_method": "method",
                "debug_mode": "debug",
                "enable_estimation": "enable_parameters",
                "enable_coordinates": "coordinates",
                "code": "code",
                "resend": "resend",
            }.items():
                if key in step.get("data", {}):
                    step["data"][key] = ui[word]
        if "sms" in data["config"]["step"]:
            data["config"]["step"]["sms"]["title"] = ui["sms"]
        init = data["options"]["step"]["init"]
        init["title"] = ui["options"]
        for key, word in {
            "poll_interval": "poll",
            "debug_mode": "debug",
            "configure_model": "parameters",
            "enable_estimation": "enable_parameters",
            "enable_coordinates": "coordinates",
            "enable_controls": "controls",
            "control_vehicles": "allowlist",
        }.items():
            init["data"][key] = ui[word]
        data["options"]["step"]["model_vehicle"]["title"] = ui["vehicle"]
        data["options"]["step"]["model_vehicle"]["data"]["model_vehicle"] = ui["vehicle"]
        params = data["options"]["step"]["model_parameters"]
        params["title"] = ui["parameters"]
        params["data"] = {
            "voltage": f"{words['nominal']} (V)",
            "capacity": f"{words['capacity']} (Ah)",
        }
        if "today_mileage" in previous.get("entity", {}).get("sensor", {}):
            data["entity"]["sensor"]["today_mileage"] = previous["entity"]["sensor"][
                "today_mileage"
            ]
        for action in (
            "get_trips",
            "get_trip_detail",
            "get_history",
            "get_statistics",
            "import_statistics",
            "get_entity_migration",
        ):
            data["services"][action]["name"] = ui[action]
        save(DIRECTORY / "translations" / f"{locale}.json", data)


if __name__ == "__main__":
    main()
