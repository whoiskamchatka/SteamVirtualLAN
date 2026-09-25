BACKGROUND = "#111316"
SURFACE = "#181b1f"
RAISED = "#22262c"
HOVER = "#2a2f36"
LINE = "#2a2f36"
TEXT = "#e6e8eb"
MUTED = "#8b929a"
FAINT = "#5a6068"
ACCENT = "#3d7eff"
ACCENT_HOVER = "#528cff"
ACCENT_PRESSED = "#336fe6"
ACCENT_SOFT = "#1d2a40"
OK = "#3ecf8e"
PENDING = "#e0b341"
ERROR = "#f07171"

TONES = {"ok": OK, "pending": PENDING, "error": ERROR, "neutral": FAINT}

STYLE = f"""
* {{
    font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
    font-size: 13px;
    color: {TEXT};
}}
QWidget#root, QDialog {{ background: {BACKGROUND}; }}

QLabel#title {{ font-size: 22px; font-weight: 600; }}
QLabel#heading {{ font-size: 17px; font-weight: 600; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#caption {{ color: {MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 0.6px; }}
QLabel#error {{ color: {ERROR}; }}
QLabel#notice {{ color: {OK}; }}
QLabel#value {{ font-family: "Cascadia Mono", Consolas, monospace; font-size: 13px; }}
QLabel#memberName {{ font-weight: 600; }}
QLabel#memberStatus {{ color: {MUTED}; font-size: 12px; }}
QLabel#avatar {{
    background: {RAISED}; color: {MUTED}; border-radius: 16px;
    font-weight: 600; qproperty-alignment: AlignCenter;
}}
QLabel#badge {{
    background: {RAISED}; color: {MUTED}; border-radius: 5px;
    padding: 1px 6px; font-size: 11px; font-weight: 600;
}}
QLabel#badgeAccent {{
    background: {ACCENT_SOFT}; color: #8fb1ff; border-radius: 5px;
    padding: 1px 6px; font-size: 11px; font-weight: 600;
}}

QFrame#card {{ background: {SURFACE}; border-radius: 12px; }}
QFrame#divider {{ background: {LINE}; max-height: 1px; min-height: 1px; }}
QFrame#memberRow {{ background: transparent; border-radius: 8px; }}
QFrame#memberRow:hover {{ background: {RAISED}; }}

QPushButton {{
    background: {RAISED}; border: none; border-radius: 8px;
    padding: 10px 16px; font-weight: 600;
}}
QPushButton:hover {{ background: {HOVER}; }}
QPushButton:pressed {{ background: {SURFACE}; }}
QPushButton:disabled {{ background: {SURFACE}; color: {FAINT}; }}
QPushButton#primary {{ background: {ACCENT}; color: white; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#primary:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton#primary:disabled {{ background: #243653; color: #7f93b8; }}
QPushButton#danger {{ background: transparent; color: {ERROR}; }}
QPushButton#danger:hover {{ background: #2a1c1f; }}
QPushButton#link {{
    background: transparent; color: {MUTED}; padding: 4px 0; text-align: left;
}}
QPushButton#link:hover {{ color: {TEXT}; }}
QPushButton#small {{
    background: transparent; color: #8fb1ff; padding: 4px 10px; border-radius: 6px;
}}
QPushButton#small:hover {{ background: {ACCENT_SOFT}; }}

QLineEdit {{
    background: {SURFACE}; border: 1px solid {LINE}; border-radius: 8px;
    padding: 9px 12px; selection-background-color: {ACCENT};
}}
QLineEdit:focus {{ border-color: {ACCENT}; }}

QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 8px; }}
QScrollBar::handle:vertical {{ background: {RAISED}; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""
