from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from steamlan.app.controller import Presence, View
from steamlan.gui.widgets import (
    CopyField,
    MemberRow,
    StatusLine,
    button,
    card,
    divider,
    label,
    set_style_name,
)


class HomePage(QWidget):
    create = Signal()
    join = Signal()
    retry = Signal()

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 36, 28, 24)
        layout.setSpacing(14)

        layout.addWidget(label("SteamVirtualLAN", "title"))
        layout.addWidget(label("Private networks with your Steam friends.", "muted"))
        layout.addSpacing(10)

        steam_card, steam_layout = card()
        row = QHBoxLayout()
        self.steam_status = StatusLine()
        row.addWidget(self.steam_status, 1)
        self.retry_button = button("Retry", "small")
        self.retry_button.clicked.connect(self.retry)
        row.addWidget(self.retry_button)
        steam_layout.addLayout(row)
        layout.addWidget(steam_card)

        layout.addSpacing(10)
        self.create_button = button("Create Network", "primary")
        self.create_button.clicked.connect(self.create)
        layout.addWidget(self.create_button)
        layout.addWidget(label("Start a network and invite Steam friends.", "muted"))
        layout.addSpacing(6)
        self.join_button = button("Join Network")
        self.join_button.clicked.connect(self.join)
        layout.addWidget(self.join_button)
        layout.addWidget(label("Join with a Lobby ID and access code.", "muted"))
        self.message = label(wrap=True)
        layout.addWidget(self.message)
        layout.addStretch(1)
        layout.addWidget(
            label(
                "Closing the window keeps SteamVirtualLAN running in the notification area.",
                "caption",
                True,
            )
        )

    def render(self, view: View) -> None:
        self.steam_status.set(view.steam_status, view.steam_tone)
        self.retry_button.setVisible(not view.steam_ready)
        enabled = view.steam_ready and not view.busy
        self.create_button.setEnabled(enabled)
        self.join_button.setEnabled(enabled)
        set_style_name(self.message, "error" if view.error else "muted")
        self.message.setText(view.error or view.busy)


class JoinPage(QWidget):
    back = Signal()
    submit = Signal(str, str)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 24)
        layout.setSpacing(8)

        back = button("Back", "link")
        back.clicked.connect(self.back)
        layout.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addSpacing(6)
        layout.addWidget(label("Join a network", "heading"))
        layout.addWidget(
            label("Enter the Lobby ID and access code from a member of the network.", "muted", True)
        )
        layout.addSpacing(14)

        layout.addWidget(label("LOBBY ID", "caption"))
        self.lobby_id = QLineEdit()
        self.lobby_id.setPlaceholderText("17 or 18 digits")
        layout.addWidget(self.lobby_id)
        layout.addSpacing(6)
        layout.addWidget(label("ACCESS CODE", "caption"))
        self.access_code = QLineEdit()
        self.access_code.setPlaceholderText("XXXXX-XXXXX")
        layout.addWidget(self.access_code)

        layout.addSpacing(10)
        self.join_button = button("Join Network", "primary")
        self.join_button.clicked.connect(self._submit)
        layout.addWidget(self.join_button)
        self.message = label(wrap=True)
        layout.addWidget(self.message)
        layout.addStretch(1)

        for field in (self.lobby_id, self.access_code):
            field.returnPressed.connect(self._submit)
            field.textChanged.connect(self._update_button)
        self._busy = False
        self._update_button()

    def render(self, view: View) -> None:
        self._busy = bool(view.busy)
        self.lobby_id.setEnabled(not self._busy)
        self.access_code.setEnabled(not self._busy)
        set_style_name(self.message, "error" if view.error else "muted")
        self.message.setText(view.error or view.busy)
        self._update_button()

    def _update_button(self) -> None:
        filled = bool(self.lobby_id.text().strip() and self.access_code.text().strip())
        self.join_button.setEnabled(filled and not self._busy)

    def _submit(self) -> None:
        if self.join_button.isEnabled():
            self.submit.emit(self.lobby_id.text(), self.access_code.text())


