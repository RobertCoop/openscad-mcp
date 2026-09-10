"""Tests for the purchased-parts catalog.

Two layers:

* Schema, lookup and reference-integration tests, which are pure Python and
  always run.
* Geometric self-checks, which shell out to OpenSCAD and are marked ``slow``.
  They skip cleanly when OpenSCAD is not installed, so the suite still passes
  on a machine without it.
"""

from __future__ import annotations

import re
import shutil

import pytest

from openscad_mcp import parts_catalog
from openscad_mcp.parts_catalog import (
    PARTS,
    list_parts,
    lookup_part,
    part_scad_path,
    part_scad_source,
    reference_entries,
    self_check,
)
from openscad_mcp.reference import (
    CONFIDENCE_LEVELS,
    TOPICS,
    fit_class,
    fit_for_diameter,
    list_topics,
    lookup,
)

pytestmark = pytest.mark.unit

PART_IDS = [part["id"] for part in PARTS]

OPENSCAD = shutil.which("openscad")
needs_openscad = pytest.mark.skipif(OPENSCAD is None, reason="OpenSCAD is not installed")


def _strip_comments(source: str) -> str:
    """Drop // line comments so prose about a construct is not mistaken for it."""
    return "\n".join(line.split("//", 1)[0] for line in source.splitlines())


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestCatalogSchema:
    """Every entry carries the fields the catalog promises."""

    def test_five_parts_with_unique_ids(self):
        assert len(PARTS) == 5
        assert len(set(PART_IDS)) == 5

    @pytest.mark.parametrize("part", PARTS, ids=PART_IDS)
    def test_required_fields_present(self, part):
        required = {
            "id",
            "name",
            "aliases",
            "category",
            "scad_file",
            "envelope_mm",
            "body",
            "mount",
            "interface",
            "mass_g",
            "electrical",
            "modules",
            "anchors",
            "confidence",
            "sources",
            "license_note",
            "verify",
        }
        assert required <= set(part)

    @pytest.mark.parametrize("part", PARTS, ids=PART_IDS)
    def test_envelope_is_three_positive_numbers(self, part):
        envelope = part["envelope_mm"]
        assert len(envelope) == 3
        assert all(isinstance(v, int | float) and v > 0 for v in envelope)

    @pytest.mark.parametrize("part", PARTS, ids=PART_IDS)
    def test_confidence_is_a_known_label(self, part):
        assert part["confidence"] in CONFIDENCE_LEVELS

    @pytest.mark.parametrize("part", PARTS, ids=PART_IDS)
    def test_verify_list_is_non_optional_and_non_empty(self, part):
        """The whole point of the data policy: unsourced numbers are declared."""
        assert isinstance(part["verify"], list)
        assert part["verify"], f"{part['id']} must declare what is unverified"
        assert all(isinstance(item, str) and item.strip() for item in part["verify"])

    @pytest.mark.parametrize("part", PARTS, ids=PART_IDS)
    def test_every_source_carries_a_url(self, part):
        assert part["sources"], f"{part['id']} has no sources"
        for source in part["sources"]:
            assert "http" in source, f"{part['id']} source without a URL: {source}"

    @pytest.mark.parametrize("part", PARTS, ids=PART_IDS)
    def test_license_note_mentions_mit_and_facts(self, part):
        note = part["license_note"].lower()
        assert "mit" in note
        assert "not copyrightable" in note

    @pytest.mark.parametrize("part", PARTS, ids=PART_IDS)
    def test_mount_block_shape(self, part):
        mount = part["mount"]
        assert {
            "pattern",
            "hole_spacing_mm",
            "hole_dia_mm",
            "screw",
            "thickness_mm",
            "plane",
        } <= set(mount)
        assert isinstance(mount["pattern"], str) and mount["pattern"]

    @pytest.mark.parametrize("part", PARTS, ids=PART_IDS)
    def test_module_names_are_consistent(self, part):
        modules = part["modules"]
        assert set(modules) == {"solid", "mask", "mount_holes_mask", "info"}
        solid = modules["solid"]
        assert solid.startswith("part_")
        assert modules["mask"] == f"{solid}_mask"
        assert modules["mount_holes_mask"] == f"{solid}_mount_holes_mask"
        assert modules["info"] == f"{solid}_info"

    @pytest.mark.parametrize("part", PARTS, ids=PART_IDS)
    def test_anchors_are_named_and_well_formed(self, part):
        anchors = part["anchors"]
        assert anchors, f"{part['id']} has no named anchors"
        for name, anchor in anchors.items():
            assert re.fullmatch(r"[a-z0-9-]+", name), f"bad anchor name {name!r}"
            assert len(anchor["pos_mm"]) == 3
            assert all(isinstance(v, int | float) for v in anchor["pos_mm"])
            assert anchor["dir"] in {"UP", "DOWN", "LEFT", "RIGHT", "FWD", "BACK"}
            assert anchor["note"].strip()

    @pytest.mark.parametrize("part", PARTS, ids=PART_IDS)
    def test_has_a_mount_plane_anchor(self, part):
        """Every purchased part has a face you bolt it down by."""
        assert "mount-plane" in part["anchors"]


