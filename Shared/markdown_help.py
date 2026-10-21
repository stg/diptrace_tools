"""Shared, nonmodal Markdown reader for the programs' user guides."""
from pathlib import Path
import re

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QKeySequence,
    QPalette, QShortcut, QTextBlockFormat, QTextCharFormat, QTextCursor,
    QTextLength, QTextTable)
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout)


def _slug(text):
    return re.sub(r"[^\w\- ]", "", text.lower()).replace(" ", "-")


class GuideDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(900, 760)
        layout = QVBoxLayout(self)
        navigation = QHBoxLayout()
        navigation.addWidget(QLabel("Jump to"))
        self.topics = QComboBox()
        self.topics.setMinimumContentsLength(22)
        navigation.addWidget(self.topics, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find in guide…")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(230)
        navigation.addWidget(self.search)
        next_button = QPushButton("Next")
        next_button.setAutoDefault(False)
        navigation.addWidget(next_button)
        layout.addLayout(navigation)
        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)
        font = QFont(QApplication.font())
        font.setPointSizeF(max(11, font.pointSizeF()))
        self.browser.setFont(font)
        self.browser.document().setDocumentMargin(26)
        layout.addWidget(self.browser)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setAutoDefault(False)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.topics.activated.connect(lambda index:self.jump(self.topics.itemData(index)))
        self.search.returnPressed.connect(self.find_next)
        next_button.clicked.connect(self.find_next)
        self.browser.anchorClicked.connect(self.follow_link)
        shortcut = QShortcut(QKeySequence.Find, self)
        shortcut.activated.connect(self.focus_search)
        QShortcut(QKeySequence.FindNext, self).activated.connect(self.find_next)

    def focus_search(self):
        self.search.setFocus()
        self.search.selectAll()

    def find_next(self):
        text = self.search.text()
        if text and not self.browser.find(text):
            self.browser.moveCursor(QTextCursor.Start)
            self.browser.find(text)

    def jump(self, position):
        if position is None:
            return
        cursor = QTextCursor(self.browser.document())
        cursor.setPosition(position)
        self.browser.setTextCursor(cursor)
        self.browser.ensureCursorVisible()
        top = self.browser.document().documentLayout().blockBoundingRect(cursor.block()).top()
        self.browser.verticalScrollBar().setValue(int(top))

    def load(self, path, text, title):
        self.path = path
        self.setWindowTitle(title)
        document = self.browser.document()
        document.setBaseUrl(QUrl.fromLocalFile(str(path.parent) + "/"))
        self.browser.setMarkdown(text)
        def clean_tables(frame):
            for child in frame.childFrames():
                if isinstance(child, QTextTable):
                    for row in range(child.rows()):
                        for col in range(child.columns()):
                            cell = child.cellAt(row, col)
                            # Qt's Markdown reader can insert a leading empty
                            # paragraph in the first cell after a fenced block.
                            start = cell.firstCursorPosition()
                            while not start.block().text() and start.block() != cell.lastCursorPosition().block():
                                start.deleteChar()
                                start = cell.firstCursorPosition()
                clean_tables(child)
        clean_tables(document.rootFrame())
        self.topics.clear()
        self.topics.addItem("Top of guide", 0)
        self.anchors = {}
        cursor = QTextCursor(document)
        cursor.beginEditBlock()
        block = document.begin()
        while block.isValid():
            cursor.setPosition(block.position())
            fmt = block.blockFormat()
            heading = fmt.headingLevel()
            table = cursor.currentTable()
            fmt.setLineHeight(135, QTextBlockFormat.ProportionalHeight.value)
            fmt.setTopMargin(0)
            fmt.setBottomMargin(5 if block.textList() or table else 11)
            if heading:
                fmt.setTopMargin(0 if heading == 1 else 23 if heading == 2 else 16)
                fmt.setBottomMargin(12 if heading == 1 else 9)
                fmt.setLineHeight(120, QTextBlockFormat.ProportionalHeight.value)
                cursor.setBlockFormat(fmt)
                cursor.setPosition(block.position())
                cursor.setPosition(block.position() + len(block.text()), QTextCursor.KeepAnchor)
                chars = QTextCharFormat()
                chars.setFontPointSize({1:23, 2:16, 3:12}.get(heading, 11))
                chars.setFontWeight(QFont.DemiBold.value)
                cursor.mergeCharFormat(chars)
                self.anchors[_slug(block.text())] = block.position()
                if heading > 1:
                    self.topics.addItem(("    " if heading > 2 else "") + block.text(), block.position())
            else:
                cursor.setBlockFormat(fmt)
            block = block.next()
        cursor.endEditBlock()
        light = self.browser.palette().color(QPalette.Base).lightness() > 128
        header = QColor("#edf2f7" if light else "#353e49")
        border = self.browser.palette().color(QPalette.Mid)
        def format_tables(frame):
            for child in frame.childFrames():
                if isinstance(child, QTextTable):
                    fmt = child.format()
                    fmt.setWidth(QTextLength(QTextLength.PercentageLength, 100))
                    fmt.setCellPadding(8)
                    fmt.setCellSpacing(0)
                    fmt.setBorder(.5)
                    fmt.setBorderBrush(border)
                    fmt.setHeaderRowCount(1)
                    child.setFormat(fmt)
                    for col in range(child.columns()):
                        cell = child.cellAt(0, col)
                        cell_format = cell.format()
                        cell_format.setBackground(header)
                        cell.setFormat(cell_format)
                        header_cursor = cell.firstCursorPosition()
                        header_cursor.setPosition(cell.lastCursorPosition().position(), QTextCursor.KeepAnchor)
                        chars = QTextCharFormat()
                        chars.setFontWeight(QFont.Bold.value)
                        header_cursor.mergeCharFormat(chars)
                format_tables(child)
        format_tables(document.rootFrame())
        self.browser.moveCursor(QTextCursor.Start)
        self.browser.verticalScrollBar().setValue(0)

    def follow_link(self, url):
        if not url.path() and url.fragment():
            self.jump(self.anchors.get(url.fragment()))
            return
        target = self.browser.document().baseUrl().resolved(url)
        if target.isLocalFile() and Path(target.toLocalFile()).suffix.lower() == ".md":
            dialog = show_help(self.parentWidget(), target.toLocalFile())
            if target.fragment():
                dialog.jump(dialog.anchors.get(target.fragment()))
        else:
            QDesktopServices.openUrl(target)


def show_help(parent, path, title=None):
    path = Path(path).resolve()
    text = path.read_text(encoding="utf-8-sig")
    name = "markdown-help:" + str(path)
    dialog = next((d for d in parent.findChildren(GuideDialog) if d.objectName() == name), None)
    if dialog is None:
        dialog = GuideDialog(parent)
        dialog.setObjectName(name)
    title = title or next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), "How to use")
    dialog.load(path, text, title)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
