from io import BytesIO

import pytest
from PIL import Image, PngImagePlugin

from openminion.modules.artifact.constants import MAX_IMAGE_BYTES
from openminion.modules.artifact.errors import ArtifactCtlError
from openminion.modules.artifact.images import normalize_image

from .utils import artifact_ctl


def _png() -> bytes:
    output = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("secret", "private screenshot metadata")
    Image.new("RGB", (8, 6), "red").save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


def test_selected_images_have_independent_session_refs_and_survive_restart(tmp_path):
    with artifact_ctl(tmp_path) as ctl:
        first = ctl.ingest_image(_png(), mime="image/png", session_id="one")
        second = ctl.ingest_image(_png(), mime="image/png", session_id="two")
        assert first.sha256 == second.sha256
        assert first.ref != second.ref
        assert ctl.index.list_views(first.sha256) == []
        for ref in (
            first.sha256,
            first.ref,
            "/tmp/screen.png",
            "https://example.org/image",
        ):
            with pytest.raises(ArtifactCtlError, match="IMAGE_UNAVAILABLE"):
                ctl.read_image(ref, session_id="two")
        ctl.revoke_image(first.ref, session_id="one")
        replacement = ctl.ingest_image(_png(), mime="image/png", session_id="one")
        assert replacement.ref != first.ref
        with pytest.raises(ArtifactCtlError, match="IMAGE_UNAVAILABLE"):
            ctl.read_image(first.ref, session_id="one")
        ctl.gc(keep_days=0, delete_unreferenced_after_days=0)
        ctl.purge(grace_days=0)
    with artifact_ctl(tmp_path) as ctl:
        assert ctl.read_image(second.ref, session_id="two") == ctl.read_image(
            replacement.ref, session_id="one"
        )
        with Image.open(BytesIO(ctl.read_image(second.ref, session_id="two"))) as image:
            assert image.size == (8, 6)
            assert image.getpixel((0, 0)) == (255, 0, 0)
            assert image.info == {}


def test_reupload_does_not_revive_deleted_image_or_legacy_ref(tmp_path):
    normalized, _, _ = normalize_image(_png(), "image/png")
    with artifact_ctl(tmp_path) as ctl:
        legacy = ctl.ingest_bytes(
            normalized, mime="image/png", session_id="one", opaque=True
        )
        original = ctl.ingest_image(_png(), mime="image/png", session_id="one")
        ctl.delete(legacy.ref)
        replacement = ctl.ingest_image(_png(), mime="image/png", session_id="two")
        assert ctl.read_image(replacement.ref, session_id="two") == normalized
        with pytest.raises(ArtifactCtlError):
            ctl.read_image(original.ref, session_id="one")
        with pytest.raises(ArtifactCtlError):
            ctl.open(legacy.ref, session_id="one")
        assert ctl.get(legacy.ref).deleted_at is not None
        ctl.purge(grace_days=0)
        assert ctl.read_image(replacement.ref, session_id="two") == normalized


def test_revoke_is_scoped_and_blob_delete_revokes_all_refs(tmp_path):
    with artifact_ctl(tmp_path) as ctl:
        first = ctl.ingest_image(_png(), mime="image/png", session_id="one")
        second = ctl.ingest_image(_png(), mime="image/png", session_id="two")
        ctl.revoke_image(first.ref, session_id="two")
        assert ctl.read_image(first.ref, session_id="one")
        ctl.delete(first.sha256, soft=False)
        for image, session in ((first, "one"), (second, "two")):
            with pytest.raises(ArtifactCtlError):
                ctl.read_image(image.ref, session_id=session)


def test_exif_orientation_is_applied_before_metadata_removed():
    output = BytesIO()
    source = Image.new("RGB", (8, 6), "blue")
    exif = Image.Exif()
    exif[274] = 6
    source.save(output, format="JPEG", exif=exif)
    normalized, width, height = normalize_image(output.getvalue(), "image/jpeg")
    assert (width, height) == (6, 8)
    with Image.open(BytesIO(normalized)) as image:
        assert not image.getexif()


@pytest.mark.parametrize(
    "data,mime",
    [
        (b"", "image/png"),
        (b"broken", "image/png"),
        (_png()[:30], "image/png"),
        (_png(), "image/jpeg"),
        (_png(), "image/gif"),
        (b"x" * (MAX_IMAGE_BYTES + 1), "image/png"),
    ],
)
def test_invalid_images_never_enter_storage(tmp_path, data, mime):
    with artifact_ctl(tmp_path) as ctl:
        with pytest.raises(ArtifactCtlError, match="INVALID_IMAGE"):
            ctl.ingest_image(data, mime=mime, session_id="one")
        assert ctl.index.list_recent() == []
        assert ctl.index.active_reference_shas() == set()


def test_animated_images_rejected():
    output = BytesIO()
    first = Image.new("RGB", (8, 6), "red")
    second = Image.new("RGB", (8, 6), "blue")
    first.save(output, format="PNG", save_all=True, append_images=[second])
    with pytest.raises(ArtifactCtlError, match="single still frame"):
        normalize_image(output.getvalue(), "image/png")


def test_decoded_pixel_limit_checked_before_load(monkeypatch):
    monkeypatch.setattr("openminion.modules.artifact.images.MAX_IMAGE_PIXELS", 47)
    with pytest.raises(ArtifactCtlError, match="megapixel"):
        normalize_image(_png(), "image/png")


def test_palette_transparency_is_preserved():
    output = BytesIO()
    source = Image.new("P", (2, 2))
    source.putpalette([255, 0, 0] * 256)
    source.save(output, format="PNG", transparency=0)
    normalized, _, _ = normalize_image(output.getvalue(), "image/png")
    with Image.open(BytesIO(normalized)) as image:
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0)) == (255, 0, 0, 0)


def test_operator_ingest_limit_is_not_bypassed(tmp_path):
    overrides = {"artifactctl": {"blob_store": {"max_ingest_bytes": 1}}}
    with artifact_ctl(tmp_path, overrides) as ctl:
        with pytest.raises(ArtifactCtlError):
            ctl.ingest_image(_png(), mime="image/png", session_id="one")
        assert ctl.index.list_recent() == []


def test_session_cleanup_revokes_only_its_image_bindings(tmp_path):
    with artifact_ctl(tmp_path) as ctl:
        first = ctl.ingest_image(_png(), mime="image/png", session_id="one::generation")
        second = ctl.ingest_image(
            _png(), mime="image/png", session_id="two::generation"
        )
        ctl.revoke_session_images("one::generation")
        with pytest.raises(ArtifactCtlError, match="IMAGE_UNAVAILABLE"):
            ctl.read_image(first.ref, session_id="one::generation")
        assert ctl.read_image(second.ref, session_id="two::generation")