class NetworkPage(QWidget):
    invite = Signal()
    toggle_online = Signal()
    leave = Signal()

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        header = QHBoxLayout()
        self.status = StatusLine("heading")
        self.status.dot.set_tone("pending")
        header.addWidget(self.status, 1)
        layout.addLayout(header)

        details, details_layout = card()
        self.lobby_id = CopyField("Lobby ID")
        details_layout.addWidget(self.lobby_id)
        self.code_divider = divider()
        details_layout.addWidget(self.code_divider)
        self.access_code = CopyField("Access code")
        details_layout.addWidget(self.access_code)
        self.adapter_divider = divider()
        details_layout.addWidget(self.adapter_divider)
        self.adapter_caption = label("VIRTUAL NETWORK", "caption")
        details_layout.addWidget(self.adapter_caption)
        self.adapter_status = StatusLine()
        details_layout.addWidget(self.adapter_status)
        layout.addWidget(details)

        members_header = QHBoxLayout()
        members_header.addWidget(label("MEMBERS", "caption"))
        members_header.addStretch(1)
        self.member_count = label(name="caption")
        members_header.addWidget(self.member_count)
        layout.addLayout(members_header)

        self.members = QVBoxLayout()
        self.members.setContentsMargins(0, 0, 0, 0)
        self.members.setSpacing(2)
        members_widget = QWidget()
        members_column = QVBoxLayout(members_widget)
        members_column.setContentsMargins(0, 0, 0, 0)
        members_column.addLayout(self.members)
        members_column.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(members_widget)
        layout.addWidget(scroll, 1)
        self._rows: dict[int, MemberRow] = {}

        self._error_shown = False
        self.notice = label(name="notice", wrap=True)
        layout.addWidget(self.notice)
        buttons = QHBoxLayout()
        self.invite_button = button("Invite Steam Friend")
        self.invite_button.clicked.connect(self.invite)
        self.online_button = button("Go Offline")
        self.online_button.clicked.connect(self.toggle_online)
        self.leave_button = button("Leave Network", "danger")
        self.leave_button.clicked.connect(self.leave)
        buttons.addWidget(self.invite_button, 1)
        buttons.addWidget(self.online_button, 1)
        buttons.addWidget(self.leave_button)
        layout.addLayout(buttons)

    def render(self, view: View) -> None:
        self.status.set(view.network_status, view.network_tone)
        self.lobby_id.set_value(str(view.lobby_id))
        self.access_code.set_value(view.access_code)
        self.access_code.setVisible(bool(view.access_code))
        self.code_divider.setVisible(bool(view.access_code))
        self.adapter_status.set(view.adapter_status, view.adapter_tone)
        for widget in (self.adapter_divider, self.adapter_caption, self.adapter_status):
            widget.setVisible(bool(view.adapter_status))
        self.invite_button.setVisible(view.can_invite)
        offline = view.presence is Presence.OFFLINE
        self.online_button.setText("Go Online" if offline else "Go Offline")
        set_style_name(self.online_button, "primary" if offline else "")
        self.online_button.setEnabled(view.steam_ready)
        self.member_count.setText(view.online_summary)
        if view.error:
            self.show_notice(view.error, error=True)
            self._error_shown = True
        elif self._error_shown:
            self.show_notice("")

        present = {member.steam_id for member in view.members}
        for steam_id in list(self._rows):
            if steam_id not in present:
                self._rows.pop(steam_id).deleteLater()
        for index, member in enumerate(view.members):
            row = self._rows.get(member.steam_id)
            if row is None:
                row = self._rows[member.steam_id] = MemberRow()
            self.members.insertWidget(index, row)
            row.show_member(member)

    def show_notice(self, text: str, error: bool = False) -> None:
        self._error_shown = False
        set_style_name(self.notice, "error" if error else "notice")
        self.notice.setText(text)
