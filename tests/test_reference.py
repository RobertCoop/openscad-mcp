"""Tests for the static engineering reference data module."""

from __future__ import annotations

import json

import pytest

from openscad_mcp import reference
from openscad_mcp.reference import (
    CONFIDENCE_LEVELS,
    TOPICS,
    cheatsheet,
    conventions_brief,
    list_topics,
    lookup,
)

pytestmark = pytest.mark.unit

# Hard limits the module promises to callers.
MAX_CONVENTIONS_BRIEF_CHARS = 1400
MAX_CHEATSHEET_CHARS = 2500


# ---------------------------------------------------------------------------
# list_topics
# ---------------------------------------------------------------------------


class TestListTopics:
    def test_returns_every_topic_in_declared_order(self):
        topics = list_topics()
        assert [item["topic"] for item in topics] == TOPICS

    def test_every_topic_has_a_non_empty_summary(self):
        for item in list_topics():
            assert item.keys() == {"topic", "summary"}
            assert item["summary"].strip(), f"{item['topic']} has an empty summary"

    def test_topics_list_has_no_duplicates(self):
        assert len(TOPICS) == len(set(TOPICS))

    def test_is_json_serialisable(self):
        json.dumps(list_topics())


# ---------------------------------------------------------------------------
# Every topic loads and every entry is well-formed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("topic", TOPICS)
class TestEveryTopicLoads:
    def test_topic_loads_and_has_entries(self, topic):
        result = lookup(topic)
        assert result["topic"] == topic
        assert result["query"] is None
        assert result["entries"], f"{topic} returned no entries"

    def test_result_has_the_documented_shape(self, topic):
        result = lookup(topic)
        assert result.keys() == {"topic", "query", "entries", "notes", "sources"}
        assert isinstance(result["entries"], list)
        assert isinstance(result["notes"], list)
        assert isinstance(result["sources"], list)

    def test_topic_has_notes_and_aggregated_sources(self, topic):
        result = lookup(topic)
        assert result["notes"], f"{topic} has no notes"
        assert all(note.strip() for note in result["notes"])
        assert result["sources"], f"{topic} aggregated no sources"
        assert len(result["sources"]) == len(set(result["sources"])), "sources not deduplicated"

    def test_every_entry_has_a_valid_confidence_label(self, topic):
        for entry in lookup(topic, detailed=True)["entries"]:
            assert entry["confidence"] in CONFIDENCE_LEVELS, (
                f"{topic}/{entry['name']} has confidence {entry['confidence']!r}, "
                f"expected one of {CONFIDENCE_LEVELS}"
            )

    def test_every_entry_has_a_non_empty_source(self, topic):
        for entry in lookup(topic, detailed=True)["entries"]:
            source = entry.get("source")
            assert isinstance(source, str), f"{topic}/{entry['name']} source is not a string"
            assert source.strip(), f"{topic}/{entry['name']} has an empty source"

    def test_every_entry_has_a_non_empty_name_and_note(self, topic):
        for entry in lookup(topic, detailed=True)["entries"]:
            assert entry["name"].strip()
            assert entry["note"].strip(), f"{topic}/{entry['name']} has an empty note"

    def test_entry_names_are_unique_within_a_topic(self, topic):
        names = [entry["name"] for entry in lookup(topic)["entries"]]
        assert len(names) == len(set(names)), f"{topic} has duplicate entry names"

    def test_lookup_output_is_json_serialisable(self, topic):
        json.dumps(lookup(topic))
        json.dumps(lookup(topic, detailed=True))

    def test_calibrate_entries_say_so_in_the_note(self, topic):
        """A "calibrate" entry must tell the reader to print a test coupon."""
        for entry in lookup(topic, detailed=True)["entries"]:
            if entry["confidence"] != "calibrate":
                continue
            note = entry["note"].lower()
            assert any(
                phrase in note
                for phrase in ("test coupon", "calibrat", "tune", "measure", "before you")
            ), f"{topic}/{entry['name']} is 'calibrate' but its note does not say to test"


