"""Decode selected still images before retaining any bytes or metadata."""

from typing import cast

from io import BytesIO

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from .constants import MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS
from .errors import ArtifactCtlError


def normalize_image(data: bytes, mime: str) -> tuple[bytes, int, int]:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ArtifactCtlError(
            "INVALID_IMAGE", "Image must be between 1 byte and 10 MiB"
        )
    formats = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
    if mime not in formats:
        raise ArtifactCtlError("INVALID_IMAGE", "Select a PNG, JPEG or WebP image")
    try:
        with Image.open(BytesIO(data)) as probe:
            probe.verify()
        with Image.open(BytesIO(data)) as source:
            if source.format != formats[mime] or getattr(source, "n_frames", 1) != 1:
                raise ArtifactCtlError(
                    "INVALID_IMAGE", "Image type must match a single still frame"
                )
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise ArtifactCtlError(
                    "INVALID_IMAGE", "Image exceeds the 20 megapixel limit"
                )
            source.load()
            frame = ImageOps.exif_transpose(source)
            output_mode = (
                "RGBA"
                if "A" in frame.getbands() or "transparency" in frame.info
                else "RGB"
            )
            if frame.mode == "P":
                frame = frame.convert(output_mode)
            if profile := source.info.get("icc_profile"):
                frame = cast(
                    Image.Image,
                    ImageCms.profileToProfile(
                        frame,
                        ImageCms.ImageCmsProfile(BytesIO(profile)),
                        ImageCms.createProfile("sRGB"),
                        outputMode=output_mode,
                    ),
                )
            else:
                frame = frame.convert(output_mode)
            output = BytesIO()
            frame.info.clear()
            frame.save(output, format="PNG")
            normalized = output.getvalue()
            if len(normalized) > MAX_IMAGE_BYTES:
                raise ArtifactCtlError(
                    "INVALID_IMAGE",
                    "Normalized image exceeds 10 MiB; select a smaller image",
                )
            return normalized, frame.width, frame.height
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        ImageCms.PyCMSError,
    ) as exc:
        raise ArtifactCtlError("INVALID_IMAGE", "Image could not be decoded") from exc