# ---------------------------------------------------------------------------
# The generated .scad files
# ---------------------------------------------------------------------------


class TestScadFiles:
    @pytest.mark.parametrize("part", PARTS, ids=PART_IDS)
    def test_file_exists_and_is_named_after_the_part(self, part):
        path = part_scad_path(part["id"])
        assert path.is_file()
        assert path.name == part["scad_file"]

    @pytest.mark.parametrize("part", PARTS, ids=PART_IDS)
    def test_declares_all_four_modules(self, part):
        source = part_scad_source(part["id"])
        modules = part["modules"]
        for name in (modules["solid"], modules["mask"], modules["mount_holes_mask"]):
            assert f"module {name}(" in source, f"{part['id']} is missing module {name}"
        assert f"function {modules['info']}()" in source

    @pytest.mark.parametrize("part", PARTS, ids=PART_IDS)
    def test_includes_bosl2_and_uses_attachable(self, part):
        source = part_scad_source(part["id"])
        assert "include <BOSL2/std.scad>" in source
        assert "attachable(" in source
        assert "named_anchor(" in source

    @pytest.mark.parametrize("part", PARTS, ids=PART_IDS)
    def test_declares_every_catalogued_anchor(self, part):
        source = part_scad_source(part["id"])
        for name in part["anchors"]:
            assert f'named_anchor("{name}"' in source, f"{part['id']} lacks anchor {name}"

    @pytest.mark.parametrize("part", PARTS, ids=PART_IDS)
    def test_never_uses_tag_or_diff(self, part):
        """tag()/diff() silently union across a use<> boundary instead of cutting.

        Checked against the code with comments stripped, since the files warn
        about tag() and diff() in prose.
        """
        code = _strip_comments(part_scad_source(part["id"]))
        assert not re.search(r"\btag\s*\(", code), f"{part['id']} uses tag()"
        assert not re.search(r"\bdiff\s*\(", code), f"{part['id']} uses diff()"

    @pytest.mark.parametrize("part", PARTS, ids=PART_IDS)
    def test_pins_fn_on_round_features(self, part):
        source = part_scad_source(part["id"])
        assert "$fn" in source, f"{part['id']} does not pin $fn"

    @pytest.mark.parametrize("part", PARTS, ids=PART_IDS)
    def test_mask_takes_the_documented_signature(self, part):
        source = part_scad_source(part["id"])
        match = re.search(
            rf"module {re.escape(part['modules']['mask'])}\((.*?)\)\s*\{{", source, re.DOTALL
        )
        assert match, "mask module signature not found"
        signature = match.group(1)
        for argument in ("clr", "bore_clr", "install", "install_len"):
            assert f"{argument} =" in signature, f"mask lacks {argument}"

    @pytest.mark.parametrize("part", PARTS, ids=PART_IDS)
    def test_install_sweep_is_a_minkowski_prism_not_a_hull(self, part):
        """hull() of two poses fills in concavities; a minkowski prism does not."""
        source = part_scad_source(part["id"])
        assert "minkowski()" in source

    @pytest.mark.parametrize("part", PARTS, ids=PART_IDS)
    def test_cites_its_sources_in_the_header(self, part):
        header = part_scad_source(part["id"])[:4000]
        assert "http" in header, f"{part['id']} .scad header cites no source"

    def test_data_policy_readme_ships_with_the_parts(self):
        readme = parts_catalog.PARTS_DIR / "README.md"
        assert readme.is_file()
        text = readme.read_text(encoding="utf-8").lower()
        assert "verify" in text
        assert "mit" in text