# ---------------------------------------------------------------------------
# Unknown topics
# ---------------------------------------------------------------------------


class TestUnknownTopic:
    def test_unknown_topic_raises_value_error(self):
        with pytest.raises(ValueError):
            lookup("bogus")

    def test_error_message_lists_the_available_topics(self):
        with pytest.raises(ValueError) as excinfo:
            lookup("bogus")
        message = str(excinfo.value)
        for topic in TOPICS:
            assert topic in message

    def test_empty_topic_raises_value_error(self):
        with pytest.raises(ValueError):
            lookup("")

    def test_topic_is_case_insensitive_and_trimmed(self):
        assert lookup("  FITS  ")["topic"] == "fits"
        assert lookup("Fasteners")["topic"] == "fasteners"


# ---------------------------------------------------------------------------
# Querying
# ---------------------------------------------------------------------------


class TestQuery:
    def test_m3_returns_exactly_the_m3_fastener_row(self):
        result = lookup("fasteners", "M3")
        assert [entry["name"] for entry in result["entries"]] == ["M3"]

    def test_m3_clearance_holes_match_iso_273(self):
        (m3,) = lookup("fasteners", "M3")["entries"]
        assert m3["clearance_hole_close_mm"] == 3.2
        assert m3["clearance_hole_medium_mm"] == 3.4
        assert m3["clearance_hole_free_mm"] == 3.6

    def test_m3_other_standard_dimensions(self):
        (m3,) = lookup("fasteners", "M3")["entries"]
        assert m3["nominal_diameter_mm"] == 3.0
        assert m3["thread_pitch_coarse_mm"] == 0.5
        assert m3["tap_drill_mm"] == 2.5
        # ISO 4762 socket head cap screw.
        assert m3["shcs_head_diameter_mm"] == 5.5
        assert m3["shcs_head_height_mm"] == 3.0
        # ISO 4032 hex nut.
        assert m3["hex_nut_width_across_flats_mm"] == 5.5
        assert m3["hex_nut_height_mm"] == 2.4

    def test_query_is_case_insensitive(self):
        upper = lookup("fasteners", "M3")["entries"]
        lower = lookup("fasteners", "m3")["entries"]
        assert [entry["name"] for entry in upper] == [entry["name"] for entry in lower] == ["M3"]

    def test_query_is_echoed_back_unchanged(self):
        assert lookup("fits", "press")["query"] == "press"

    def test_bearing_designation_query(self):
        result = lookup("bearings", "608")
        assert [entry["name"] for entry in result["entries"]] == ["608"]
        (bearing,) = result["entries"]
        assert (bearing["bore_mm"], bearing["outer_diameter_mm"], bearing["width_mm"]) == (
            8.0,
            22.0,
            7.0,
        )

    def test_press_query_matches_the_press_fit_entry(self):
        names = [entry["name"] for entry in lookup("fits", "press")["entries"]]
        assert "press fit" in names

    def test_keyword_query_matches_beyond_the_name(self):
        """ "slip" is a keyword on the close running fit, not part of its name."""
        names = [entry["name"] for entry in lookup("fits", "slip")["entries"]]
        assert "close running fit (slip)" in names

    def test_unmatched_query_returns_no_entries_but_keeps_context(self):
        result = lookup("fits", "zzzz-no-such-thing")
        assert result["entries"] == []
        assert result["sources"] == []
        assert result["notes"], "notes should still be returned for an unmatched query"

    def test_blank_query_is_treated_as_no_filter(self):
        assert len(lookup("fits", "   ")["entries"]) == len(lookup("fits")["entries"])

    def test_query_narrows_the_aggregated_sources(self):
        filtered = lookup("bearings", "608")
        everything = lookup("bearings")
        assert len(filtered["sources"]) <= len(everything["sources"])


