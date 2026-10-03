"""Create the two devices the evaluation corpus assumes.

lamp and fan used to be hardcoded in rule_schema.DEVICES. They are registry
entries now, so a fresh install has neither and every corpus request fails to
compile. Seeding restores the baseline without special-casing the schema.

Run it with the web service up or down; it imports drishti_config rather than
Garuda_web, so it neither starts the pipeline nor claims a GPIO pin.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from basic_pipelines.garuda_auto.device_registry import DeviceRegistry  # noqa: E402

SEEDS = [
    {"id": "lamp", "name": "Lamp", "type": "light", "room": "study",
     "transport": {"kind": "relay", "channel": 1}},
    {"id": "fan", "name": "Fan", "type": "fan", "room": "study",
     "transport": {"kind": "relay", "channel": 2}},
]


def seed(registry):
    """Add any missing seed device. Returns the ids it created."""
    created = []
    for entry in SEEDS:
        if registry.get(entry["id"]) is not None:
            continue
        ok, reason = registry.add(dict(entry))
        if not ok:
            raise SystemExit(f"could not seed {entry['id']}: {reason}")
        created.append(entry["id"])
    return created


def main():
    from basic_pipelines.drishti_config import DATA_DIR, RELAY_CHANNELS
    registry = DeviceRegistry(str(Path(DATA_DIR) / "devices.json"),
                              relay_channels=RELAY_CHANNELS)
    created = seed(registry)
    print(f"seeded: {created}" if created else "nothing to seed")


if __name__ == "__main__":
    main()
