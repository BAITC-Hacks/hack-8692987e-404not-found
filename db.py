from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List


ROOT = Path(__file__).parent
DB_PATH = ROOT / "campaigns.db"
TARIFFS = [f"tariff_{index}" for index in range(1, 22)]


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def initialize(path: Path = DB_PATH) -> Path:
    with connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS customer_profile (
                customer_id INTEGER PRIMARY KEY,
                current_tariff TEXT NOT NULL,
                arpu_segment TEXT NOT NULL,
                data_segment TEXT NOT NULL,
                call_segment TEXT NOT NULL,
                predicted_arpu REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tariff_dictionary (
                tariff_id TEXT PRIMARY KEY,
                monthly_price REAL NOT NULL,
                data_gb REAL NOT NULL,
                voice_minutes INTEGER NOT NULL,
                sms_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS traffic (
                customer_id INTEGER NOT NULL,
                month TEXT NOT NULL,
                minutes REAL NOT NULL,
                sms REAL NOT NULL,
                data_mb REAL NOT NULL,
                device_type TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS arpu_monthly (
                customer_id INTEGER NOT NULL,
                month TEXT NOT NULL,
                arpu REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS change_tariff (
                customer_id INTEGER NOT NULL,
                month TEXT NOT NULL,
                from_tariff TEXT NOT NULL,
                to_tariff TEXT NOT NULL,
                arpu_before REAL NOT NULL,
                arpu_after REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feature_dictionary (
                feature_name TEXT PRIMARY KEY,
                description TEXT NOT NULL
            );
            """
        )
        existing = connection.execute("SELECT COUNT(*) FROM customer_profile").fetchone()[0]
        if existing == 0:
            _seed(connection)
    return path


def load_profile(path: Path = DB_PATH) -> List[Dict[str, Any]]:
    initialize(path)
    with connect(path) as connection:
        rows = connection.execute("SELECT * FROM customer_profile ORDER BY customer_id").fetchall()
    return [dict(row) for row in rows]


def database_summary(path: Path = DB_PATH) -> Dict[str, int]:
    initialize(path)
    tables = ["customer_profile", "tariff_dictionary", "traffic", "arpu_monthly", "change_tariff"]
    with connect(path) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }


def _seed(connection: sqlite3.Connection) -> None:
    profiles = []
    arpu_values = {"LOW": 700, "MID": 2500, "HIGH": 6200}
    data_values = {"NON_USER": 0, "LITE": 900, "HEAVY": 4800}
    call_values = {"LOW": 55, "MEDIUM": 220, "HIGH": 620}
    for customer_id in range(1, 23442):
        arpu_segment = ("LOW", "MID", "HIGH")[customer_id % 3]
        data_segment = ("NON_USER", "LITE", "HEAVY")[(customer_id // 3) % 3]
        call_segment = ("LOW", "MEDIUM", "HIGH")[(customer_id // 9) % 3]
        current = TARIFFS[(customer_id - 1) % 21]
        predicted = arpu_values[arpu_segment] + (customer_id % 17) * 35
        profiles.append(
            (customer_id, current, arpu_segment, data_segment, call_segment, predicted)
        )
    connection.executemany(
        "INSERT INTO customer_profile VALUES (?, ?, ?, ?, ?, ?)", profiles
    )

    tariffs = []
    for number, tariff_id in enumerate(TARIFFS, start=1):
        tariffs.append((tariff_id, 250 + number * 180, number * 2.0, number * 80, number * 35))
    connection.executemany("INSERT INTO tariff_dictionary VALUES (?, ?, ?, ?, ?)", tariffs)

    traffic = []
    arpu_monthly = []
    for customer_id, _, arpu_segment, data_segment, call_segment, predicted in profiles:
        data_mb = data_values[data_segment] + customer_id % 250
        minutes = call_values[call_segment] + customer_id % 40
        sms = 20 + customer_id % 80
        for month_number in (1, 2, 3):
            month = f"2026-{month_number:02d}"
            traffic.append((customer_id, month, minutes, sms, data_mb, "smartphone"))
            arpu_monthly.append((customer_id, month, predicted - 80 + month_number * 20))
    connection.executemany("INSERT INTO traffic VALUES (?, ?, ?, ?, ?, ?)", traffic)
    connection.executemany("INSERT INTO arpu_monthly VALUES (?, ?, ?)", arpu_monthly)

    changes = []
    for customer_id in range(1, 14825):
        from_number = (customer_id - 1) % 20 + 1
        to_number = min(21, from_number + 1 + (customer_id % 3))
        before = 500 + (customer_id % 17) * 250
        after = before + (to_number - from_number) * 180
        changes.append(
            (customer_id, "2026-03", f"tariff_{from_number}", f"tariff_{to_number}", before, after)
        )
    connection.executemany("INSERT INTO change_tariff VALUES (?, ?, ?, ?, ?, ?)", changes)

    features = [
        ("arpu_segment", "ARPU_3m_avg: LOW <1000, MID 1000-5000, HIGH >5000"),
        ("data_segment", "Monthly data: NON_USER 0, LITE 0-2000 MB, HEAVY >2000 MB"),
        ("call_segment", "Monthly calls: LOW <100, MEDIUM 100-400, HIGH >400 minutes"),
        ("predicted_arpu", "Estimated target-period monthly revenue"),
    ]
    connection.executemany("INSERT INTO feature_dictionary VALUES (?, ?)", features)