# ---------------------------------------------------------------------------
# Lookup API
# ---------------------------------------------------------------------------


class TestListParts:
    def test_returns_one_brief_per_part(self):
        listed = list_parts()
        assert len(listed) == len(PARTS)
        assert [entry["id"] for entry in listed] == PART_IDS

    def test_brief_has_exactly_the_documented_keys(self):
        for entry in list_parts():
            assert set(entry) == {"id", "name", "category", "envelope_mm"}

    def test_is_a_copy_not_the_live_data(self):
        listed = list_parts()
        listed[0]["envelope_mm"][0] = 999.0
        assert PARTS[0]["envelope_mm"][0] != 999.0


class TestLookupPart:
    @pytest.mark.parametrize("part_id", PART_IDS)
    def test_exact_id(self, part_id):
        assert lookup_part(part_id)["id"] == part_id

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("28byj48", "28byj-48"),
            ("blue stepper", "28byj-48"),
            ("uln2003 stepper", "28byj-48"),
            ("17HS4401", "nema17"),
            ("NEMA 17", "nema17"),
            ("nema-17", "nema17"),
            ("lazy susan", "lazy-susan-4in"),
            ("turntable bearing", "lazy-susan-4in"),
            ("kw12-3", "kw11-3z"),
            ("SS-5GL", "kw11-3z"),
            ("tcrt5000", "tcrt5000-module"),
            ("ir line sensor", "tcrt5000-module"),
        ],
    )
    def test_alias_lookup(self, query, expected):
        assert lookup_part(query)["id"] == expected

    @pytest.mark.parametrize(
        ("query", "expected"),
        [("lazy", "lazy-susan-4in"), ("tcrt", "tcrt5000-module"), ("byj", "28byj-48")],
    )
    def test_substring_lookup(self, query, expected):
        assert lookup_part(query)["id"] == expected

    def test_punctuation_and_case_are_ignored(self):
        assert lookup_part("  NeMa_17  ")["id"] == "nema17"

    @pytest.mark.parametrize("query", ["", "   ", "flux capacitor", "m3 screw"])
    def test_unknown_query_returns_none(self, query):
        assert lookup_part(query) is None

    def test_exact_id_beats_substring(self):
        """'nema17' is also a substring of the NEMA name, but the id match wins."""
        assert lookup_part("nema17")["id"] == "nema17"

    def test_detailed_keeps_the_long_fields(self):
        detailed = lookup_part("nema17", detailed=True)
        assert "body" in detailed
        assert "anchors" in detailed
        assert "sources" in detailed
        assert "electrical" in detailed

    def test_compact_drops_the_prose_but_keeps_verify(self):
        compact = lookup_part("nema17")
        assert "body" not in compact
        assert "electrical" not in compact
        assert "sources" not in compact
        assert compact["verify"], "verify must survive the compact view"
        assert compact["confidence"]
        assert compact["license_note"]

    def test_compact_keeps_the_structured_design_blocks(self):
        """A caller placing the part needs mount, interface and anchor positions."""
        compact = lookup_part("nema17")
        assert compact["mount"]["hole_spacing_mm"] == 31.0
        assert compact["interface"]["shaft_dia_mm"] == 5.0
        assert compact["envelope_mm"] == [42.3, 42.3, 64.0]

    def test_compact_anchors_collapse_to_positions(self):
        compact = lookup_part("nema17")
        assert compact["anchors"]["shaft-tip"] == [0.0, 0.0, 32.0]
        assert all(len(pos) == 3 for pos in compact["anchors"].values())

    def test_detailed_anchors_keep_direction_and_note(self):
        detailed = lookup_part("nema17", detailed=True)
        anchor = detailed["anchors"]["shaft-tip"]
        assert anchor["dir"] == "UP"
        assert anchor["note"]

    def test_result_is_a_copy(self):
        result = lookup_part("nema17", detailed=True)
        result["verify"].append("mutated")
        assert "mutated" not in PARTS[1]["verify"]


