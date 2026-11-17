"""The local sample-document picker.

Browsers cannot be told which folder a native file dialog should open to, so
this is the actual mechanism behind "always default the upload to this
folder": the API lists a configured directory and reads a chosen file by
name. The path-traversal tests matter because `name` arrives from the client.
"""

from __future__ import annotations

import pytest

from leaderboard_api import samples
from leaderboard_api.config import Settings


@pytest.fixture
def sample_dir(tmp_path):
    (tmp_path / "HealthOne NOVA.pdf").write_bytes(b"%PDF-1.4 fake pdf bytes")
    (tmp_path / "HealthOne NOVA2.pdf").write_bytes(b"%PDF-1.4 more bytes")
    (tmp_path / "notes.txt").write_text("plain text sample")
    (tmp_path / "ignored.exe").write_bytes(b"not a supported type")
    sub = tmp_path / "subfolder"
    sub.mkdir()
    (sub / "nested.pdf").write_bytes(b"%PDF-1.4 nested")
    return tmp_path


def settings_for(directory) -> Settings:
    return Settings(
        endpoint_url="https://example.invalid",
        access_key_id="key",
        secret_access_key="secret",
        sample_directory=str(directory),
    )


def test_no_directory_configured_yields_no_samples():
    assert samples.list_samples(Settings()) == []


def test_a_missing_directory_yields_no_samples(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert samples.list_samples(settings_for(missing)) == []


def test_lists_only_supported_files_directly_in_the_folder(sample_dir):
    listed = {item["name"] for item in samples.list_samples(settings_for(sample_dir))}
    assert listed == {"HealthOne NOVA.pdf", "HealthOne NOVA2.pdf", "notes.txt"}
    assert "ignored.exe" not in listed
    assert "nested.pdf" not in listed  # inside a subfolder, not exposed


def test_listing_is_sorted_and_reports_size_and_content_type(sample_dir):
    listed = samples.list_samples(settings_for(sample_dir))
    names = [item["name"] for item in listed]
    assert names == sorted(names)
    pdf = next(item for item in listed if item["name"] == "HealthOne NOVA.pdf")
    assert pdf["content_type"] == "application/pdf"
    assert pdf["size"] == len(b"%PDF-1.4 fake pdf bytes")


def test_read_sample_returns_the_real_bytes(sample_dir):
    body, content_type = samples.read_sample(
        settings_for(sample_dir), "HealthOne NOVA.pdf"
    )
    assert body == b"%PDF-1.4 fake pdf bytes"
    assert content_type == "application/pdf"


def test_read_sample_rejects_a_missing_name(sample_dir):
    with pytest.raises(samples.SampleNotFound):
        samples.read_sample(settings_for(sample_dir), "does-not-exist.pdf")


def test_read_sample_rejects_an_unsupported_extension(sample_dir):
    with pytest.raises(samples.SampleNotFound):
        samples.read_sample(settings_for(sample_dir), "ignored.exe")


@pytest.mark.parametrize(
    "malicious_name",
    [
        "../etc/passwd",
        "subfolder/nested.pdf",
        "..",
        ".",
        "",
        "/etc/passwd",
        "sub\\nested.pdf",
    ],
)
def test_read_sample_rejects_path_traversal_attempts(sample_dir, malicious_name):
    with pytest.raises(samples.SampleNotFound):
        samples.read_sample(settings_for(sample_dir), malicious_name)


def test_read_sample_rejects_a_symlink_escaping_the_folder(tmp_path):
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4 outside the sample folder")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link.pdf").symlink_to(outside)

    with pytest.raises(samples.SampleNotFound):
        samples.read_sample(settings_for(root), "link.pdf")


def test_read_sample_with_no_directory_configured_fails_closed():
    with pytest.raises(samples.SampleNotFound):
        samples.read_sample(Settings(), "anything.pdf")