# ---------------------------------------------------------------------------
# detailed flag
# ---------------------------------------------------------------------------


class TestDetailedFlag:
    def test_compact_is_the_default(self):
        assert lookup("fits")["entries"] == lookup("fits", detailed=False)["entries"]

    def test_compact_entries_drop_the_verbose_fields(self):
        for entry in lookup("dfm")["entries"]:
            assert "note" not in entry
            assert "keywords" not in entry

    def test_compact_entries_keep_confidence_and_source(self):
        for entry in lookup("dfm")["entries"]:
            assert entry["confidence"] in CONFIDENCE_LEVELS
            assert entry["source"].strip()

    def test_detailed_entries_carry_notes_and_keywords(self):
        for entry in lookup("dfm", detailed=True)["entries"]:
            assert entry["note"].strip()
            assert entry["keywords"]

    def test_detailed_is_a_superset_of_compact(self):
        compact = {entry["name"]: entry for entry in lookup("joints")["entries"]}
        detailed = {entry["name"]: entry for entry in lookup("joints", detailed=True)["entries"]}
        assert compact.keys() == detailed.keys()
        for name, entry in compact.items():
            assert entry.items() <= detailed[name].items()

    def test_callers_cannot_mutate_the_module_data(self):
        """The returned entries must be copies, not the module's own dicts."""
        first = lookup("materials", detailed=True)["entries"][0]
        original = first["name"]
        first["name"] = "MUTATED"
        first.setdefault("keywords", []).append("MUTATED")
        again = lookup("materials", detailed=True)["entries"][0]
        assert again["name"] == original
        assert "MUTATED" not in again["keywords"]

    def test_callers_cannot_mutate_the_notes(self):
        result = lookup("fits")
        result["notes"].append("MUTATED")
        assert "MUTATED" not in lookup("fits")["notes"]


# ---------------------------------------------------------------------------
# conventions_brief and cheatsheet
# ---------------------------------------------------------------------------


class TestConventionsBrief:
    def test_is_within_the_token_budget(self):
        assert len(conventions_brief()) <= MAX_CONVENTIONS_BRIEF_CHARS

    def test_is_non_empty_plain_text(self):
        brief = conventions_brief()
        assert isinstance(brief, str)
        assert brief.strip()

    @pytest.mark.parametrize(
        "phrase",
        [
            "millimetres",
            "Z is up",
            "right-handed",
            "bottom-centre",
            "0.01",
            "$slop",
            "$fn",
            "BOSL2",
            "echo(",
        ],
    )
    def test_covers_the_key_conventions(self, phrase):
        assert phrase in conventions_brief()

    def test_is_stable_across_calls(self):
        assert conventions_brief() == conventions_brief()


class TestCheatsheet:
    def test_is_within_the_size_budget(self):
        assert len(cheatsheet()) <= MAX_CHEATSHEET_CHARS

    def test_is_non_empty_plain_text(self):
        sheet = cheatsheet()
        assert isinstance(sheet, str)
        assert sheet.strip()

    @pytest.mark.parametrize(
        "phrase",
        [
            "compile-time",
            "difference()",
            "intersection_for()",
            "center",
            "hull()",
            "minkowski()",
            "linear_extrude",
            "rotate_extrude",
            "offset(",
            "projection(",
            "mirror",
            "assert(",
            "echo()",
            "let(",
            "each",
            "is_undef",
            "$fn",
            "$fa",
            "$fs",
            "convexity",
            "font",
            "2D and 3D",
        ],
    )
    def test_covers_the_common_gotchas(self, phrase):
        assert phrase in cheatsheet()

    def test_is_stable_across_calls(self):
        assert cheatsheet() == cheatsheet()


# ---------------------------------------------------------------------------
# Topic content spot checks
# ---------------------------------------------------------------------------