class TestScadAccessors:
    def test_unknown_part_raises_keyerror_naming_the_alternatives(self):
        with pytest.raises(KeyError) as excinfo:
            part_scad_path("no-such-part")
        assert "nema17" in str(excinfo.value)

    def test_source_matches_the_file(self):
        path = part_scad_path("nema17")
        assert part_scad_source("nema17") == path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Specific numbers that must not silently drift
# ---------------------------------------------------------------------------


class TestSourcedNumbers:
    def test_nema17_frame_matches_the_nema_standard(self):
        part = lookup_part("nema17", detailed=True)
        assert part["envelope_mm"][0] == 42.3
        assert part["mount"]["hole_spacing_mm"] == 31.0
        assert part["interface"]["boss_dia_mm"] == 22.0
        assert part["interface"]["shaft_dia_mm"] == 5.0

    def test_nema17_records_that_42_3_and_m3_are_not_nema_numbers(self):
        verify = " ".join(lookup_part("nema17")["verify"]).lower()
        assert "42.3 mm is not a nema number" in verify
        assert "4-40" in verify

    def test_byj48_hole_spacing_is_the_drawing_value(self):
        part = lookup_part("28byj-48", detailed=True)
        assert part["mount"]["hole_spacing_mm"] == 35.0
        assert part["mount"]["hole_dia_mm"] == 4.2
        assert part["interface"]["shaft_offset_mm"] == 8.0

    def test_lazy_susan_is_a_true_four_inch_not_100mm(self):
        part = lookup_part("lazy-susan-4in", detailed=True)
        assert part["envelope_mm"][0] == pytest.approx(101.6)

    def test_microswitch_size_correction_is_recorded(self):
        """It is widely mislisted as the larger 28.5 mm V-15 family."""
        part = lookup_part("kw11-3z", detailed=True)
        assert part["envelope_mm"] == [20.0, 6.4, 10.7]
        assert part["mount"]["hole_spacing_mm"] == 9.5
        assert any("28" in item for item in part["verify"])

    def test_tcrt5000_uses_the_vishay_range_not_vendor_marketing(self):
        part = lookup_part("tcrt5000-module", detailed=True)
        assert part["interface"]["peak_distance_mm"] == 2.5
        assert part["interface"]["operating_range_mm"] == [0.2, 15.0]
        assert part["interface"]["sensor_package_mm"] == [10.2, 5.8, 7.0]

    def test_tcrt5000_flags_the_unsourced_placements(self):
        verify = " ".join(lookup_part("tcrt5000-module")["verify"]).lower()
        assert "position" in verify
        assert "not sourced" in verify


# ---------------------------------------------------------------------------
# reference.py integration
# ---------------------------------------------------------------------------


