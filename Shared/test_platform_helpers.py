import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from Shared.platform_helpers import open_file, show_startup_error


class DesktopIntegrationTests(unittest.TestCase):
    def test_windows_keeps_startfile(self):
        with patch('Shared.platform_helpers.sys.platform', 'win32'), patch('Shared.platform_helpers.os.startfile', create=True) as start:
            open_file('folder/file.dch')
        start.assert_called_once_with('folder/file.dch')

    def test_unix_default_handler_and_failure(self):
        for platform in ('darwin', 'linux'):
            with self.subTest(platform=platform), patch('Shared.platform_helpers.sys.platform', platform), patch('PySide6.QtGui.QDesktopServices.openUrl', return_value=True) as launch:
                open_file('folder/file.dch')
                self.assertEqual(Path(launch.call_args.args[0].toLocalFile()), Path('folder/file.dch').resolve())
            with patch('Shared.platform_helpers.sys.platform', platform), patch('PySide6.QtGui.QDesktopServices.openUrl', return_value=False):
                with self.assertRaises(OSError):
                    open_file('file.dch')

    def test_unix_startup_error_without_qt(self):
        with patch('Shared.platform_helpers.sys.platform', 'linux'), patch.dict(sys.modules, {'PySide6.QtWidgets': None}), patch('builtins.print') as output:
            show_startup_error('Startup error', 'Missing dependency')
        output.assert_called_once_with('Startup error\nMissing dependency', file=sys.stderr)


if __name__ == '__main__':
    unittest.main()
