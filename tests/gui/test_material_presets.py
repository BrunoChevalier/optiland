"""Exact, bounded view preferences cannot broaden missing catalog choices."""

from __future__ import annotations

import json
import math
from dataclasses import FrozenInstanceError, asdict, replace

import pytest

from optiland.materials.properties import MaterialKey, MaterialQuery
from optiland_gui.services.material_presets import (
    MaterialPresetStore,
    MaterialViewPreset,
)


class Settings:
    def __init__(self):
        self.data = {}

    def value(self, key, default):
        return self.data.get(key, default)

    def setValue(self, key, value):
        self.data[key] = value


def preset():
    return MaterialViewPreset(
        "partial-gf",
        MaterialQuery(name="Same name", catalog_keys=(("missing:local", "Catalog"),)),
        (
            (
                MaterialKey("missing:local", "Catalog", "same-name-2"),
                "Same name",
                "Local catalog",
            ),
        ),
        (90, 10, 0.4, 0.8),
        False,
    )


def test_round_trip_retains_unavailable_catalogs_exact_pin_labels_and_view():
    settings = Settings()
    store = MaterialPresetStore(settings)
    expected = preset()
    store.save("Saved α", expected)
    loaded = store.load("Saved α")
    assert loaded == expected
    assert loaded.query.catalog_keys == (("missing:local", "Catalog"),)
    assert loaded.pins[0][0].record_id == "same-name-2"
    assert loaded.bounds == (90, 10, 0.4, 0.8)
    assert not loaded.show_context
    assert set(settings.data) == {store.KEY}
    assert "owned_data" not in settings.data[store.KEY]
    store.save("Saved α", replace(expected, query=MaterialQuery(catalog_keys=())))
    assert store.load("Saved α").query.catalog_keys == ()


def test_preset_copies_mutable_input_containers_before_storage():
    pins = [list(preset().pins[0])]
    bounds = [90, 10, 0.4, 0.8]
    value = replace(preset(), pins=pins, bounds=bounds)
    pins[0][1] = "mutated"
    bounds[0] = 1
    assert value.pins == preset().pins and value.bounds == preset().bounds
    with pytest.raises(FrozenInstanceError):
        value.view = "nd-vd"


@pytest.mark.parametrize(
    "field,value",
    [
        ("view", "wrong"),
        ("query", MaterialQuery()),
        (
            "query",
            MaterialQuery(catalog_keys=tuple((str(i), "Catalog") for i in range(257))),
        ),
        ("pins", ((),)),
        ("pins", (1,)),
        ("pins", (("wrong key", "label", "source"),)),
        ("pins", ((MaterialKey("s", "c", "r"), "x" * 4097, "source"),)),
        ("pins", preset().pins * 2),
        (
            "pins",
            tuple((MaterialKey("s", "c", str(i)), "label", "source") for i in range(5)),
        ),
        ("bounds", (1, 1, 2, 3)),
        ("bounds", (1, 2, math.nan, 3)),
        ("bounds", (True, 2, 3, 4)),
        ("bounds", (1, 2, 3)),
        ("bounds", None),
        ("show_context", 1),
    ],
)
def test_invalid_preset_cannot_be_constructed(field, value):
    with pytest.raises(ValueError):
        replace(preset(), **{field: value})


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "{",
        "[]",
        '{"": {}}',
        '{"x": NaN}',
        '{"x": {"query": {}}}',
        '{"x": {"view":"nd-vd", "query":{}, "pins":[1],'
        ' "bounds":[], "show_context":true}}',
    ],
)
def test_corrupt_stored_views_fail_without_broadening_or_overwriting(raw):
    settings = Settings()
    store = MaterialPresetStore(settings)
    settings.data[store.KEY] = raw
    with pytest.raises(ValueError):
        store.load("x")
    assert settings.data[store.KEY] == raw


def test_store_enforces_names_count_byte_limit_and_atomic_failure():
    settings = Settings()
    store = MaterialPresetStore(settings)
    for i in range(32):
        store.save(f"view-{i:02}", preset())
    assert store.names() == tuple(f"view-{i:02}" for i in range(32))
    old = settings.data[store.KEY]
    for name in ("", " ", "x" * 129, None, "33rd"):
        with pytest.raises(ValueError):
            store.save(name, preset())
    assert settings.data[store.KEY] == old
    store.remove("view-00")
    store.save("replacement", preset())
    assert "replacement" in store.names()
    with pytest.raises(ValueError):
        store.remove("absent")
    with pytest.raises(ValueError):
        store.load("absent")
    with pytest.raises(ValueError):
        store.save("bad type", object())

    small = MaterialPresetStore(Settings())
    small.save("ordinary", preset())
    previous = small.settings.data[small.KEY]
    huge = replace(
        preset(), query=MaterialQuery(name="é" * small.MAX_BYTES, catalog_keys=())
    )
    with pytest.raises(ValueError, match="limit"):
        small.save("too large", huge)
    assert small.settings.data[small.KEY] == previous
    small.settings.data[small.KEY] = "é" * small.MAX_BYTES
    with pytest.raises(ValueError, match="limit"):
        small.names()


def test_oversized_or_recursively_corrupt_saved_container_is_rejected():
    settings = Settings()
    store = MaterialPresetStore(settings)
    settings.data[store.KEY] = json.dumps({str(i): asdict(preset()) for i in range(33)})
    with pytest.raises(ValueError, match="32"):
        store.names()
    settings.data[store.KEY] = "[" * 2000 + "]" * 2000
    with pytest.raises(ValueError, match="Saved material views"):
        store.names()