class TestReferenceIntegration:
    def test_parts_is_a_registered_topic(self):
        assert "parts" in TOPICS
        assert "parts" in {topic["topic"] for topic in list_topics()}

    def test_topic_summary_is_non_empty(self):
        summary = next(t["summary"] for t in list_topics() if t["topic"] == "parts")
        assert len(summary) > 40

    def test_lookup_parts_returns_every_entry(self):
        result = lookup("parts")
        assert len(result["entries"]) == len(PARTS)
        assert result["notes"]
        assert result["sources"]

    @pytest.mark.parametrize(
        ("query", "expected_id"),
        [("nema", "nema17"), ("lazy susan", "lazy-susan-4in"), ("tcrt5000", "tcrt5000-module")],
    )
    def test_lookup_parts_filters_by_query(self, query, expected_id):
        entries = lookup("parts", query)["entries"]
        assert expected_id in {entry["id"] for entry in entries}

    def test_lookup_parts_entries_keep_confidence_and_source(self):
        for entry in lookup("parts")["entries"]:
            assert entry["confidence"] in CONFIDENCE_LEVELS
            assert "http" in entry["source"]

    def test_lookup_parts_carries_the_verify_list_through(self):
        for entry in lookup("parts")["entries"]:
            assert entry["verify"]

    def test_reference_entries_shape(self):
        for entry in reference_entries():
            assert {"name", "id", "keywords", "confidence", "source", "note"} <= set(entry)
            assert isinstance(entry["anchors"], list)

    def test_compact_lookup_drops_notes_but_keeps_source(self):
        entry = lookup("parts", "nema17")["entries"][0]
        assert "note" not in entry
        assert "source" in entry


# ---------------------------------------------------------------------------
# fit_for_diameter / fit_class
# ---------------------------------------------------------------------------


class TestFitForDiameter:
    def test_returns_exactly_three_candidates(self):
        assert len(fit_for_diameter(3.3)) == 3

    def test_3_3_finds_the_m4_tap_drill_with_zero_delta(self):
        matches = fit_for_diameter(3.3)
        tap = [m for m in matches if m["fastener"] == "M4" and m["role"] == "tap drill"]
        assert tap, "M4 tap drill should be among the top three for 3.3 mm"
        assert tap[0]["delta_mm"] == 0.0
        assert tap[0]["value_mm"] == 3.3

    def test_3_3_also_offers_m3_clearance_readings(self):
        """3.3 is a tenth off both M3 clearance grades; never present one winner."""
        matches = fit_for_diameter(3.3)
        m3_clearance = [
            m for m in matches if m["fastener"] == "M3" and m["role"].startswith("clearance")
        ]
        assert len(m3_clearance) == 2
        assert {m["delta_mm"] for m in m3_clearance} == {0.1, -0.1}

    def test_sorted_by_absolute_delta(self):
        deltas = [abs(m["delta_mm"]) for m in fit_for_diameter(4.6)]
        assert deltas == sorted(deltas)

    def test_delta_sign_means_your_hole_is_bigger(self):
        match = next(m for m in fit_for_diameter(3.5) if m["value_mm"] == 3.4)
        assert match["delta_mm"] == pytest.approx(0.1)

    def test_4_3_is_both_an_m4_clearance_and_an_m2_counterbore(self):
        """Two different fasteners land on 4.3 exactly; both must be offered."""
        roles = {(m["fastener"], m["role"]) for m in fit_for_diameter(4.3)}
        assert ("M4", "clearance hole, close") in roles
        assert ("M2", "counterbore") in roles

    def test_counterbores_are_in_the_candidate_pool(self):
        roles = {m["role"] for m in fit_for_diameter(6.0)}
        assert "counterbore" in roles

    def test_every_candidate_carries_confidence_and_source(self):
        for match in fit_for_diameter(2.5):
            assert match["confidence"] in CONFIDENCE_LEVELS
            assert "http" in match["source"]
            assert match["meaning"]

    @pytest.mark.parametrize("bad", [0, -1, -0.5])
    def test_non_positive_diameter_rejected(self, bad):
        with pytest.raises(ValueError, match="positive"):
            fit_for_diameter(bad)

    def test_non_numeric_rejected(self):
        with pytest.raises(ValueError):
            fit_for_diameter("wide")


