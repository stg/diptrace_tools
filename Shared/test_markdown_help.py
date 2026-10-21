import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QUrl
from PySide6.QtGui import QTextTable
from PySide6.QtWidgets import QApplication, QWidget

from Shared.markdown_help import GuideDialog, show_help


class MarkdownHelpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.parent = QWidget()

    def tearDown(self):
        self.parent.close()
        self.parent.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def guide(self):
        path = self.root/'README.md'
        path.write_text('# Guide\n\n1. First step\n2. Final step\n\n## Files\n\n'
            '```text\nfile.csv\n```\n\n| File | Purpose |\n| --- | --- |\n| a | b |\n',encoding='utf-8')
        return show_help(self.parent,path)

    def test_heading_format_does_not_change_preceding_list_and_table_has_no_blank_header(self):
        dialog = self.guide()
        document = dialog.browser.document()
        block = document.begin()
        while block.isValid() and block.text() != 'Final step':
            block = block.next()
        self.assertTrue(block.isValid())
        self.assertIsNotNone(block.textList())
        self.assertEqual(block.blockFormat().headingLevel(),0)
        table = next(frame for frame in document.rootFrame().childFrames() if isinstance(frame,QTextTable))
        self.assertEqual(table.cellAt(0,0).firstCursorPosition().block().text(),'File')
        self.assertGreaterEqual(table.cellAt(0,0).firstCursorPosition().block().begin().fragment().charFormat().fontWeight(),700)
        self.assertEqual(table.format().headerRowCount(),1)

    def test_topics_find_relative_links_and_reuse(self):
        dialog = self.guide()
        self.assertEqual(dialog.topics.itemText(1),'Files')
        dialog.jump(dialog.topics.itemData(1))
        self.assertEqual(dialog.browser.textCursor().block().text(),'Files')
        dialog.search.setText('Final step'); dialog.find_next()
        self.assertEqual(dialog.browser.textCursor().selectedText(),'Final step')
        self.assertIs(show_help(self.parent,self.root/'README.md'),dialog)
        (self.root/'other.md').write_text('# Other\n\n## Target\n\nDetails.',encoding='utf-8')
        dialog.follow_link(QUrl('other.md#target'))
        other = next(d for d in self.parent.findChildren(GuideDialog) if d.path.name=='other.md')
        self.assertEqual(other.browser.textCursor().block().text(),'Target')
        with patch('Shared.markdown_help.QDesktopServices.openUrl') as open_url:
            dialog.follow_link(QUrl('https://example.com/help'))
        self.assertEqual(open_url.call_args.args[0].toString(),'https://example.com/help')


if __name__ == '__main__':
    unittest.main()
