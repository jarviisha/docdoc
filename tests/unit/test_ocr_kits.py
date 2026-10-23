"""The two OCR kits (`packaging/ocr/paddlex/`, `packaging/ocr/tesseract/`) and
the example endpoints file they are meant to be registered through, checked
the same way `test_documented_api_references_resolve.py` checks prose: by
reading the files, not by running the containers they describe (neither kit
need be built for CI to catch the two ways they actually drift).

**`paddle.max_pages` matching `OCR.yaml`'s `max_num_input_imgs` is the one
fact this suite exists to pin.** PaddleX's serving CLI silently truncates a
PDF longer than `max_num_input_imgs` — HTTP 200, first N pages, no error
(`.superpowers/sdd/2026-10-19-ocr-gateway/paddlex-27-files.md`) — which is
exactly the failure `PaddleXParser._pdf_chunks` exists to prevent, and it can
only prevent it if the endpoint's own `max_pages` tells it where the service's
cliff actually is. A kit and an example that disagree on this number would
still load; the mismatch would only surface in production, as a silently
short document.
"""

from __future__ import annotations

import pathlib
import re

from docdoc.ingest.parsers.paddlex import load_endpoints

_EXAMPLE = pathlib.Path("packaging/ocr/endpoints.example.json")
_PADDLEX_OCR_YAML = pathlib.Path("packaging/ocr/paddlex/OCR.yaml")
_KIT_COMPOSE_FILES = (
    pathlib.Path("packaging/ocr/paddlex/compose.yml"),
    pathlib.Path("packaging/ocr/tesseract/compose.yml"),
)
_MAIN_COMPOSE = pathlib.Path("packaging/docker/compose.yml")


def test_example_endpoints_file_loads() -> None:
    endpoints = load_endpoints(_EXAMPLE)

    ids = {endpoint.id for endpoint in endpoints}
    assert ids == {"paddle", "tesseract"}


def test_paddle_max_pages_matches_kit() -> None:
    # No YAML dependency in the base install (Global Constraints), so the
    # kit's own number is read with a regex rather than a parser.
    text = _PADDLEX_OCR_YAML.read_text(encoding="utf-8")
    match = re.search(r"max_num_input_imgs:\s*(\d+)", text)
    assert match is not None, "OCR.yaml must set Serving.extra.max_num_input_imgs"
    kit_max_pages = int(match.group(1))

    (paddle,) = [e for e in load_endpoints(_EXAMPLE) if e.id == "paddle"]

    assert paddle.max_pages == kit_max_pages


def test_example_endpoints_declare_generous_timeouts() -> None:
    """CPU OCR of a multi-page scan can run for minutes; the generic
    `TransportSettings` defaults (30s/120s) are too short for it (012
    review, Finding 4). The example file is the template deployments copy,
    so it must show a value worth copying, not the bare minimum that loads.
    """
    for endpoint in load_endpoints(_EXAMPLE):
        assert endpoint.timeout_s is not None, endpoint.id
        assert endpoint.timeout_s >= 60, endpoint.id
        assert endpoint.deadline_s is not None, endpoint.id
        assert endpoint.deadline_s >= 300, endpoint.id


def test_kits_pin_images() -> None:
    # `packaging/ocr/tesseract/compose.yml` builds from its own Dockerfile
    # and declares no `image:` at all -- nothing to pin there. Only
    # `packaging/ocr/paddlex/compose.yml` pulls one, and it is the one that
    # must carry a real tag.
    image_line = re.compile(r"^\s*image:\s*(\S+)\s*$", re.M)
    images = [
        image
        for compose_file in _KIT_COMPOSE_FILES
        for image in image_line.findall(compose_file.read_text(encoding="utf-8"))
    ]
    assert images, "expected at least one pinned image: across the OCR kits"
    for image in images:
        assert ":" in image.rsplit("/", 1)[-1], f"{image!r} has no tag"
        assert not image.endswith(":latest"), f"{image!r} is :latest"


def test_kits_declare_restart_policy() -> None:
    # Neither kit runs under any supervisor but docker itself: a host reboot
    # or an OOM kill (the paddlex kit's own `mem_limit: 6g` exists precisely
    # because that is an expected outcome, not a rare one, per the source
    # host this kit is ported from) must bring the container back without a
    # human watching for it.
    service_line = re.compile(r"^  (\w[\w-]*):\n", re.M)
    for compose_file in _KIT_COMPOSE_FILES:
        text = compose_file.read_text(encoding="utf-8")
        services_block = re.search(r"^services:\n(.*)", text, re.S | re.M)
        assert services_block is not None, f"{compose_file} has no services: block"
        body = services_block.group(1)
        service_names = service_line.findall(body)
        assert service_names, f"{compose_file} declares no services"

        for index, name in enumerate(service_names):
            start = body.index(f"  {name}:\n")
            end = (
                body.index(f"  {service_names[index + 1]}:\n")
                if index + 1 < len(service_names)
                else len(body)
            )
            block = body[start:end]
            assert "restart:" in block, f"{compose_file}: service {name!r} has no restart policy"


def test_paddlex_compose_quotes_the_version_pin_in_its_shell_command() -> None:
    """`compose.yml`'s `command:` runs through `bash -lc`, where an unquoted
    `filetype>=1.2` is a redirect (`>`) into a file named `=1.2`, not a pip
    version spec -- `pip install ... filetype` (no floor) plus a stray file
    (012 review, Finding 7). Every `pip install` argument with a bare
    comparison operator must be quoted.
    """
    text = pathlib.Path("packaging/ocr/paddlex/compose.yml").read_text(encoding="utf-8")
    for line in text.splitlines():
        if "pip install" not in line:
            continue
        for word in line.split():
            if re.fullmatch(r"[A-Za-z0-9_.-]+(>=|<=|==|>|<)[\w.]+", word):
                raise AssertionError(f"unquoted version spec in a shell command: {line!r}")


def test_main_compose_has_no_model_service() -> None:
    text = _MAIN_COMPOSE.read_text(encoding="utf-8")
    services_block = re.search(r"^services:\n(.*?)^volumes:", text, re.S | re.M)
    assert services_block is not None
    service_names = re.findall(r"^  (\w[\w-]*):\n", services_block.group(1), re.M)

    # `ocr-worker` is a docdoc worker claiming OCR runs (ADR-0022), not a model:
    # it reaches the OCR hosts over HTTP exactly as `worker` does.
    assert set(service_names) == {
        "api",
        "worker",
        "ocr-worker",
        "postgres",
        "seaweedfs",
        "createbucket",
        "migrate",
    }
