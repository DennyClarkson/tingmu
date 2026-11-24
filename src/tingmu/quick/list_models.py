"""Stable Qt list rows and bounded Markdown presentation for live workspaces."""

from copy import deepcopy

from PySide6.QtCore import QAbstractListModel, QModelIndex, Property, QThread, Qt, Signal, Slot


def _row_key(row, position):
    # Old in-memory integrations may supply rows without IDs. Keep their ordered
    # identity without adding synthetic IDs to persisted/public message payloads.
    ident = row.get("id")
    return ("id", ident) if ident not in (None, "") else ("legacy-position", position)


class StableListModel(QAbstractListModel):
    """Update rows by ID without resetting delegates and their local UI state."""

    ModelDataRole = int(Qt.ItemDataRole.UserRole) + 1
    countChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        self._keys = []

    def roleNames(self):
        return {self.ModelDataRole: b"modelData"}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == self.ModelDataRole and index.isValid() and 0 <= index.row() < len(self._rows):
            # Qt converts the owned row to QVariant; Python snapshot callers use get().
            return self._rows[index.row()]
        return None

    @Property(int, notify=countChanged)
    def count(self):
        return len(self._rows)

    @Slot(int, result="QVariantMap")
    def get(self, index):
        return deepcopy(self._rows[index]) if 0 <= index < len(self._rows) else {}

    @Slot(str, result="QVariantMap")
    def findById(self, ident):
        """Return an isolated snapshot of just the requested row."""
        try:
            index = self._keys.index(("id", ident))
        except ValueError:
            return {}
        return self.get(index)

    def sync(self, rows):
        """Own a snapshot and emit only the operations needed for this revision."""
        if QThread.currentThread() != self.thread():
            raise RuntimeError("List models must be synchronized on their owning Qt thread")
        rows = list(rows)
        keys = [_row_key(row, index) for index, row in enumerate(rows)]
        if len(set(keys)) != len(keys):
            raise ValueError("List rows must have unique IDs")
        previous_count = len(self._rows)
        wanted = set(keys)
        for index in range(len(self._rows) - 1, -1, -1):
            if self._keys[index] not in wanted:
                self.beginRemoveRows(QModelIndex(), index, index)
                del self._rows[index]
                del self._keys[index]
                self.endRemoveRows()
        for index, (key, row) in enumerate(zip(keys, rows)):
            if index >= len(self._keys) or self._keys[index] != key:
                try:
                    source = self._keys.index(key, index)
                except ValueError:
                    self.beginInsertRows(QModelIndex(), index, index)
                    self._rows.insert(index, deepcopy(row))
                    self._keys.insert(index, key)
                    self.endInsertRows()
                    continue
                # The prefix is already in target order, so moves only go upward.
                self.beginMoveRows(QModelIndex(), source, source, QModelIndex(), index)
                self._rows.insert(index, self._rows.pop(source))
                self._keys.insert(index, self._keys.pop(source))
                self.endMoveRows()
            if self._rows[index] != row:
                self._rows[index] = deepcopy(row)
                cell = self.index(index, 0)
                self.dataChanged.emit(cell, cell, [self.ModelDataRole])
        if previous_count != len(self._rows):
            self.countChanged.emit()


class MessageMarkdownCache:
    """Keep one parsed content revision per current message, never streaming history."""

    def __init__(self):
        self._entries = {}
        self._messages = {}
        self._rows = {}

    def display(self, messages):
        """Public compatibility view: callers may freely mutate every nested value."""
        return deepcopy(self.display_view(messages))

    def display_view(self, messages):
        """Trusted UI-thread view for model synchronization; never mutate its rows.

        Only changed messages acquire new snapshots. StableListModel owns its own
        copies, so handing this view to sync does not expose cache internals.
        """
        from tingmu.richtext import MarkdownBoundary, markdown_blocks

        current, snapshots, display_rows, rows = {}, {}, {}, []
        for index, message in enumerate(messages):
            key = _row_key(message, index)
            content = message.get("content", "")
            cached = self._entries.get(key)
            if cached is None or cached[0] != content:
                if cached is not None and content.startswith(cached[0]):
                    previous, boundary = cached[1], cached[2]
                else:
                    previous, boundary = [], MarkdownBoundary()
                cached = (content, markdown_blocks(content, previous_blocks=previous, boundary=boundary), boundary)
            current[key] = cached
            if self._messages.get(key) == message:
                snapshot = self._messages[key]
                row = self._rows[key]
            else:
                snapshot = deepcopy(message)
                row = {**snapshot, "blocks": cached[1]}
            snapshots[key] = snapshot
            display_rows[key] = row
            rows.append(row)
        # Session changes, removed messages and superseded stream chunks release
        # their content immediately; memory tracks only the displayed messages.
        self._entries = current
        self._messages = snapshots
        self._rows = display_rows
        return rows