class TestFitsContent:
    def test_every_fit_has_both_per_side_and_diametral_clearance(self):
        for entry in lookup("fits", detailed=True)["entries"]:
            assert isinstance(entry["clearance_per_side_mm"], (int, float))
            assert isinstance(entry["clearance_diametral_mm"], (int, float))

    def test_diametral_is_twice_the_per_side_clearance(self):
        for entry in lookup("fits", detailed=True)["entries"]:
            assert entry["clearance_diametral_mm"] == pytest.approx(
                2 * entry["clearance_per_side_mm"]
            ), f"{entry['name']} diametral clearance is not twice the per-side value"

    def test_ranges_are_ordered_low_to_high(self):
        for entry in lookup("fits", detailed=True)["entries"]:
            for field in ("clearance_per_side_range_mm", "clearance_diametral_range_mm"):
                low, high = entry[field]
                assert low <= high, f"{entry['name']} {field} is reversed"

    def test_fits_get_looser_from_press_to_sliding(self):
        entries = {e["name"]: e for e in lookup("fits", detailed=True)["entries"]}
        order = [
            "press fit",
            "snap fit / light interference",
            "close running fit (slip)",
            "free running fit (loose)",
            "sliding lid / drawer fit",
        ]
        values = [entries[name]["clearance_per_side_mm"] for name in order]
        assert values == sorted(values)

    def test_notes_cover_the_standard_fdm_advice(self):
        notes = " ".join(lookup("fits")["notes"])
        assert "horizontal" in notes.lower()
        assert "elephant foot" in notes.lower()
        assert "$slop" in notes

    def test_slop_note_records_the_bosl2_default_and_per_side_application(self):
        """BOSL2's $slop default is 0.0, and BOSL2 applies it per side."""
        slop_notes = [note for note in lookup("fits")["notes"] if "$slop" in note]
        assert slop_notes
        combined = " ".join(slop_notes)
        assert "0.0" in combined
        assert "PER SIDE" in combined or "per side" in combined


class TestFastenersContent:
    EXPECTED_SIZES = ["M2", "M2.5", "M3", "M4", "M5", "M6", "M8"]

    def test_covers_m2_through_m8(self):
        names = [entry["name"] for entry in lookup("fasteners")["entries"]]
        assert names == self.EXPECTED_SIZES

    def test_clearance_holes_increase_from_close_to_free(self):
        for entry in lookup("fasteners")["entries"]:
            assert (
                entry["clearance_hole_close_mm"]
                < entry["clearance_hole_medium_mm"]
                < entry["clearance_hole_free_mm"]
            ), f"{entry['name']} clearance holes are not ordered"

    def test_clearance_holes_are_larger_than_nominal(self):
        for entry in lookup("fasteners")["entries"]:
            assert entry["clearance_hole_close_mm"] > entry["nominal_diameter_mm"]

    def test_tap_drill_is_nominal_minus_the_pitch(self):
        for entry in lookup("fasteners")["entries"]:
            expected = entry["nominal_diameter_mm"] - entry["thread_pitch_coarse_mm"]
            assert entry["tap_drill_mm"] == pytest.approx(expected, abs=0.05), (
                f"{entry['name']} tap drill {entry['tap_drill_mm']} does not match "
                f"nominal minus pitch ({expected})"
            )

    def test_iso_4762_head_height_equals_nominal_diameter(self):
        for entry in lookup("fasteners")["entries"]:
            assert entry["shcs_head_height_mm"] == entry["nominal_diameter_mm"]

    def test_counterbore_clears_the_head(self):
        for entry in lookup("fasteners")["entries"]:
            assert entry["counterbore_diameter_mm"] > entry["shcs_head_diameter_mm"]

    def test_all_fastener_entries_are_standard_confidence(self):
        for entry in lookup("fasteners")["entries"]:
            assert entry["confidence"] == "standard"

    def test_sources_cite_the_governing_standards(self):
        sources = " ".join(lookup("fasteners")["sources"])
        for standard in ("ISO 273", "ISO 4762", "ISO 4032"):
            assert standard in sources

    @pytest.mark.parametrize(
        ("size", "close", "medium", "free"),
        [
            ("M2", 2.2, 2.4, 2.6),
            ("M2.5", 2.7, 2.9, 3.1),
            ("M3", 3.2, 3.4, 3.6),
            ("M4", 4.3, 4.5, 4.8),
            ("M5", 5.3, 5.5, 5.8),
            ("M6", 6.4, 6.6, 7.0),
            ("M8", 8.4, 9.0, 10.0),
        ],
    )
    def test_full_iso_273_table(self, size, close, medium, free):
        entries = {entry["name"]: entry for entry in lookup("fasteners")["entries"]}
        entry = entries[size]
        assert entry["clearance_hole_close_mm"] == close
        assert entry["clearance_hole_medium_mm"] == medium
        assert entry["clearance_hole_free_mm"] == free

    @pytest.mark.parametrize(
        ("size", "width_af", "height"),
        [
            ("M2", 4.0, 1.6),
            ("M2.5", 5.0, 2.0),
            ("M3", 5.5, 2.4),
            ("M4", 7.0, 3.2),
            ("M5", 8.0, 4.7),
            ("M6", 10.0, 5.2),
            ("M8", 13.0, 6.8),
        ],
    )
    def test_full_iso_4032_nut_table(self, size, width_af, height):
        entries = {entry["name"]: entry for entry in lookup("fasteners")["entries"]}
        assert entries[size]["hex_nut_width_across_flats_mm"] == width_af
        assert entries[size]["hex_nut_height_mm"] == height


