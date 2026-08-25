"""The seam between the SDK and the mapping, pinned without a network.

``map_annotate_result`` reads one shape: the REST JSON one, camelCase keys and
enums as names. The SDK hands back proto objects, and
``_annotate_over_the_wire`` converts them with two ``to_dict`` arguments. If a
future SDK renames or drops either, the mapping would receive snake_case keys and
integer enums, recognize nothing, and fail as an empty result -- a confusing way
to learn about an upgrade.

The conversion needs the SDK but no credentials and no network, so it is pinned
here rather than left to the live test. What remains live-only is whether the
service still *sends* this shape; what these tests own is whether docdoc still
reads what the SDK produces.

Skipped on a base install, per Constitution XII and SC-013.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

vision = pytest.importorskip("google.cloud.vision")

from docdoc.ingest.parsers.gcv import map_annotate_result  # noqa: E402
from docdoc.ingest.source import SourceFile  # noqa: E402


def converted(response: Any) -> dict[str, Any]:
    """Exactly the conversion the adapter performs on the wire path."""
    return vision.AnnotateImageResponse.to_dict(  # type: ignore[no-any-return]
        response,
        preserving_proto_field_name=False,
        use_integers_for_enums=False,
    )


@pytest.fixture
def source() -> SourceFile:
    from pathlib import Path

    path = Path(__file__).parent.parent / "fixtures" / "image" / "sample_page.png"
    return SourceFile.from_bytes(path.read_bytes(), filename=path.name)


@pytest.fixture
def response() -> Any:
    """One word, one line break, built with the SDK's own types."""
    annotation = vision.TextAnnotation
    return vision.AnnotateImageResponse(
        full_text_annotation=annotation(
            text="Hi",
            pages=[
                vision.Page(
                    width=10,
                    height=20,
                    blocks=[
                        vision.Block(
                            paragraphs=[
                                vision.Paragraph(
                                    words=[
                                        vision.Word(
                                            confidence=0.9,
                                            bounding_box=vision.BoundingPoly(
                                                vertices=[
                                                    vision.Vertex(x=0, y=1),
                                                    vision.Vertex(x=5, y=1),
                                                    vision.Vertex(x=5, y=9),
                                                    vision.Vertex(x=0, y=9),
                                                ]
                                            ),
                                            symbols=[
                                                vision.Symbol(text="H"),
                                                vision.Symbol(
                                                    text="i",
                                                    property=annotation.TextProperty(
                                                        detected_break=annotation.DetectedBreak(
                                                            type_=annotation.DetectedBreak.BreakType.LINE_BREAK
                                                        )
                                                    ),
                                                ),
                                            ],
                                        )
                                    ]
                                )
                            ]
                        )
                    ],
                )
            ],
        )
    )


class TestTheConversionArgumentsStillExist:
    def test_to_dict_accepts_both_arguments_the_adapter_passes(self) -> None:
        parameters = inspect.signature(vision.AnnotateImageResponse.to_dict).parameters

        assert "preserving_proto_field_name" in parameters
        assert "use_integers_for_enums" in parameters

    def test_the_feature_the_adapter_requests_still_exists(self) -> None:
        # TEXT_DETECTION would return no block/paragraph/word tree and no break
        # markers, so the adapter would map an empty document from a page of text.
        assert vision.Feature.Type.DOCUMENT_TEXT_DETECTION is not None

    def test_the_client_call_still_takes_the_retry_and_timeout_arguments(self) -> None:
        # docdoc owns the retry policy; `retry=None` is what stops a second one
        # underneath multiplying the attempt count past the documented bound.
        parameters = inspect.signature(vision.ImageAnnotatorClient.annotate_image).parameters

        assert "retry" in parameters
        assert "timeout" in parameters


class TestTheConvertedShapeIsWhatTheMappingReads:
    def test_keys_are_camel_case(self, response: Any) -> None:
        result = converted(response)
        word = result["fullTextAnnotation"]["pages"][0]["blocks"][0]["paragraphs"][0]["words"][0]

        assert "boundingBox" in word, f"proto field names leaked through: {sorted(word)}"

    def test_enums_are_names_not_integers(self, response: Any) -> None:
        result = converted(response)
        word = result["fullTextAnnotation"]["pages"][0]["blocks"][0]["paragraphs"][0]["words"][0]
        break_type = word["symbols"][-1]["property"]["detectedBreak"]["type"]

        assert break_type == "LINE_BREAK"

    def test_a_real_sdk_response_maps_end_to_end(self, response: Any, source: SourceFile) -> None:
        """The whole point: SDK object in, valid Document out, no network."""
        document = map_annotate_result(
            converted(response), source=source, options={}, text_layer=None
        )

        assert document.text == "Hi\n"
        (token,) = document.tokens
        assert token.geometry is not None
        assert token.geometry.bbox.x1 == pytest.approx(0.5)  # 5 of 10 px
        assert token.source_confidence == pytest.approx(0.9)

    def test_a_per_image_error_survives_the_conversion(self, source: SourceFile) -> None:
        # The failure travels inside a successful response rather than as an
        # exception, so it has to survive to_dict for the adapter to see it.
        failed = vision.AnnotateImageResponse()
        failed.error.code = 3
        failed.error.message = "Bad image data"

        result = converted(failed)

        assert result["error"]["code"] == 3
