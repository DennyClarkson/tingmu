from unittest.mock import Mock

import pytest

from tingmu.quick import main


@pytest.fixture
def font_database(monkeypatch):
    database = Mock(spec=main.QFontDatabase)
    monkeypatch.setattr(main, "QFontDatabase", database)
    return database


def test_accepted_font_path_needs_no_python_file_read(tmp_path, font_database):
    # Only the simulated native loader can open this path.
    path = tmp_path / "font.otf"
    font_database.addApplicationFont.return_value = 0

    main._register_bundled_font(path)

    font_database.addApplicationFont.assert_called_once_with(str(path))
    font_database.addApplicationFontFromData.assert_not_called()


def test_rejected_unicode_font_path_retries_with_file_contents(tmp_path, font_database):
    path = tmp_path / "听幕🎬" / "font.otf"
    path.parent.mkdir()
    contents = b"OTTO\x00\xff"
    path.write_bytes(contents)
    font_database.addApplicationFont.return_value = -1
    font_database.addApplicationFontFromData.return_value = 0

    main._register_bundled_font(path)

    font_database.addApplicationFont.assert_called_once_with(str(path))
    font_database.addApplicationFontFromData.assert_called_once()
    assert bytes(font_database.addApplicationFontFromData.call_args.args[0]) == contents


def test_rejected_font_data_reports_portable_folder_error(tmp_path, font_database):
    path = tmp_path / "font.otf"
    path.write_bytes(b"corrupt font")
    font_database.addApplicationFont.return_value = -1
    font_database.addApplicationFontFromData.return_value = -1

    with pytest.raises(RuntimeError, match="随附字体无法读取，请检查便携文件夹是否完整"):
        main._register_bundled_font(path)