class TestInsertsContent:
    def test_covers_m2_through_m5(self):
        names = [entry["name"] for entry in lookup("inserts")["entries"]]
        for size in ("M2", "M2.5", "M3", "M4", "M5"):
            assert size in names

    def test_hole_is_smaller_than_the_insert_and_deeper_than_it_is_long(self):
        for entry in lookup("inserts")["entries"]:
            assert entry["hole_diameter_mm"] < entry["insert_outer_diameter_mm"]
            assert entry["recommended_hole_depth_mm"] >= entry["insert_length_mm"]

    def test_boss_allows_for_the_minimum_wall_on_both_sides(self):
        for entry in lookup("inserts")["entries"]:
            expected = entry["insert_outer_diameter_mm"] + 2 * entry["min_wall_thickness_mm"]
            assert entry["suggested_boss_outer_diameter_mm"] == pytest.approx(expected, abs=0.05)

    def test_entries_are_consensus_not_standard(self):
        """Insert dimensions are vendor-specific, so they must not claim to be standard."""
        for entry in lookup("inserts")["entries"]:
            assert entry["confidence"] == "consensus"

    def test_notes_tell_the_reader_to_check_the_datasheet(self):
        for entry in lookup("inserts", detailed=True)["entries"]:
            assert "datasheet" in entry["note"].lower()

    def test_sources_name_a_vendor(self):
        sources = " ".join(lookup("inserts")["sources"]).lower()
        assert "cnc kitchen" in sources


