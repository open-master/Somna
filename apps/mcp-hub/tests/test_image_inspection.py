from types import SimpleNamespace

from PIL import Image

from app.tools.filesystem import _action_inspect_image


def test_image_decode_and_corrupt_file(tmp_path):
    settings = SimpleNamespace(fs_max_file_bytes=1024 * 1024)
    image = tmp_path / "cloud.png"
    Image.new("RGB", (24, 16), "white").save(image)
    result = _action_inspect_image(image, settings)
    assert result.ok
    assert result.output["image_valid"] is True
    assert result.output["width"] == 24
    image.write_bytes(b"not an image")
    assert not _action_inspect_image(image, settings).ok


def test_missing_and_empty_images(tmp_path):
    settings = SimpleNamespace(fs_max_file_bytes=1024)
    image = tmp_path / "missing.png"
    assert not _action_inspect_image(image, settings).ok
    image.touch()
    assert not _action_inspect_image(image, settings).ok