class TestFitClass:
    def test_8_0_in_8_2_is_a_running_fit(self):
        result = fit_class(8.0, 8.2)
        assert result["diametral_mm"] == pytest.approx(0.2)
        assert result["per_side_mm"] == pytest.approx(0.1)
        assert "running" in result["fit"]
        assert result["in_table"] is True

    def test_overlapping_bands_are_reported_as_alternatives(self):
        result = fit_class(8.0, 8.2)
        assert result["alternatives"], "0.2 mm sits in several bands; say so"
        assert result["fit"] not in result["alternatives"]

    def test_zero_clearance_is_a_press_fit_and_not_interference(self):
        result = fit_class(6.0, 6.0)
        assert result["diametral_mm"] == 0.0
        assert result["fit"] == "press fit"
        assert result["interference"] is False

    def test_negative_clearance_is_flagged_as_interference(self):
        result = fit_class(6.0, 5.95)
        assert result["diametral_mm"] < 0
        assert result["interference"] is True

    def test_loose_pair_is_a_free_or_sliding_fit(self):
        result = fit_class(8.0, 8.5)
        assert result["in_table"] is True
        assert result["fit"] in {"free running fit (loose)", "sliding lid / drawer fit"}

    def test_off_the_table_says_so_rather_than_pretending(self):
        result = fit_class(8.0, 20.0)
        assert result["in_table"] is False
        assert result["alternatives"] == []
        assert "outside every band" in result["note"]

    def test_heavy_interference_says_so(self):
        result = fit_class(8.0, 7.0)
        assert result["in_table"] is False
        assert result["interference"] is True
        assert "press" in result["note"]

    def test_result_carries_confidence_and_source(self):
        result = fit_class(8.0, 8.2)
        assert result["confidence"] in CONFIDENCE_LEVELS
        assert result["source"]

    def test_fit_name_comes_from_the_fits_table(self):
        names = {entry["name"] for entry in lookup("fits")["entries"]}
        assert fit_class(8.0, 8.2)["fit"] in names

    def test_non_numeric_rejected(self):
        with pytest.raises(ValueError):
            fit_class("eight", 8.2)


# ---------------------------------------------------------------------------
# Geometric self-check, against the real OpenSCAD binary
# ---------------------------------------------------------------------------


class TestSelfCheckHarness:
    def test_skips_cleanly_when_openscad_is_missing(self):
        result = self_check("nema17", openscad="definitely-not-a-real-binary")
        assert result["available"] is False
        assert result["ok"] is None
        assert result["checks"] == []
        assert "skipped" in result

    def test_unknown_part_raises(self):
        with pytest.raises(KeyError):
            self_check("no-such-part")


@needs_openscad
@pytest.mark.slow
@pytest.mark.render
class TestSelfCheckGeometry:
    """The model must agree with its own catalog entry, checked in OpenSCAD."""

    @pytest.fixture(scope="class")
    def results(self):
        return {part_id: self_check(part_id, openscad=OPENSCAD) for part_id in PART_IDS}

    @pytest.mark.parametrize("part_id", PART_IDS)
    def test_all_checks_pass(self, results, part_id):
        result = results[part_id]
        assert result["available"] is True
        assert result["ok"] is True, f"{part_id} failed: {result['failed']}"

    @pytest.mark.parametrize("part_id", PART_IDS)
    def test_envelope_matches_the_declared_bounding_box(self, results, part_id):
        check = next(c for c in results[part_id]["checks"] if c["check"] == "envelope")
        assert check["ok"], check
        assert all(abs(d) <= 0.05 for d in check["delta_mm"])

    @pytest.mark.parametrize("part_id", PART_IDS)
    def test_every_named_anchor_lands_where_the_entry_says(self, results, part_id):
        anchor_checks = [c for c in results[part_id]["checks"] if c["check"].startswith("anchor:")]
        assert len(anchor_checks) == len(lookup_part(part_id, detailed=True)["anchors"])
        for check in anchor_checks:
            assert check["ok"], check

    @pytest.mark.parametrize("part_id", PART_IDS)
    def test_mask_contains_the_part_at_zero_clearance(self, results, part_id):
        check = next(c for c in results[part_id]["checks"] if c["check"] == "containment")
        assert check["ok"], check["detail"]