class TestBearingsContent:
    EXPECTED = {
        "608": (8.0, 22.0, 7.0),
        "625": (5.0, 16.0, 5.0),
        "623": (3.0, 10.0, 4.0),
        "626": (6.0, 19.0, 6.0),
        "6000": (10.0, 26.0, 8.0),
        "688": (8.0, 16.0, 5.0),
        "MR105": (5.0, 10.0, 4.0),
    }

    @pytest.mark.parametrize("designation", sorted(EXPECTED))
    def test_bearing_dimensions(self, designation):
        result = lookup("bearings", designation)
        assert len(result["entries"]) == 1, f"{designation} did not match exactly one entry"
        (entry,) = result["entries"]
        actual = (entry["bore_mm"], entry["outer_diameter_mm"], entry["width_mm"])
        assert actual == self.EXPECTED[designation]

    def test_bore_is_smaller_than_the_outer_diameter(self):
        for entry in lookup("bearings")["entries"]:
            assert entry["bore_mm"] < entry["outer_diameter_mm"]

    def test_notes_give_press_fit_pocket_advice(self):
        for entry in lookup("bearings", detailed=True)["entries"]:
            assert "pocket" in entry["note"].lower()

    def test_variant_width_ambiguity_is_flagged(self):
        """688 and MR105 have different widths open vs sealed; say so."""
        entries = {e["name"]: e for e in lookup("bearings", detailed=True)["entries"]}
        for designation in ("688", "MR105"):
            (entry,) = [e for name, e in entries.items() if name.startswith(designation)]
            assert "variant" in entry["note"].lower()


class TestMagnetsContent:
    def test_covers_the_common_disc_sizes(self):
        names = " ".join(entry["name"] for entry in lookup("magnets")["entries"])
        for size in ("6x3", "8x3", "10x3", "5x2"):
            assert size in names

    def test_pocket_is_larger_than_the_magnet(self):
        for entry in lookup("magnets")["entries"]:
            assert entry["pocket_diameter_press_mm"] > entry["diameter_mm"]
            assert entry["pocket_diameter_glue_mm"] > entry["pocket_diameter_press_mm"]
            assert entry["pocket_depth_mm"] > entry["thickness_mm"]


class TestJointsContent:
    EXPECTED = [
        "dovetail",
        "snap-fit cantilever",
        "press-fit pin",
        "tongue and groove",
        "living hinge",
        "mortise and tenon",
        "screw boss",
        "captive nut trap",
    ]

    def test_covers_every_required_joint(self):
        names = [entry["name"] for entry in lookup("joints")["entries"]]
        assert names == self.EXPECTED

    def test_every_joint_says_when_to_use_it(self):
        for entry in lookup("joints")["entries"]:
            assert entry["use_when"].strip()

    def test_dovetail_angle_is_in_the_usual_range(self):
        (entry,) = lookup("joints", "dovetail")["entries"]
        low, high = entry["angle_range_deg"]
        assert (low, high) == (8.0, 14.0)
        assert low <= entry["typical_angle_deg"] <= high

    def test_snap_fit_deflection_and_angles(self):
        (entry,) = lookup("joints", "cantilever")["entries"]
        assert entry["deflection_range_mm"] == [0.5, 1.0]
        assert entry["insertion_angle_deg"] == 30.0
        assert entry["retention_angle_range_deg"] == [60.0, 90.0]

    def test_living_hinge_thickness_and_material_warning(self):
        (entry,) = lookup("joints", "living hinge")["entries"]
        assert entry["thickness_range_mm"] == [0.3, 0.5]
        note = lookup("joints", "living hinge", detailed=True)["entries"][0]["note"]
        assert "polypropylene" in note.lower()
        assert "PLA" in note

    @pytest.mark.parametrize(
        ("query", "module"),
        [
            ("dovetail", "dovetail()"),
            ("cantilever", "rabbit_clip()"),
            ("press-fit pin", "snap_pin()"),
            ("tongue", "partition()"),
        ],
    )
    def test_bosl2_module_names_are_recorded(self, query, module):
        (entry,) = lookup("joints", query)["entries"]
        assert module in entry["bosl2_modules"]

    def test_bosl2_modules_look_like_module_calls(self):
        for entry in lookup("joints")["entries"]:
            for module in entry.get("bosl2_modules", []):
                assert module.endswith("()"), f"{module} does not look like a module name"


class TestConventionsContent:
    def test_every_convention_states_a_rule(self):
        for entry in lookup("conventions")["entries"]:
            assert entry["rule"].strip()

    def test_brief_and_topic_agree_on_the_core_ideas(self):
        brief = conventions_brief().lower()
        for phrase in ("millimetre", "origin", "epsilon", "clearance", "$fn"):
            assert phrase in brief


