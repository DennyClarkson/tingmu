import pytest


def pytest_addoption(parser):
    parser.addoption("--require-ffmpeg", action="store_true",
                     help="Fail subtitle integration tests if FFmpeg or ffprobe is unavailable.")


@pytest.fixture
def ffmpeg(request):
    from tingmu import embedded_subtitles

    try:
        embedded_subtitles._binary("ffprobe")
        return embedded_subtitles._binary("ffmpeg")
    except embedded_subtitles.SubtitleError:
        if request.config.getoption("--require-ffmpeg"):
            pytest.fail("Subtitle integration tests require both ffmpeg and ffprobe")
        pytest.skip("FFmpeg CLI unavailable")
