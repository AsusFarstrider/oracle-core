from __future__ import annotations

from functools import lru_cache
import io
from pathlib import Path
import shutil
import tempfile

from ruamel.yaml import YAML

from oracle_app.configuration import (
    BrainEffectiveRuntimeSettings,
    GenerationStore,
    HouseholdRuntimeSettings,
    inspect_candidate,
    load_effective_config,
)


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"


@lru_cache(maxsize=1)
def neutral_brain_runtime_settings() -> BrainEffectiveRuntimeSettings:
    """Build the reusable provider-free household context from committed input."""

    with tempfile.TemporaryDirectory() as temporary:
        temporary_root = Path(temporary)
        bundle = temporary_root / "bundle"
        shutil.copytree(EXAMPLE_ROOT, bundle)
        household_path = bundle / "household.yaml"
        yaml = YAML(typ="safe")
        household = yaml.load(household_path.read_text(encoding="utf-8"))
        household["rooms"] = [
            {
                "id": "living_room",
                "enabled": True,
                "display_name": "Living Room",
                "aliases": ["lounge"],
            },
            {
                "id": "bedroom",
                "enabled": True,
                "display_name": "Bedroom",
                "aliases": [],
            },
            {
                "id": "guest_room",
                "enabled": True,
                "display_name": "Guest's Room",
                "aliases": ["guest room"],
            },
            {
                "id": "dining_room",
                "enabled": True,
                "display_name": "Dining Room",
                "aliases": [],
            },
            {
                "id": "kitchen",
                "enabled": True,
                "display_name": "Kitchen",
                "aliases": [],
            },
        ]
        rendered = io.StringIO()
        yaml.dump(household, rendered)
        household_path.write_text(rendered.getvalue(), encoding="utf-8")
        store = GenerationStore(temporary_root / "store")
        store.initialize("example-home")
        config, secrets = store.install_candidate(inspect_candidate(bundle))
        activation = store.create_activation(config.generation_id, secrets.generation_id)
        store._replace_selected_pointer(  # noqa: SLF001 - immutable test fixture
            activation.generation_id,
            operation_id="selection_op_11111111111111111111111111111111",
            selection_revision=1,
            satellite_projection_activation_ids={},
        )
        return BrainEffectiveRuntimeSettings.from_effective_config(
            load_effective_config(store)
        )


def neutral_household_runtime_settings() -> HouseholdRuntimeSettings:
    return neutral_brain_runtime_settings().household