class TestDfmContent:
    def test_overhang_rule_is_45_degrees(self):
        (entry,) = lookup("dfm", "overhang")["entries"]
        assert entry["max_unsupported_overhang_deg"] == 45.0

    def test_minimum_wall_matches_two_perimeters_of_a_04_nozzle(self):
        (entry,) = lookup("dfm", "minimum wall")["entries"]
        assert entry["nozzle_diameter_mm"] == 0.4
        assert entry["min_wall_mm"] == pytest.approx(2 * entry["nozzle_diameter_mm"])
        assert entry["wall_range_mm"] == [0.8, 1.6]

    def test_horizontal_holes_lose_more_than_vertical_ones(self):
        (entry,) = lookup("dfm", "hole compensation")["entries"]
        assert entry["horizontal_hole_undersize_mm"] > entry["vertical_hole_undersize_mm"]

    def test_print_in_place_gap_is_03_to_05(self):
        (entry,) = lookup("dfm", "print-in-place")["entries"]
        assert entry["gap_range_mm"] == [0.3, 0.5]
        assert entry["confidence"] == "calibrate"

    def test_every_numeric_range_is_ordered(self):
        for entry in lookup("dfm", detailed=True)["entries"]:
            for field, value in entry.items():
                if not field.endswith(("_range_mm", "_range_deg", "_range")):
                    continue
                low, high = value
                assert low <= high, f"{entry['name']} {field} is reversed"


class TestMaterialsContent:
    EXPECTED_SUBSTRINGS = ["PLA", "PETG", "ABS", "ASA", "TPU", "Nylon", "PC", "resin"]

    @pytest.mark.parametrize("material", EXPECTED_SUBSTRINGS)
    def test_covers_every_required_material(self, material):
        names = " ".join(entry["name"] for entry in lookup("materials")["entries"])
        assert material in names

    def test_densities_are_physically_plausible(self):
        for entry in lookup("materials")["entries"]:
            assert 0.8 <= entry["density_g_cm3"] <= 2.5, f"{entry['name']} density is implausible"

    def test_density_sits_inside_its_stated_range(self):
        for entry in lookup("materials")["entries"]:
            low, high = entry["density_range_g_cm3"]
            assert low <= entry["density_g_cm3"] <= high, f"{entry['name']} density outside range"

    def test_pla_and_abs_densities(self):
        entries = {entry["name"]: entry for entry in lookup("materials")["entries"]}
        assert entries["PLA"]["density_g_cm3"] == 1.24
        assert entries["ABS"]["density_g_cm3"] == 1.04

    def test_every_material_has_a_printing_note(self):
        for entry in lookup("materials", detailed=True)["entries"]:
            assert len(entry["note"]) > 40, f"{entry['name']} printing note is too thin"


# ---------------------------------------------------------------------------
# Module hygiene
# ---------------------------------------------------------------------------


class TestModuleHygiene:
    def test_public_api_is_exported(self):
        for name in ("TOPICS", "list_topics", "lookup", "conventions_brief", "cheatsheet"):
            assert name in reference.__all__
            assert hasattr(reference, name)

    def test_sources_look_like_urls_or_standard_names(self):
        for topic in TOPICS:
            for source in lookup(topic)["sources"]:
                assert "http" in source or any(
                    token in source for token in ("ISO", "DIN", "ASME", "BOSL2", "SPIROL")
                ), f"{topic} has an unattributable source: {source!r}"

    def test_no_topic_is_entirely_calibrate(self):
        """Every topic should offer at least one number better than a guess."""
        for topic in TOPICS:
            levels = {entry["confidence"] for entry in lookup(topic)["entries"]}
            assert levels - {"calibrate"}, f"{topic} offers only calibrate-grade data"
