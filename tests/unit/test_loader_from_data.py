"""Schema data validates through one path, whether it came from a file or a console row."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docdoc.extraction.errors import SchemaError
from docdoc.extraction.loader import parse_schema, prompt_from_text


def test_parse_schema_accepts_a_file_shaped_dict():
    doc = json.loads(Path("schemas/invoice@2.json").read_text())
    assert parse_schema(doc).identity == "invoice@2"


def test_parse_schema_reports_each_problem_with_its_location():
    doc = {"name": "x", "version": 1, "fields": [{"name": "Bad Name", "type": "string"}]}
    with pytest.raises(SchemaError) as e:
        parse_schema(doc)
    assert any(loc.startswith("fields.0.name") for loc, _ in e.value.problems)


def test_parse_schema_refuses_a_non_object():
    with pytest.raises(SchemaError):
        parse_schema([1])


def test_prompt_from_text_refuses_blank():
    schema = parse_schema({"name": "x", "version": 1})
    with pytest.raises(SchemaError):
        prompt_from_text(schema, "  \n")
