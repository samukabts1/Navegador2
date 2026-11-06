#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Upgraded One Browser v5.3
==========================
Correções de crash:
- Interceptor anti-bot mantido vivo (antes era coletado pelo GC, causando
  access violation ao navegar) e corrigido para a API real do QtWebEngine 5.
- Removida a chamada a QWebEngineProfile.setOffTheRecord(), que não existe no
  Qt5 e derrubava o app ao abrir a janela anônima.
- Perfis do WebEngine agora vivem enquanto o QApplication viver (evita o
  crash "Release of profile requested but WebEnginePage still not deleted").
- Exceções não tratadas em slots/virtuais do Qt não abortam mais o processo
  (hook de exceção + guardas nos callbacks mais críticos).
- Conexões SQLite são fechadas corretamente (antes vazavam até travar o app).
- Abas/popups, downloads e fechamento de janela protegidos contra objetos já
  destruídos pelo lado C++.
"""

import os
import sys
import math
import json
import base64
import html as html_module
import hashlib
import sqlite3
import shutil
import datetime
import traceback
import urllib.parse
import ctypes
from ctypes import wintypes
from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse

os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
_extra_flags = (
    "--no-sandbox --disable-dev-shm-usage --ignore-certificate-errors "
    "--disable-features=IsolateOrigins,site-per-process,AutomationControlled "
    "--disable-blink-features=AutomationControlled "
    "--enable-features=OverlayScrollbar"
)
# Preserva flags definidas externamente pelo usuário.
_existing_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    f"{_existing_flags} {_extra_flags}".strip() if _existing_flags else _extra_flags
)

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTabWidget, QTabBar, QToolBar, QToolButton, QLineEdit, QComboBox, QMenu,
    QAction, QLabel, QPushButton, QMessageBox, QDialog, QScrollArea,
    QFileDialog, QProgressBar, QShortcut, QInputDialog,
    QColorDialog, QFrame, QListWidget, QListWidgetItem,
)
from PyQt5.QtCore import (
    Qt, QUrl, QTimer, pyqtSignal, QObject, QSize, QStandardPaths,
    QPointF, QRectF, QBuffer, QByteArray, QIODevice, QDateTime,
)
from PyQt5.QtGui import (
    QIcon, QPixmap, QColor, QPalette, QKeySequence, QDesktopServices,
    QPainter, QFont, QPen, QPainterPath, QPolygonF,
)
from PyQt5.QtNetwork import (
    QNetworkAccessManager, QNetworkRequest, QNetworkReply,
    QSslCertificate, QSslKey, QSsl,
)
from PyQt5.QtWebEngineWidgets import (
    QWebEngineView, QWebEnginePage, QWebEngineProfile,
    QWebEngineSettings, QWebEngineDownloadItem, QWebEngineScript,
)
from PyQt5.QtWebEngineCore import QWebEngineUrlRequestInterceptor

try:  # Qt 5.x expõe a classe de info em QtWebEngineCore
    from PyQt5.QtWebEngineCore import QWebEngineUrlRequestInfo
except ImportError:  # pragma: no cover - versões muito antigas
    QWebEngineUrlRequestInfo = None

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTES E HELPERS
# ═══════════════════════════════════════════════════════════════════════════════
APP_NAME = "Upgraded One Browser"
APP_VERSION = "5.3"
BASE_DIR = Path.home() / ".UpgradedOneBrowser"
PROFILES_DIR = BASE_DIR / "profiles"
# Ações das páginas internas. É um domínio https fictício porque o Chromium
# bloqueia navegação para esquemas desconhecidos (upgraded_one://); a navegação
# é interceptada antes de qualquer requisição de rede.
INTERNAL_ACTION_BASE = "https://upgradedone.internal/"


def _app_dir() -> Path:
    """Diretório dos recursos, tanto rodando do código quanto empacotado."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


ICON_FILE = str(_app_dir() / "justatest.jpg")
LOGO_FILE = str(_app_dir() / "justatest.jpg")
ICO_FILE = str(_app_dir() / "justatest.ico")


def app_icon() -> QIcon:
    """Ícone do aplicativo.

    O .ico é usado primeiro porque é o formato que o Windows mostra melhor na
    barra de tarefas e nos atalhos; o .jpg fica como reserva.
    """
    for path in (ICO_FILE, ICON_FILE, LOGO_FILE):
        try:
            if path and os.path.exists(path):
                icon = QIcon(path)
                if not icon.isNull():
                    return icon
        except (RuntimeError, ValueError):
            continue
    return QIcon()


def logo_pixmap(size: int) -> QPixmap:
    """Logo quadrada já redimensionada (ou vazia se nenhum arquivo existir)."""
    for path in (LOGO_FILE, ICO_FILE, ICON_FILE):
        try:
            if path and os.path.exists(path):
                pm = QPixmap(path)
                if not pm.isNull():
                    return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        except (RuntimeError, ValueError):
            continue
    return QPixmap()


def logo_data_uri() -> str:
    """Logo embutida em base64 para as páginas internas (HTML)."""
    for path in (LOGO_FILE, ICO_FILE, ICON_FILE):
        try:
            if not path or not os.path.exists(path):
                continue
            with open(path, "rb") as f:
                raw = f.read()
            if not raw:
                continue
            lower = path.lower()
            if lower.endswith(".png"):
                mime = "image/png"
            elif lower.endswith(".ico"):
                mime = "image/x-icon"
            elif lower.endswith(".gif"):
                mime = "image/gif"
            elif lower.endswith(".svg"):
                mime = "image/svg+xml"
            else:
                mime = "image/jpeg"
            return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))
        except (OSError, ValueError):
            continue
    return ""


def favicon_fallback_data_uri(label, domain=""):
    """Quadradinho colorido com a inicial do site.

    Usado como reserva quando o serviço de favicons não responde, para que o
    card nunca fique com a imagem "quebrada" (sem logo).
    """
    text = "".join(ch for ch in str(label or domain or "?") if ch.isalnum())
    letter = (text[:1] or "?").upper()
    palette = ("#ff2a5f", "#a855f7", "#3b82f6", "#10b981",
               "#f59e0b", "#ef4444", "#ec4899", "#6366f1")
    try:
        index = int(hashlib.md5(str(domain or label).encode("utf-8", "ignore")).hexdigest(), 16)
    except (TypeError, ValueError):
        index = 0
    color = palette[index % len(palette)]
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64'>"
        f"<rect width='64' height='64' rx='14' fill='{color}'/>"
        "<text x='32' y='43' font-family='Segoe UI,Arial,sans-serif' font-size='34'"
        f" font-weight='700' fill='#ffffff' text-anchor='middle'>{esc(letter)}</text>"
        "</svg>"
    )
    return "data:image/svg+xml;charset=utf-8," + urllib.parse.quote(svg)

DEFAULT_COLORS = {
    "dark":  {"accent": "#ff2a5f", "secondary": "#141721", "main": "#0b0c10", "home": "#0b0c10"},
    "light": {"accent": "#ff2a5f", "secondary": "#e4e6eb", "main": "#f0f2f5", "home": "#f0f2f5"},
}
PRIVATE_COLORS = {"accent": "#a855f7", "secondary": "#161124", "main": "#0b0c10", "home": "#090611"}

SEARCH_ENGINES = {
    "Google": "https://www.google.com/search?q={}",
    "DuckDuckGo": "https://duckduckgo.com/?q={}",
    "Bing": "https://www.bing.com/search?q={}",
    "Brave": "https://search.brave.com/search?q={}",
    "Ecosia": "https://www.ecosia.org/search?q={}",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
# O Google bloqueia o login em navegadores embutidos (QtWebEngine). Usar o
# mesmo User-Agent do Chrome nas páginas de autenticação ajuda a contornar o
# aviso "Esse navegador ou app pode não ser seguro".
GOOGLE_AUTH_HOSTS = {
    "accounts.google.com",
    "accounts.youtube.com",
    "myaccount.google.com",
    "gds.google.com",
    "ssl.gstatic.com",
    "www.gstatic.com",
}
GOOGLE_CHROME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.6478.127 Safari/537.36"
)
FIREFOX_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
    "Gecko/20100101 Firefox/128.0"
)
CERT_STORAGE_FILE = "certificates.json"


def shorten(text: str, limit: int) -> str:
    """Encurta um texto com '...' respeitando o limite."""
    text = "" if text is None else str(text)
    limit = int(limit)
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def is_valid_url(text: str) -> bool:
    """Valida se uma string possui formato de URL/Domínio válido."""
    if not isinstance(text, str) or not text:
        return False
    if any(c.isspace() for c in text):
        return False
    if text.startswith(("http://", "https://", "upgraded_one://", "data:", "file://")):
        return True
    if "." not in text:
        return False
    tld = text.rsplit(".", 1)[-1]
    return bool(tld) and all(c.isalnum() or c == "-" for c in tld)


def esc(text: str) -> str:
    return html_module.escape("" if text is None else str(text))


def safe_color(value, fallback: str) -> str:
    """Normaliza uma cor vinda do profile.json (que pode estar corrompido)."""
    try:
        color = QColor(value)
        if color.isValid():
            return color.name()
    except (TypeError, ValueError):
        pass
    return fallback


def rgba(color, alpha=1.0) -> str:
    """Converte uma cor para rgba() (usado no CSS das páginas internas)."""
    try:
        c = QColor(color)
        if c.isValid():
            return "rgba(%d,%d,%d,%s)" % (c.red(), c.green(), c.blue(), round(float(alpha), 3))
    except (TypeError, ValueError):
        pass
    return "rgba(0,0,0,%s)" % alpha


def theme_palette(accent="#ff2a5f", bg_secondary="#141721", bg_main="#0b0c10",
                  dark=True, is_private=False):
    """Cores derivadas do tema atual (usadas no CSS e nos ícones)."""
    accent = PRIVATE_COLORS["accent"] if is_private else safe_color(accent, "#ff2a5f")
    if dark:
        pal = {
            "accent": accent,
            "text": "#e6ebf5",
            "muted": "#8b92a5",
            "border": "#1e2230",
            "tab_bg": "#101320",
            "hover": "#1b2030",
            "titlebar": "#080a10",
            "input": "#0e1119",
            "danger": "#ef4444",
        }
    else:
        pal = {
            "accent": accent,
            "text": "#1b1f27",
            "muted": "#5f6673",
            "border": "#d6dae2",
            "tab_bg": "#e9ecf2",
            "hover": "#dfe3ec",
            "titlebar": "#eef1f5",
            "input": "#ffffff",
            "danger": "#dc2626",
        }
    pal["accent_soft"] = rgba(accent, 0.14)
    pal["accent_glow"] = rgba(accent, 0.35)
    pal["shadow"] = "rgba(0,0,0,0.45)" if dark else "rgba(20,26,40,0.16)"
    return pal


# ─── Ícones vetoriais (desenhados pelo Qt, sem emoji e sem arquivos) ───────────
_ICON_CACHE = {}


def _draw_icon(name, p, color):
    """Desenha o ícone ``name`` em um espaço de coordenadas 24x24."""
    qcolor = QColor(color)
    if name == "home":
        p.drawPolyline(QPolygonF([QPointF(3.6, 11.2), QPointF(12, 4), QPointF(20.4, 11.2)]))
        p.drawPolyline(QPolygonF([QPointF(6.2, 9.4), QPointF(6.2, 20), QPointF(17.8, 20), QPointF(17.8, 9.4)]))
        p.drawPolyline(QPolygonF([QPointF(10.1, 20), QPointF(10.1, 14.2),
                                  QPointF(13.9, 14.2), QPointF(13.9, 20)]))
    elif name == "back":
        p.drawPolyline(QPolygonF([QPointF(14.6, 5.2), QPointF(7.8, 12), QPointF(14.6, 18.8)]))
    elif name == "forward":
        p.drawPolyline(QPolygonF([QPointF(9.4, 5.2), QPointF(16.2, 12), QPointF(9.4, 18.8)]))
    elif name == "reload":
        p.drawArc(QRectF(4.4, 4.8, 15.2, 15.2), 70 * 16, 300 * 16)
        p.drawPolyline(QPolygonF([QPointF(12.6, 3.2), QPointF(16.9, 5.3), QPointF(14.8, 9.5)]))
    elif name in ("close", "stop"):
        p.drawLine(QPointF(6.2, 6.2), QPointF(17.8, 17.8))
        p.drawLine(QPointF(17.8, 6.2), QPointF(6.2, 17.8))
    elif name == "downloads":
        p.drawLine(QPointF(12, 3.8), QPointF(12, 14.6))
        p.drawPolyline(QPolygonF([QPointF(7.4, 10.2), QPointF(12, 14.8), QPointF(16.6, 10.2)]))
        p.drawLine(QPointF(4.8, 19.6), QPointF(19.2, 19.6))
    elif name == "history":
        p.drawEllipse(QPointF(12, 12), 7.8, 7.8)
        p.drawPolyline(QPolygonF([QPointF(12, 7.4), QPointF(12, 12.2), QPointF(15.6, 14.2)]))
    elif name == "star":
        points = []
        for i in range(10):
            angle = -math.pi / 2 + i * math.pi / 5
            radius = 8.2 if i % 2 == 0 else 3.6
            points.append(QPointF(12 + radius * math.cos(angle), 12 + radius * math.sin(angle)))
        p.drawPolygon(QPolygonF(points))
    elif name == "accent":
        path = QPainterPath(QPointF(12, 3.2))
        path.cubicTo(QPointF(12, 3.2), QPointF(5.8, 10.4), QPointF(5.8, 14.2))
        path.cubicTo(QPointF(5.8, 17.8), QPointF(8.6, 20.8), QPointF(12, 20.8))
        path.cubicTo(QPointF(15.4, 20.8), QPointF(18.2, 17.8), QPointF(18.2, 14.2))
        path.cubicTo(QPointF(18.2, 10.4), QPointF(12, 3.2), QPointF(12, 3.2))
        p.drawPath(path)
        p.drawLine(QPointF(9.3, 14.6), QPointF(9.3, 16.4))
    elif name == "panel":
        p.drawPolyline(QPolygonF([QPointF(12, 3.4), QPointF(20.2, 7.6),
                                  QPointF(12, 11.8), QPointF(3.8, 7.6), QPointF(12, 3.4)]))
        p.drawPolyline(QPolygonF([QPointF(3.8, 12.2), QPointF(12, 16.4), QPointF(20.2, 12.2)]))
        p.drawPolyline(QPolygonF([QPointF(3.8, 16.6), QPointF(12, 20.8), QPointF(20.2, 16.6)]))
    elif name == "background":
        p.drawRoundedRect(QRectF(3.4, 4.4, 17.2, 15.2), 2.6, 2.6)
        p.drawEllipse(QPointF(8.4, 9.4), 1.7, 1.7)
        p.drawPolyline(QPolygonF([QPointF(5.2, 17.4), QPointF(10, 12.4), QPointF(13, 15.4),
                                  QPointF(15.6, 12.8), QPointF(19, 17.4)]))
    elif name == "home-color":
        p.drawPolyline(QPolygonF([QPointF(3.4, 10.4), QPointF(10.2, 4.2), QPointF(17, 10.4)]))
        p.drawPolyline(QPolygonF([QPointF(5.4, 9.2), QPointF(5.4, 18.4),
                                  QPointF(15, 18.4), QPointF(15, 9.2)]))
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor)
        p.drawEllipse(QPointF(18.4, 17.6), 3.3, 3.3)
    elif name == "settings":
        p.drawEllipse(QPointF(12, 12), 3.3, 3.3)
        for i in range(8):
            angle = math.radians(i * 45)
            p.drawLine(
                QPointF(12 + 6.1 * math.cos(angle), 12 + 6.1 * math.sin(angle)),
                QPointF(12 + 8.7 * math.cos(angle), 12 + 8.7 * math.sin(angle)),
            )
    elif name == "incognito":
        p.drawEllipse(QPointF(7.6, 14.2), 3.4, 2.8)
        p.drawEllipse(QPointF(16.4, 14.2), 3.4, 2.8)
        p.drawLine(QPointF(11.0, 13.8), QPointF(13.0, 13.8))
        p.drawLine(QPointF(4.6, 9.2), QPointF(19.4, 9.2))
    elif name == "power":
        p.drawArc(QRectF(5, 4.6, 14, 14), 55 * 16, 250 * 16)
        p.drawLine(QPointF(12, 3.2), QPointF(12, 11.4))
    elif name == "moon":
        path = QPainterPath(QPointF(16.4, 3.4))
        path.cubicTo(QPointF(10.4, 4.6), QPointF(6.6, 8.6), QPointF(6.6, 13.2))
        path.cubicTo(QPointF(6.6, 17.6), QPointF(10.6, 21.0), QPointF(15.8, 20.8))
        path.cubicTo(QPointF(12.2, 18.0), QPointF(10.6, 14.0), QPointF(12.6, 10.0))
        path.cubicTo(QPointF(13.6, 7.4), QPointF(15.0, 5.0), QPointF(16.4, 3.4))
        p.drawPath(path)
    elif name == "sun":
        p.drawEllipse(QPointF(12, 12), 3.6, 3.6)
        for i in range(8):
            angle = math.radians(i * 45)
            p.drawLine(
                QPointF(12 + 6.4 * math.cos(angle), 12 + 6.4 * math.sin(angle)),
                QPointF(12 + 8.9 * math.cos(angle), 12 + 8.9 * math.sin(angle)),
            )
    elif name in ("search", "zoom-in", "zoom-out"):
        p.drawEllipse(QPointF(10.6, 10.6), 6.0, 6.0)
        p.drawLine(QPointF(15.2, 15.2), QPointF(20.2, 20.2))
        if name == "zoom-in":
            p.drawLine(QPointF(10.6, 7.9), QPointF(10.6, 13.3))
            p.drawLine(QPointF(7.9, 10.6), QPointF(13.3, 10.6))
        elif name == "zoom-out":
            p.drawLine(QPointF(7.9, 10.6), QPointF(13.3, 10.6))
    elif name == "fullscreen":
        p.drawPolyline(QPolygonF([QPointF(4, 9.2), QPointF(4, 4), QPointF(9.2, 4)]))
        p.drawPolyline(QPolygonF([QPointF(14.8, 4), QPointF(20, 4), QPointF(20, 9.2)]))
        p.drawPolyline(QPolygonF([QPointF(20, 14.8), QPointF(20, 20), QPointF(14.8, 20)]))
        p.drawPolyline(QPolygonF([QPointF(9.2, 20), QPointF(4, 20), QPointF(4, 14.8)]))
    elif name == "minimize":
        p.drawLine(QPointF(5.5, 16), QPointF(18.5, 16))
    elif name == "maximize":
        p.drawRoundedRect(QRectF(5.2, 5.2, 13.6, 13.6), 2.0, 2.0)
    elif name == "restore":
        p.drawRoundedRect(QRectF(4.4, 8.4, 11.2, 11.2), 2.0, 2.0)
        p.drawPolyline(QPolygonF([QPointF(8.2, 8.4), QPointF(8.2, 4.4),
                                  QPointF(19.6, 4.4), QPointF(19.6, 15.8), QPointF(15.6, 15.8)]))
    elif name == "plus":
        p.drawLine(QPointF(12, 5), QPointF(12, 19))
        p.drawLine(QPointF(5, 12), QPointF(19, 12))
    elif name == "trash":
        p.drawLine(QPointF(4.6, 7), QPointF(19.4, 7))
        p.drawPolyline(QPolygonF([QPointF(9, 7), QPointF(9, 4.6),
                                  QPointF(15, 4.6), QPointF(15, 7)]))
        p.drawPolyline(QPolygonF([QPointF(6.6, 7), QPointF(7.6, 19.6),
                                  QPointF(16.4, 19.6), QPointF(17.4, 7)]))
        p.drawLine(QPointF(10.4, 10.4), QPointF(10.7, 16.6))
        p.drawLine(QPointF(13.6, 10.4), QPointF(13.3, 16.6))
    elif name == "database":
        p.drawEllipse(QPointF(12, 6.4), 6.4, 2.6)
        p.drawLine(QPointF(5.6, 6.4), QPointF(5.6, 17.4))
        p.drawLine(QPointF(18.4, 6.4), QPointF(18.4, 17.4))
        p.drawArc(QRectF(5.6, 14.8, 12.8, 5.2), 180 * 16, 180 * 16)
        p.drawArc(QRectF(5.6, 10.6, 12.8, 5.2), 180 * 16, 180 * 16)
    elif name == "shield":
        path = QPainterPath(QPointF(12, 3.2))
        path.lineTo(19.6, 6.4)
        path.lineTo(19.6, 12.0)
        path.cubicTo(QPointF(19.6, 16.6), QPointF(16.2, 20.0), QPointF(12, 21.2))
        path.cubicTo(QPointF(7.8, 20.0), QPointF(4.4, 16.6), QPointF(4.4, 12.0))
        path.lineTo(4.4, 6.4)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "bookmarks":
        p.drawRoundedRect(QRectF(4.6, 3.6, 14.8, 16.8), 2.4, 2.4)
        p.drawLine(QPointF(8.4, 8.6), QPointF(15.6, 8.6))
        p.drawLine(QPointF(8.4, 12.4), QPointF(15.6, 12.4))
    elif name == "certificate":
        # Documento com selo/medalha: certificado digital.
        p.drawRoundedRect(QRectF(4.2, 3.4, 13.0, 12.0), 2.0, 2.0)
        p.drawLine(QPointF(7.2, 7.0), QPointF(14.2, 7.0))
        p.drawLine(QPointF(7.2, 9.8), QPointF(12.0, 9.8))
        p.drawEllipse(QPointF(16.3, 15.2), 3.2, 3.2)
        p.drawLine(QPointF(14.7, 17.9), QPointF(13.8, 21.2))
        p.drawLine(QPointF(17.9, 17.9), QPointF(18.8, 21.2))


def vector_icon(name, color="#e1e6f0", size=20) -> QIcon:
    """Ícone vetorial nítido (renderizado em 2x) para botões da interface."""
    key = (name, str(color).lower(), int(size))
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return QIcon(cached)

    scale = 2
    px = max(10, int(size) * scale)
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.scale(px / 24.0, px / 24.0)
        pen = QPen(QColor(color))
        pen.setWidthF(1.8)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        _draw_icon(name, painter, color)
    finally:
        painter.end()
    pm.setDevicePixelRatio(scale)
    _ICON_CACHE[key] = pm
    return QIcon(pm)


# ─── Ícones SVG para as páginas internas (HTML) ───────────────────────────────
HTML_ICON_PATHS = {
    "home": "<path d='M4 11l8-7 8 7'/><path d='M6.5 9.5V20h11V9.5'/><path d='M10 20v-6h4v6'/>",
    "history": "<circle cx='12' cy='12' r='7.8'/><path d='M12 7.4v4.8l3.6 2'/>",
    "star": ("<polygon points='12,3.6 14.6,9 20.4,9.8 16.2,13.9 17.2,19.7 12,16.9 "
             "6.8,19.7 7.8,13.9 3.6,9.8 9.4,9'/>"),
    "database": ("<ellipse cx='12' cy='6.4' rx='6.4' ry='2.6'/>"
                 "<path d='M5.6 6.4v11c0 1.4 2.9 2.6 6.4 2.6s6.4-1.2 6.4-2.6v-11'/>"
                 "<path d='M5.6 11.6c0 1.4 2.9 2.6 6.4 2.6s6.4-1.2 6.4-2.6'/>"),
    "trash": ("<path d='M4.6 7h14.8'/><path d='M9 7V4.6h6V7'/>"
              "<path d='M6.6 7l1 12.6h8.8L17.4 7'/>"
              "<path d='M10.4 10.4l.3 6.2'/><path d='M13.6 10.4l-.3 6.2'/>"),
    "palette": ("<path d='M12 3.2S5.8 10.4 5.8 14.2a6.2 6.2 0 0 0 12.4 0C18.2 10.4 12 3.2 12 3.2z'/>"
                "<path d='M9.3 14.6v1.8'/>"),
    "panel": ("<path d='M12 3.4l8.2 4.2L12 11.8 3.8 7.6 12 3.4z'/>"
              "<path d='M3.8 12.2L12 16.4l8.2-4.2'/><path d='M3.8 16.6L12 20.8l8.2-4.2'/>"),
    "image": ("<rect x='3.4' y='4.4' width='17.2' height='15.2' rx='2.6'/>"
              "<circle cx='8.4' cy='9.4' r='1.7'/>"
              "<path d='M5.2 17.4l4.8-5 3 3 2.6-2.6L19 17.4'/>"),
    "shield": ("<path d='M12 3.2l7.6 3.2V12c0 4.6-3.4 8-7.6 9.2C7.8 20 4.4 16.6 4.4 12V6.4L12 3.2z'/>"
               "<path d='M9 12l2.2 2.2 4-4.2'/>"),
    "certificate": ("<rect x='4.2' y='3.4' width='13' height='12' rx='2'/>"
                    "<path d='M7.2 7h7'/><path d='M7.2 9.8h4.8'/>"
                    "<circle cx='16.3' cy='15.2' r='3.2'/>"
                    "<path d='M14.7 17.9l-.9 3.3'/><path d='M17.9 17.9l.9 3.3'/>"),
    "settings": ("<circle cx='12' cy='12' r='3.3'/>"
                 "<path d='M12 5.9v-2'/><path d='M12 20.1v-2'/><path d='M5.9 12h-2'/>"
                 "<path d='M20.1 12h-2'/><path d='M7.7 7.7L6.3 6.3'/><path d='M17.7 17.7l-1.4-1.4'/>"
                 "<path d='M16.3 7.7l1.4-1.4'/><path d='M6.3 17.7l1.4-1.4'/>"),
    "sparkle": ("<path d='M12 3.5l1.9 5.1L19 10.5l-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9L12 3.5z'/>"),
    "search": "<circle cx='10.6' cy='10.6' r='6'/><path d='M15.2 15.2l5 5'/>",
    "moon": "<path d='M16.4 3.4a8.4 8.4 0 1 0 3.4 14.6A9.6 9.6 0 0 1 16.4 3.4z'/>",
    "sun": ("<circle cx='12' cy='12' r='4'/><path d='M12 2.6v2.2'/><path d='M12 19.2v2.2'/>"
            "<path d='M2.6 12h2.2'/><path d='M19.2 12h2.2'/><path d='M5.4 5.4l1.6 1.6'/>"
            "<path d='M17 17l1.6 1.6'/><path d='M18.6 5.4L17 7'/><path d='M7 17l-1.6 1.6'/>"),
    "download": ("<path d='M12 3.8v10.8'/><path d='M7.4 10.2L12 14.8l4.6-4.6'/>"
                 "<path d='M4.8 19.6h14.4'/>"),
}


def svg_icon(name, color="#e6ebf5", size=16) -> str:
    """Ícone SVG inline para usar nos botões das páginas internas."""
    body = HTML_ICON_PATHS.get(name, "")
    stroke = "none" if name == "star" else color
    fill = color if name == "star" else "none"
    return (
        f"<svg width='{size}' height='{size}' viewBox='0 0 24 24' fill='{fill}' "
        f"stroke='{stroke}' stroke-width='1.8' stroke-linecap='round' "
        f"stroke-linejoin='round' style='vertical-align:middle'>{body}</svg>"
    )


def install_exception_hook():
    """Registra um hook que apenas loga exceções.

    Sem isso, qualquer exceção Python não tratada dentro de um slot/virtual do
    Qt aciona qFatal() no PyQt5 e fecha o processo abruptamente.
    """
    log_path = BASE_DIR / "crash.log"

    def _hook(exc_type, exc_value, exc_tb):
        try:
            BASE_DIR.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.datetime.now().isoformat()}] ")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        except Exception:
            pass
        try:
            traceback.print_exception(exc_type, exc_value, exc_tb)
        except Exception:
            pass

    sys.excepthook = _hook


# ═══════════════════════════════════════════════════════════════════════════════
# GERENCIADOR DE PERFIS
# ═══════════════════════════════════════════════════════════════════════════════
class ProfileManager:
    def __init__(self):
        try:
            PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._profiles = self._scan()

    def _scan(self):
        profiles = {}
        try:
            folders = list(PROFILES_DIR.iterdir())
        except OSError:
            return profiles
        for folder in folders:
            try:
                cfg = folder / "profile.json"
                if not folder.is_dir() or not cfg.exists():
                    continue
                with open(cfg, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    continue
                name = data.get("name")
                if not isinstance(name, str) or not name.strip():
                    name = folder.name
                data["name"] = name
                data["folder"] = str(folder)
                profiles[name] = data
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                continue
        return profiles

    def list(self):
        return sorted(self._profiles.keys())

    def create(self, name: str) -> bool:
        name = (name or "").strip()
        if not name or name in self._profiles:
            return False
        folder = PROFILES_DIR / self._sanitize(name)
        try:
            folder_is_used = folder.exists() and (not folder.is_dir() or any(folder.iterdir()))
        except OSError:
            return False
        if folder_is_used:
            # Já existe conteúdo de outro perfil na mesma pasta sanitizada.
            folder = PROFILES_DIR / f"{self._sanitize(name)}_{int(datetime.datetime.now().timestamp())}"
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        data = {
            "name": name,
            "folder": str(folder),
            "created": datetime.datetime.now().isoformat(),
            "accent_color": DEFAULT_COLORS["dark"]["accent"],
            "bg_secondary": DEFAULT_COLORS["dark"]["secondary"],
            "bg_main": DEFAULT_COLORS["dark"]["main"],
            "bg_home": DEFAULT_COLORS["dark"]["home"],
            "dark_mode": True,
            "search_engine": "Google",
            "homepage": "upgraded_one://home",
            "block_popups": True,
        }
        self._save(data)
        self._profiles[name] = data
        return True

    def delete(self, name: str) -> bool:
        if name not in self._profiles:
            return False
        folder_raw = self._profiles[name].get("folder")
        if isinstance(folder_raw, str) and folder_raw:
            try:
                folder = Path(folder_raw).resolve()
                root = PROFILES_DIR.resolve()
                # Só apaga pastas realmente dentro de PROFILES_DIR.
                if folder != root and root in folder.parents and folder.is_dir():
                    shutil.rmtree(folder, ignore_errors=True)
            except (OSError, ValueError, RuntimeError):
                pass
        del self._profiles[name]
        return True

    def get(self, name: str):
        return self._profiles.get(name, {})

    def update(self, name: str, key: str, value):
        if name in self._profiles:
            self._profiles[name][key] = value
            self._save(self._profiles[name])

    def _save(self, data: dict):
        try:
            folder = data.get("folder")
            if not folder:
                return
            cfg = Path(folder) / "profile.json"
            cfg.parent.mkdir(parents=True, exist_ok=True)
            tmp = cfg.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, cfg)
        except (OSError, TypeError, ValueError):
            pass

    @staticmethod
    def _sanitize(name: str) -> str:
        return "".join(c if c.isalnum() or c in "-_ " else "_" for c in str(name)).strip() or "perfil"


# ═══════════════════════════════════════════════════════════════════════════════
# BANCO DE DADOS LOCAL
# ═══════════════════════════════════════════════════════════════════════════════
class LocalDatabase:
    def __init__(self, profile_folder: Path):
        self.db_path = Path(profile_folder) / "browser_data.db"
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._init()

    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self):
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        url TEXT NOT NULL,
                        title TEXT,
                        visit_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )""")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS bookmarks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        url TEXT NOT NULL UNIQUE,
                        title TEXT,
                        folder TEXT DEFAULT 'Geral',
                        added_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )""")
        except sqlite3.Error:
            pass

    def add_history(self, url, title):
        if not url or url.startswith(("upgraded_one://", "data:", "file://", "about:")):
            return
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute("INSERT INTO history (url, title) VALUES (?, ?)", (url, title or url))
        except sqlite3.Error:
            pass

    def get_history(self, limit=200):
        try:
            with closing(self._connect()) as conn:
                cur = conn.execute("SELECT * FROM history ORDER BY visit_time DESC LIMIT ?", (limit,))
                return [dict(r) for r in cur.fetchall()]
        except sqlite3.Error:
            return []

    def clear_history(self):
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute("DELETE FROM history")
        except sqlite3.Error:
            pass

    def add_bookmark(self, url, title, folder="Geral"):
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute(
                    "INSERT OR REPLACE INTO bookmarks (url, title, folder) VALUES (?, ?, ?)",
                    (url, title or url, folder),
                )
        except sqlite3.Error:
            pass

    def remove_bookmark(self, url):
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute("DELETE FROM bookmarks WHERE url = ?", (url,))
        except sqlite3.Error:
            pass

    def get_bookmarks(self):
        try:
            with closing(self._connect()) as conn:
                cur = conn.execute("SELECT * FROM bookmarks ORDER BY added_time DESC")
                return [dict(r) for r in cur.fetchall()]
        except sqlite3.Error:
            return []


class FaviconCache(QObject):
    """Busca o favicon de um domínio e guarda em disco/memória.

    Os ícones são baixados uma única vez (ficam em ~/.UpgradedOneBrowser/favicons)
    para que a barra de favoritos e os cards da Home sempre tenham logo, mesmo
    quando o site demora ou o serviço de favicons está lento.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.folder = BASE_DIR / "favicons"
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.folder = None
        self._manager = QNetworkAccessManager(self)
        self._memory = {}
        self._callbacks = {}
        self._fetching = set()
        self._failed = set()

    @staticmethod
    def _key(domain: str) -> str:
        return hashlib.md5(domain.encode("utf-8", "ignore")).hexdigest()

    def icon(self, domain, callback=None) -> QIcon:
        """Devolve o ícone em cache (se houver) e agenda o download quando falta."""
        domain = (domain or "").strip().lower()
        if not domain:
            return QIcon()
        cached = self._memory.get(domain)
        if cached is not None:
            return QIcon(cached)
        if self.folder is not None:
            path = self.folder / (self._key(domain) + ".png")
            try:
                if path.exists():
                    pm = QPixmap(str(path))
                    if not pm.isNull():
                        icon = QIcon(pm)
                        self._memory[domain] = icon
                        return QIcon(icon)
            except (OSError, RuntimeError):
                pass
        if callback is not None:
            self._callbacks.setdefault(domain, []).append(callback)
        self._fetch(domain)
        return QIcon()

    def _fetch(self, domain):
        if domain in self._fetching or domain in self._failed:
            return
        self._fetching.add(domain)
        request = QNetworkRequest(QUrl(f"https://icons.duckduckgo.com/ip3/{domain}.ico"))
        request.setAttribute(QNetworkRequest.FollowRedirectsAttribute, True)
        request.setRawHeader(b"User-Agent", b"Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        reply = self._manager.get(request)
        reply.finished.connect(lambda r=reply, d=domain: self._on_finished(r, d))

    def _on_finished(self, reply, domain):
        icon = QIcon()
        try:
            if reply.error() == QNetworkReply.NoError:
                data = bytes(reply.readAll())
                pm = QPixmap()
                if data and pm.loadFromData(data):
                    icon = QIcon(pm)
                    if self.folder is not None:
                        try:
                            pm.save(str(self.folder / (self._key(domain) + ".png")), "PNG")
                        except (OSError, RuntimeError):
                            pass
        except (RuntimeError, AttributeError):
            pass
        finally:
            try:
                reply.deleteLater()
            except RuntimeError:
                pass
            self._fetching.discard(domain)

        if icon.isNull():
            self._failed.add(domain)
        else:
            self._memory[domain] = icon

        pending = self._callbacks.pop(domain, [])
        for callback in pending:
            try:
                callback(QIcon(icon) if not icon.isNull() else QIcon())
            except (RuntimeError, TypeError):
                pass

    def forget_all(self):
        """Esquece os ícones baixados (usado ao limpar o cache)."""
        self._memory.clear()
        self._failed.clear()
        try:
            if self.folder is not None:
                shutil.rmtree(self.folder, ignore_errors=True)
                self.folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# INTERCEPTOR ANTI-BOT
# ═══════════════════════════════════════════════════════════════════════════════
class AntiBotInterceptor(QWebEngineUrlRequestInterceptor):
    """Ajusta cabeçalhos das requisições de navegação.

    Importante: a instância precisa ficar viva enquanto o perfil existir. Se
    ela for coletada pelo garbage collector, o próximo acesso a partir do
    Chromium derruba o processo (access violation). Ver MainWindow.__init__.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

    def interceptRequest(self, info):
        try:
            # Qt 5 não tem requestType(); o tipo de recurso é resourceType().
            is_main = True
            resource_type = getattr(info, "resourceType", None)
            main_frame = getattr(QWebEngineUrlRequestInfo, "ResourceTypeMainFrame", None) \
                if QWebEngineUrlRequestInfo is not None else None
            if callable(resource_type) and main_frame is not None:
                is_main = resource_type() == main_frame
            if not is_main:
                return
            info.setHttpHeader(b"Accept-Language", b"pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7")
            info.setHttpHeader(b"DNT", b"1")
            info.setHttpHeader(b"Upgrade-Insecure-Requests", b"1")
            # Login do Google: apresentar-se como Chrome evita o bloqueio
            # "Esse navegador ou app pode não ser seguro" aplicado a WebViews.
            try:
                host = info.requestUrl().host().lower()
            except (AttributeError, RuntimeError):
                host = ""
            if host in GOOGLE_AUTH_HOSTS or host.endswith(".google.com"):
                info.setHttpHeader(b"User-Agent", GOOGLE_CHROME_USER_AGENT.encode("ascii"))
                info.setHttpHeader(b"Accept-Language", b"pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7")
                info.setHttpHeader(b"Sec-Ch-Ua", b"\"Not/A)Brand\";v=\"8\", \"Chromium\";v=\"126\", \"Google Chrome\";v=\"126\"")
                info.setHttpHeader(b"Sec-Ch-Ua-Mobile", b"?0")
                info.setHttpHeader(b"Sec-Ch-Ua-Platform", b"\"Windows\"")
        except Exception:
            # Nunca deixar uma exceção escapar de um virtual do Qt.
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# GERENCIADOR DE DOWNLOADS
# ═══════════════════════════════════════════════════════════════════════════════
class DownloadItem(QWidget):
    def __init__(self, download, parent=None):
        super().__init__(parent)
        self.download = download
        self._path = ""
        self._filename = ""
        try:
            self._filename = download.suggestedFileName() or "arquivo"
        except (RuntimeError, AttributeError):
            self._filename = "arquivo"

        self.setFixedHeight(56)
        self.setStyleSheet("background: #141721; border-radius: 8px;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)

        self.info = QLabel(f"{self._filename}\nIniciando...")
        self.info.setStyleSheet("color: #e1e6f0; font-size: 12px; background: transparent;")
        layout.addWidget(self.info, stretch=1)

        self.btn_open = QPushButton("Abrir")
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._open_file)
        layout.addWidget(self.btn_open)

        self.btn_folder = QPushButton("Pasta")
        self.btn_folder.setEnabled(False)
        self.btn_folder.clicked.connect(self._open_folder)
        layout.addWidget(self.btn_folder)

        try:
            download.downloadProgress.connect(self.on_progress)
            download.finished.connect(self.on_finished)
        except (RuntimeError, AttributeError):
            self.info.setText(f"{self._filename}\nCancelado/Falhou")

    def _current_path(self):
        if self._path:
            return self._path
        try:
            self._path = self.download.path() or ""
        except (RuntimeError, AttributeError):
            self._path = ""
        return self._path

    def _open_file(self):
        path = self._current_path()
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_folder(self):
        path = self._current_path()
        folder = os.path.dirname(path) if path else ""
        if folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def on_progress(self, received, total):
        pct = int((received / total) * 100) if total > 0 else 0
        try:
            self.info.setText(f"{self._filename}\n{pct}%")
        except RuntimeError:
            pass

    def on_finished(self):
        try:
            state = self.download.state()
            self._path = self.download.path() or ""
        except (RuntimeError, AttributeError):
            return
        try:
            if state == QWebEngineDownloadItem.DownloadCompleted:
                self.btn_open.setEnabled(True)
                self.btn_folder.setEnabled(True)
                self.info.setText(f"{self._filename}\nConcluido")
            else:
                self.info.setText(f"{self._filename}\nCancelado/Falhou")
        except RuntimeError:
            pass


class DownloadManager(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self.setWindowTitle("Downloads")
        self.setMinimumSize(500, 400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QWidget()
        header.setFixedHeight(50)
        header.setStyleSheet("background: #07080a; border-bottom: 1px solid #1a1c23;")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 0, 16, 0)
        title = QLabel("Downloads")
        title.setStyleSheet("color: #e1e6f0; font-size: 16px; font-weight: 700; background: transparent;")
        hl.addWidget(title)
        hl.addStretch()
        clear_btn = QPushButton("Limpar Concluidos")
        clear_btn.clicked.connect(self.clear_completed)
        hl.addWidget(clear_btn)
        layout.addWidget(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: #0b0c10; border: none;")
        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setAlignment(Qt.AlignTop)
        self.container_layout.setSpacing(8)
        self.container_layout.setContentsMargins(12, 12, 12, 12)
        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll)

    def add_download(self, download):
        item = DownloadItem(download)
        self.container_layout.addWidget(item)
        self.show()
        self.raise_()
        self.activateWindow()

    def clear_completed(self):
        for i in reversed(range(self.container_layout.count())):
            w = self.container_layout.itemAt(i).widget()
            if isinstance(w, DownloadItem) and "Concluido" in w.info.text():
                w.deleteLater()


# ═══════════════════════════════════════════════════════════════════════════════
# PAGINAS INTERNAS (HTML)
# ═══════════════════════════════════════════════════════════════════════════════
class InternalPages:
    @staticmethod
    def _base_style(dark=True):
        return {
            "text": "#e6ebf5" if dark else "#1b1f27",
            "muted": "#8b92a5" if dark else "#5f6673",
            "th_bg": "#0e1017" if dark else "#d8dce3",
            "border": "#1e2230" if dark else "#d6dae2",
        }

    @staticmethod
    def _page_css(colors, dark=True):
        st = InternalPages._base_style(dark)
        a = colors["accent"]
        s = colors["bg_secondary"]
        m = colors["bg_main"]
        text, muted, border = st["text"], st["muted"], st["border"]
        danger = "#ef4444" if dark else "#dc2626"
        card_bg = "rgba(255,255,255,0.04)" if dark else "#ffffff"
        table_head = rgba(s, 0.75) if dark else "#e9ecf2"
        hover_row = rgba(a, 0.08)
        return f"""
*{{box-sizing:border-box}}
html,body{{margin:0;padding:0}}
body{{
    min-height:100vh;color:{text};
    font-family:'Segoe UI',system-ui,-apple-system,sans-serif;
    background:
        radial-gradient(900px 420px at 50% -12%, {rgba(a, 0.13)}, transparent 70%),
        radial-gradient(700px 420px at 100% 110%, {rgba(a, 0.08)}, transparent 65%),
        {m};
    background-attachment:fixed;
    padding:38px 44px 64px;
    font-size:14px;
}}
.page{{max-width:1000px;margin:0 auto}}
.header{{display:flex;align-items:center;justify-content:space-between;gap:18px;flex-wrap:wrap;margin-bottom:24px}}
.title-wrap{{display:flex;align-items:center;gap:14px;min-width:0}}
.title-icon{{
    width:46px;height:46px;border-radius:14px;flex:none;
    display:flex;align-items:center;justify-content:center;
    background:{rgba(a, 0.13)};border:1px solid {rgba(a, 0.35)};color:{a};
}}
h1{{font-size:25px;margin:0;letter-spacing:-0.4px;font-weight:700}}
.subtitle{{color:{muted};font-size:12.5px;margin:4px 0 0}}
.actions{{display:flex;gap:9px;flex-wrap:wrap}}
.btn{{
    display:inline-flex;align-items:center;gap:8px;
    background:{table_head};color:{text};border:1px solid {border};
    padding:10px 15px;border-radius:10px;font-size:12.5px;font-weight:600;
    text-decoration:none;cursor:pointer;transition:all .18s ease;
}}
.btn:hover{{border-color:{a};transform:translateY(-1px);box-shadow:0 8px 20px {rgba(a, 0.18)}}}
.btn.primary{{background:{a};border-color:{a};color:#ffffff}}
.btn.danger{{background:{rgba(danger, 0.12)};border-color:{rgba(danger, 0.4)};color:{danger}}}
.btn.danger:hover{{background:{danger};border-color:{danger};color:#ffffff;box-shadow:0 8px 20px {rgba(danger, 0.3)}}}
.table-wrap{{background:{card_bg};border:1px solid {border};border-radius:16px;overflow:hidden}}
table{{width:100%;border-collapse:collapse}}
th,td{{padding:13px 18px;text-align:left;font-size:13px;border-bottom:1px solid {border}}}
th{{background:{table_head};color:{muted};font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;font-weight:700}}
tbody tr:last-child td{{border-bottom:none}}
tbody tr{{transition:background .15s ease}}
tbody tr:hover td{{background:{hover_row}}}
td a{{color:{text};text-decoration:none;font-weight:600}}
td a:hover{{color:{a}}}
.td-url{{color:{muted};max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.td-date{{color:{muted};white-space:nowrap}}
.empty{{padding:48px 20px;text-align:center;color:{muted};font-size:13.5px;line-height:1.6}}
.empty svg{{display:block;margin:0 auto 12px;opacity:.7}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px}}
.panel{{background:{card_bg};border:1px solid {border};border-radius:16px;padding:20px 22px}}
.panel h3{{margin:0 0 12px;font-size:14.5px;display:flex;align-items:center;gap:9px;color:{text};font-weight:700}}
.panel p{{margin:7px 0;color:{muted};font-size:13px;line-height:1.55}}
.kv{{display:flex;justify-content:space-between;gap:14px;padding:8px 0;border-bottom:1px dashed {border};font-size:13px;color:{muted}}}
.kv:last-child{{border-bottom:none}}
.kv b{{color:{text};font-weight:600}}
.swatches{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:8px}}
.swatch{{
    display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:11px;
    border:1px solid {border};background:{rgba(text, 0.03)};color:{text};
    text-decoration:none;font-size:12px;font-weight:600;transition:all .18s ease;
}}
.swatch:hover{{border-color:{a};transform:translateY(-1px);box-shadow:0 6px 16px {rgba(a, 0.16)}}}
.dot{{width:18px;height:18px;border-radius:6px;border:1px solid rgba(255,255,255,.22);flex:none}}
.code{{
    background:{table_head};border:1px solid {border};border-radius:8px;padding:8px 11px;
    font-family:'Consolas','Cascadia Mono',monospace;font-size:11.5px;color:{muted};
    display:block;margin-top:8px;white-space:pre-wrap;
}}
::-webkit-scrollbar{{width:10px;height:10px}}
::-webkit-scrollbar-track{{background:transparent}}
::-webkit-scrollbar-thumb{{background:{border};border-radius:5px}}
::-webkit-scrollbar-thumb:hover{{background:{a}}}
"""

    @staticmethod
    def _page(title, colors, dark, content, extra_css=""):
        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{esc(title)} — Upgraded One</title>
<style>{InternalPages._page_css(colors, dark)}{extra_css}</style></head>
<body><div class="page">{content}</div></body></html>"""

    @staticmethod
    def _card_html(url, domain, name):
        """Card da Home com favicon remoto e reserva local.

        O ``onerror`` troca o favicon quebrado por um selo com a inicial do
        site, então o card nunca aparece sem "logo".
        """
        fallback = favicon_fallback_data_uri(name, domain)
        favicon = "https://icons.duckduckgo.com/ip3/%s.ico" % domain
        return (
            '<a class="card" href="%s" title="%s">'
            '<img src="%s" alt="" onerror="this.onerror=null;this.src=\'%s\';">'
            "<span>%s</span></a>"
        ) % (esc(url), esc(name), esc(favicon), fallback, esc(name))

    @staticmethod
    def speed_dial(colors, is_private, bookmarks, search_template, dark=True):
        a = colors["accent"]
        s = colors["bg_secondary"]
        h = colors["bg_home"]
        text = "#e6ebf5" if dark else "#1b1f27"
        muted = "#8b92a5" if dark else "#5f6673"
        border = "rgba(255,255,255,0.10)" if dark else "rgba(20,26,40,0.12)"
        card_bg = "rgba(255,255,255,0.055)" if dark else "rgba(255,255,255,0.85)"
        action = search_template.split("{}")[0].rstrip("?=&")

        sites = [
            ("https://www.youtube.com", "youtube.com", "YouTube"),
            ("https://www.twitch.tv", "twitch.tv", "Twitch"),
            ("https://discord.com/app", "discord.com", "Discord"),
            ("https://www.google.com", "google.com", "Google"),
            ("https://github.com", "github.com", "GitHub"),
            ("https://reddit.com", "reddit.com", "Reddit"),
        ]
        entries = []
        for b in bookmarks:
            burl = (b.get("url") or "").strip()
            if not burl:
                continue
            domain = urllib.parse.urlparse(burl).netloc or burl
            entries.append((burl, domain, shorten(b.get("title") or domain, 14)))
        entries += sites

        cards = ""
        seen = set()
        for burl, domain, name in entries:
            key = domain.lower()
            if key in seen or len(seen) >= 12:
                continue
            seen.add(key)
            cards += InternalPages._card_html(burl, domain, name)

        logo = logo_data_uri()
        if logo:
            logo_html = f"<img src='{logo}' alt='Upgraded One'>"
        else:
            logo_html = svg_icon("sparkle", "#ffffff", 40)

        badge = ""
        if is_private:
            badge = (
                f"<div class='badge'>{svg_icon('shield', 'currentColor', 13)}"
                "Modo anonimo &mdash; nada sera salvo</div>"
            )

        content = f"""
<div class="hero">
    <div class="brand-logo">{logo_html}</div>
    <h1 class="brand">Upgraded One</h1>
    <p class="tagline">Rapido, bonito e do seu jeito. Pesquise ou digite um endereco.</p>
    {badge}
    <form class="search" action="{esc(action)}" method="get">
        {svg_icon('search', muted, 20)}
        <input type="text" name="q" placeholder="Pesquisar na web ou digitar um endereco..."
               autofocus autocomplete="off" spellcheck="false">
    </form>
    <div class="grid">{cards}</div>
    <div class="hints">
        <span><b>Ctrl+T</b>nova aba</span>
        <span><b>Ctrl+H</b>historico</span>
        <span><b>Ctrl+Shift+O</b>favoritos</span>
        <span><b>Ctrl+,</b>configuracoes</span>
    </div>
</div>
"""
        extra = f"""
body{{
    display:flex;align-items:center;justify-content:center;
    padding:48px 32px;
    background:
        radial-gradient(620px 320px at 50% 0%, {rgba(a, 0.18)}, transparent 72%),
        radial-gradient(520px 320px at 88% 98%, {rgba(a, 0.10)}, transparent 70%),
        {h} !important;
}}
.page{{width:100%;max-width:none}}
.hero{{width:100%;max-width:760px;margin:0 auto;text-align:center}}
.brand-logo{{
    width:84px;height:84px;margin:0 auto 18px;border-radius:24px;overflow:hidden;
    display:flex;align-items:center;justify-content:center;
    background:{rgba(a, 0.13)};border:1px solid {rgba(a, 0.38)};
    box-shadow:0 18px 44px {rgba(a, 0.26)};
}}
.brand-logo img{{width:84px;height:84px;object-fit:cover;display:block}}
.brand-logo svg{{color:#ffffff}}
.brand{{
    font-size:42px;font-weight:800;letter-spacing:-1.2px;margin:0;padding:0 6px;
    color:{text};
    background:linear-gradient(92deg,{text} 0%,{a} 58%,{rgba(a, 0.78)} 100%);
    -webkit-background-clip:text;background-clip:text;
    -webkit-text-fill-color:transparent;
}}
.tagline{{color:{muted};font-size:14px;margin:10px 0 0}}
.badge{{
    display:inline-flex;align-items:center;gap:7px;margin-top:14px;
    padding:6px 13px;border-radius:999px;font-size:12px;font-weight:600;
    color:{a};background:{rgba(a, 0.12)};border:1px solid {rgba(a, 0.34)};
}}
.search{{
    display:flex;align-items:center;gap:11px;margin:30px auto 0;max-width:600px;
    height:54px;padding:0 20px;border-radius:17px;background:{s};
    border:1px solid {border};box-shadow:0 14px 34px rgba(0,0,0,0.22);
    transition:border-color .2s ease, box-shadow .2s ease, transform .2s ease;
}}
.search:focus-within{{
    border-color:{a};box-shadow:0 16px 40px {rgba(a, 0.26)};
    transform:translateY(-1px);
}}
.search input{{
    flex:1;height:100%;background:transparent;border:none;outline:none;
    color:{text};font-size:15px;
}}
.search input::placeholder{{color:{muted}}}
.grid{{
    display:grid;grid-template-columns:repeat(auto-fit,86px);justify-content:center;
    gap:14px;margin-top:36px;
}}
.card{{
    width:86px;height:92px;padding:0;display:flex;flex-direction:column;
    align-items:center;justify-content:center;gap:9px;border-radius:18px;
    text-decoration:none;color:{text};font-size:11.5px;font-weight:600;
    background:{card_bg};border:1px solid {border};
    transition:transform .18s ease, border-color .18s ease, box-shadow .18s ease;
}}
.card:hover{{
    transform:translateY(-4px);border-color:{rgba(a, 0.55)};
    box-shadow:0 14px 30px {rgba(a, 0.20)};
}}
.card img{{width:30px;height:30px;border-radius:9px;object-fit:contain}}
.card span{{max-width:72px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.hints{{
    display:flex;flex-wrap:wrap;justify-content:center;gap:8px 18px;
    margin-top:34px;color:{muted};font-size:11.5px;
}}
.hints span{{display:inline-flex;align-items:center}}
.hints b{{
    color:{text};background:{card_bg};border:1px solid {border};border-radius:6px;
    padding:2px 6px;font-size:10.5px;margin-right:6px;font-weight:700;
}}
"""
        return InternalPages._page("Home", colors, dark, content, extra)

    @staticmethod
    def privacy_buttons(prefix=""):
        """Botoes de limpeza de cache/historico.

        Apontam para https://upgradedone.internal/... e a navegacao e
        interceptada em CustomWebEnginePage.acceptNavigationRequest (links para
        esquemas desconhecidos, como upgraded_one://, sao bloqueados pelo
        Chromium e por isso nao funcionavam).
        """
        del prefix  # mantido por compatibilidade com chamadas antigas
        return (
            f"<a class='btn' href='{INTERNAL_ACTION_BASE}clear-cache'>"
            f"{svg_icon('database', 'currentColor', 15)}<span>Limpar cache</span></a>"
            f"<a class='btn' href='{INTERNAL_ACTION_BASE}clear-history'>"
            f"{svg_icon('history', 'currentColor', 15)}<span>Limpar historico</span></a>"
            f"<a class='btn danger' href='{INTERNAL_ACTION_BASE}clear-all'>"
            f"{svg_icon('trash', 'currentColor', 15)}<span>Limpar tudo</span></a>"
        )

    @staticmethod
    def history_page(colors, history, dark=True):
        st = InternalPages._base_style(dark)
        a = colors["accent"]
        if not history:
            rows = (
                "<tr><td colspan='3'><div class='empty'>"
                f"{svg_icon('history', st['muted'], 36)}"
                "Nenhum historico por aqui.<br>"
                "As paginas que voce visitar aparecem nesta lista.</div></td></tr>"
            )
        else:
            rows = ""
            for hrow in history:
                title = esc(shorten(hrow.get("title") or "Sem titulo", 70))
                url = esc(hrow.get("url", ""))
                rows += (
                    f"<tr><td><a href='{url}'>{title}</a></td>"
                    f"<td class='td-url'>{url}</td>"
                    f"<td class='td-date'>{esc(hrow.get('visit_time', ''))}</td></tr>"
                )
        content = f"""
<div class="header">
    <div class="title-wrap">
        <div class="title-icon">{svg_icon('history', a, 22)}</div>
        <div>
            <h1>Historico</h1>
            <p class="subtitle">{len(history)} pagina(s) registradas</p>
        </div>
    </div>
    <div class="actions">{InternalPages.privacy_buttons()}</div>
</div>
<div class="table-wrap">
    <table>
        <thead><tr><th>Titulo</th><th>URL</th><th>Data</th></tr></thead>
        <tbody>{rows}</tbody>
    </table>
</div>
"""
        return InternalPages._page("Historico", colors, dark, content)

    @staticmethod
    def bookmarks_page(colors, bookmarks, dark=True):
        st = InternalPages._base_style(dark)
        a = colors["accent"]
        if not bookmarks:
            rows = (
                "<tr><td colspan='3'><div class='empty'>"
                f"{svg_icon('star', st['muted'], 36)}"
                "Nenhum favorito ainda.<br>"
                "Use Ctrl+D para guardar a pagina atual.</div></td></tr>"
            )
        else:
            rows = ""
            for b in bookmarks:
                title = esc(shorten(b.get("title") or "Sem titulo", 70))
                url = esc(b.get("url", ""))
                rows += (
                    f"<tr><td><a href='{url}'>{title}</a></td>"
                    f"<td class='td-url'>{url}</td>"
                    f"<td>{esc(b.get('folder', 'Geral'))}</td></tr>"
                )
        content = f"""
<div class="header">
    <div class="title-wrap">
        <div class="title-icon">{svg_icon('star', a, 21)}</div>
        <div>
            <h1>Favoritos</h1>
            <p class="subtitle">{len(bookmarks)} link(s) salvos</p>
        </div>
    </div>
</div>
<div class="table-wrap">
    <table>
        <thead><tr><th>Titulo</th><th>URL</th><th>Pasta</th></tr></thead>
        <tbody>{rows}</tbody>
    </table>
</div>
"""
        return InternalPages._page("Favoritos", colors, dark, content)

    @staticmethod
    def settings_page(colors, profile_data, dark=True, cert_count=0, cert_active=""):
        a = colors["accent"]
        theme_label = "Escuro" if dark else "Claro"
        cert_label = (
            "Nenhum certificado" if not cert_count
            else f"{cert_count} certificado" + ("s" if cert_count > 1 else "")
        )
        cert_active_row = (
            f"<div class=\"kv\"><span>Ativo</span><b>{esc(cert_active)}</b></div>"
            if cert_active else ""
        )
        swatches = "".join(
            f"<a class='swatch' href='{INTERNAL_ACTION_BASE}color/{key}'>"
            f"<span class='dot' style='background:{value}'></span>{label}</a>"
            for key, label, value in (
                ("accent", "Destaque", colors["accent"]),
                ("panel", "Paineis", colors["bg_secondary"]),
                ("main", "Fundo", colors["bg_main"]),
                ("home", "Home", colors["bg_home"]),
            )
        )
        content = f"""
<div class="header">
    <div class="title-wrap">
        <div class="title-icon">{svg_icon('settings', a, 22)}</div>
        <div>
            <h1>Configuracoes</h1>
            <p class="subtitle">Aparencia, dados e informacoes do perfil</p>
        </div>
    </div>
    <div class="actions">
        <a class="btn" href="{INTERNAL_ACTION_BASE}theme">
            {svg_icon('sun' if dark else 'moon', 'currentColor', 15)}
            <span>Usar tema {'claro' if dark else 'escuro'}</span>
        </a>
    </div>
</div>
<div class="grid">
    <div class="panel">
        <h3>{svg_icon('shield', 'currentColor', 16)}Perfil atual</h3>
        <div class="kv"><span>Nome</span><b>{esc(profile_data.get('name', 'Padrao'))}</b></div>
        <div class="kv"><span>Busca</span><b>{esc(profile_data.get('search_engine', 'Google'))}</b></div>
        <div class="kv"><span>Pop-ups</span><b>{'Bloqueados' if profile_data.get('block_popups', True) else 'Permitidos'}</b></div>
    </div>
    <div class="panel">
        <h3>{svg_icon('palette', 'currentColor', 16)}Aparencia</h3>
        <div class="kv"><span>Tema</span><b>{theme_label}</b></div>
        <p>Clique em uma cor para personalizar todo o navegador.</p>
        <div class="swatches">{swatches}</div>
    </div>
    <div class="panel">
        <h3>{svg_icon('database', 'currentColor', 16)}Dados e privacidade</h3>
        <p>Apague os arquivos temporarios e o historico deste perfil.</p>
        <div class="actions">{InternalPages.privacy_buttons()}</div>
    </div>
    <div class="panel">
        <h3>{svg_icon('certificate', 'currentColor', 16)}Certificado digital</h3>
        <div class="kv"><span>Importados</span><b>{cert_label}</b></div>
        {cert_active_row}
        <p>Use um certificado A1 (.pfx/.p12) ou um par PEM para entrar em
        sites que exigem autenticacao com certificado (bancos, gov.br, etc.).
        Apenas o certificado ativo e enviado aos sites; os instalados no
        Windows tambem sao oferecidos automaticamente.</p>
        <div class="actions">
            <a class="btn" href="{INTERNAL_ACTION_BASE}certificate/manage">
                {svg_icon('certificate', 'currentColor', 15)}
                <span>Gerenciar certificados</span>
            </a>
            <a class="btn" href="{INTERNAL_ACTION_BASE}certificate/import">
                {svg_icon('download', 'currentColor', 15)}
                <span>Importar A1</span>
            </a>
        </div>
    </div>
    <div class="panel">
        <h3>{svg_icon('sparkle', 'currentColor', 16)}Sobre</h3>
        <div class="kv"><span>Navegador</span><b>Upgraded One</b></div>
        <div class="kv"><span>Motor</span><b>Chromium (Qt WebEngine)</b></div>
        <p>Flags ativas:</p>
        <code class="code">--no-sandbox --disable-dev-shm-usage --ignore-certificate-errors</code>
    </div>
</div>
"""
        return InternalPages._page("Configuracoes", colors, dark, content)

# ═══════════════════════════════════════════════════════════════════════════════
# CSS DINAMICO COM CORREÇÃO DE FAVICONS E BOTAO FECHAR NAS ABAS
# ═══════════════════════════════════════════════════════════════════════════════
def generate_css(accent="#ff2a5f", bg_secondary="#141721", bg_main="#0b0c10",
                 is_private=False, dark=True):
    t = theme_palette(accent, bg_secondary, bg_main, dark, is_private)
    accent = t["accent"]
    text, muted = t["text"], t["muted"]
    border, hover = t["border"], t["hover"]
    titlebar, input_focus = t["titlebar"], t["input"]
    danger = t["danger"]
    accent_soft = t["accent_soft"]

    return f"""
    QMainWindow, QWidget, #CentralWidget, QStackedWidget {{
        background-color: {bg_main} !important;
        color: {text};
    }}
    QDialog {{ background-color: {bg_main}; color: {text}; }}
    QLabel {{ background: transparent; color: {text}; }}

    /* ── Barra de títulos ─────────────────────────────────────────────── */
    #CustomTitleBar {{
        background-color: {titlebar} !important;
        border-bottom: 1px solid {border};
    }}
    #TitleLabel {{
        color: {muted};
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.5px;
        background: transparent !important;
    }}
    #CustomTitleBar QToolButton {{
        background: transparent !important;
        border: none;
        color: {muted};
        border-radius: 8px;
        margin: 3px 2px;
    }}
    #CustomTitleBar QToolButton:hover {{
        background-color: {hover} !important;
    }}
    #CustomTitleBar QToolButton#BtnClose:hover {{
        background-color: {danger} !important;
    }}

    /* ── Barra lateral ────────────────────────────────────────────────── */
    #Sidebar {{
        background-color: {titlebar} !important;
        border: none;
    }}
    #SidebarScroll {{
        background-color: {titlebar} !important;
        border: none;
        border-right: 1px solid {border};
    }}
    #Sidebar QToolButton {{
        background: transparent !important;
        border: none;
        border-radius: 12px;
        padding: 0;
    }}
    #Sidebar QToolButton:hover {{
        background-color: {accent_soft} !important;
    }}
    #Sidebar QToolButton:pressed {{
        background-color: {hover} !important;
    }}
    #Sidebar QToolButton[active="true"] {{
        background-color: {accent_soft} !important;
    }}
    #SidebarSeparator {{
        background-color: {border};
        max-height: 1px;
        min-height: 1px;
        margin: 6px 16px;
    }}

    /* ── Barras de ferramentas ────────────────────────────────────────── */
    QToolBar {{
        background-color: {bg_main} !important;
        border: none;
        border-bottom: 1px solid {border};
        spacing: 4px;
        padding: 6px 10px;
    }}
    QToolBar::separator {{
        width: 1px;
        background-color: {border};
        margin: 6px 4px;
    }}
    QToolBar QToolButton {{
        background: transparent !important;
        border: none;
        border-radius: 8px;
        padding: 5px;
    }}
    QToolBar QToolButton:hover {{
        background-color: {hover} !important;
    }}
    QToolBar QToolButton:pressed {{
        background-color: {accent_soft} !important;
    }}

    /* ── Barra de endereço ────────────────────────────────────────────── */
    QLineEdit {{
        background-color: {bg_secondary} !important;
        color: {text};
        border: 1px solid {border};
        border-radius: 17px;
        padding: 6px 14px;
        font-size: 13px;
        min-height: 22px;
        selection-background-color: {accent};
        selection-color: #ffffff;
    }}
    QLineEdit:hover {{ border: 1px solid {rgba(accent, 0.55)}; }}
    QLineEdit:focus {{
        border: 1px solid {accent};
        background-color: {input_focus} !important;
    }}

    /* ── Abas ─────────────────────────────────────────────────────────── */
    QTabWidget {{ background-color: {bg_main} !important; border: none; }}
    QTabWidget::pane {{ border: none; background-color: {bg_main} !important; top: -1px; }}
    QTabBar {{ background-color: transparent !important; border: none; }}
    QTabBar::tab {{
        background-color: transparent !important;
        color: {muted};
        border: 1px solid transparent;
        border-bottom: 2px solid transparent;
        border-top-left-radius: 10px;
        border-top-right-radius: 10px;
        padding: 6px 30px 6px 12px;
        margin: 4px 2px 0 2px;
        font-size: 12px;
        font-weight: 600;
        height: 32px;
        min-width: 132px;
        max-width: 210px;
    }}
    QTabBar::tab:selected {{
        background-color: {bg_secondary} !important;
        color: {text};
        border-bottom: 2px solid {accent};
    }}
    QTabBar::tab:hover:!selected {{
        background-color: {hover} !important;
        color: {text};
    }}
    QTabBar::close-button {{
        subcontrol-position: right;
        subcontrol-origin: padding;
        width: 14px;
        height: 14px;
        border-radius: 7px;
        background: transparent;
        image: none;
    }}
    #NewTabButton {{
        background: transparent !important;
        border: 1px solid transparent;
        border-radius: 9px;
    }}
    #NewTabButton:hover {{
        background-color: {hover} !important;
        border: 1px solid {border};
    }}
    #TabCloseButton {{
        background: transparent !important;
        border: none;
        border-radius: 9px;
    }}
    #TabCloseButton:hover {{
        background-color: rgba(239, 68, 68, 0.22) !important;
    }}

    /* ── Barra de progresso ───────────────────────────────────────────── */
    QProgressBar {{ border: none; background: transparent; height: 2px; text-align: center; }}
    QProgressBar::chunk {{ background-color: {accent}; }}

    /* ── Menus e combos ───────────────────────────────────────────────── */
    QMenu {{
        background-color: {bg_secondary};
        color: {text};
        border: 1px solid {border};
        border-radius: 10px;
        padding: 6px;
    }}
    QMenu::item {{ padding: 7px 26px 7px 14px; border-radius: 7px; }}
    QMenu::item:selected {{ background-color: {accent}; color: #ffffff; }}
    QMenu::separator {{ height: 1px; background: {border}; margin: 5px 8px; }}
    QComboBox {{
        background-color: {bg_secondary};
        color: {text};
        border: 1px solid {border};
        border-radius: 10px;
        padding: 5px 10px;
        min-height: 22px;
        font-size: 12px;
    }}
    QComboBox:hover {{ border-color: {accent}; }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
    QComboBox QAbstractItemView {{
        background-color: {bg_secondary};
        color: {text};
        border: 1px solid {border};
        border-radius: 8px;
        padding: 4px;
        outline: none;
        selection-background-color: {accent};
        selection-color: #ffffff;
    }}

    /* ── Botões e diálogos ────────────────────────────────────────────── */
    QPushButton {{
        background-color: {bg_secondary};
        color: {text};
        border: 1px solid {border};
        padding: 7px 16px;
        border-radius: 9px;
        font-weight: 600;
    }}
    QPushButton:hover {{ background-color: {hover}; border-color: {accent}; }}
    QPushButton:pressed {{ background-color: {accent_soft}; }}
    QMessageBox {{ background-color: {bg_main}; }}
    QMessageBox QLabel {{ color: {text}; }}
    QMessageBox QPushButton {{
        background-color: {bg_secondary};
        color: {text};
        border: 1px solid {border};
        padding: 7px 18px;
        border-radius: 9px;
        min-width: 76px;
        font-weight: 600;
    }}
    QMessageBox QPushButton:hover {{ background-color: {accent}; color: #ffffff; }}
    QListWidget {{
        background-color: {bg_secondary};
        color: {text};
        border: 1px solid {border};
        border-radius: 10px;
        padding: 6px;
        outline: none;
    }}
    QListWidget::item {{
        padding: 9px 10px;
        border-radius: 8px;
        margin: 2px 0;
    }}
    QListWidget::item:selected {{
        background-color: {accent};
        color: #ffffff;
    }}
    QListWidget::item:hover {{ background-color: {hover}; }}

    /* ── Rolagem e dicas ──────────────────────────────────────────────── */
    QScrollArea {{ border: none; background: {bg_main}; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{
        background: {border};
        border-radius: 5px;
        min-height: 32px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {accent}; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{
        background: {border};
        border-radius: 5px;
        min-width: 32px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {accent}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QToolTip {{
        background-color: {bg_secondary};
        color: {text};
        border: 1px solid {accent};
        border-radius: 6px;
        padding: 5px 9px;
        opacity: 240;
    }}
    """


# ═══════════════════════════════════════════════════════════════════════════════
# COMPONENTES UI
# ═══════════════════════════════════════════════════════════════════════════════
class IconButton(QToolButton):
    """Botão com ícone vetorial que troca de cor no hover (sem emoji)."""

    def __init__(self, name, tooltip="", parent=None, size=18, button=32):
        super().__init__(parent)
        self.icon_name = name
        self.icon_px = int(size)
        self._base = "#8b92a5"
        self._hover = "#ff2a5f"
        self._hovered = False
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.setIconSize(QSize(self.icon_px, self.icon_px))
        if button:
            self.setFixedSize(int(button), int(button))
        self._apply_icon()

    def set_icon_colors(self, base, hover=None):
        self._base = base or self._base
        self._hover = hover or self._base
        self._apply_icon()

    def set_icon_name(self, name):
        self.icon_name = name
        self._apply_icon()

    def _apply_icon(self):
        color = self._hover if self._hovered else self._base
        self.setIcon(vector_icon(self.icon_name, color, self.icon_px))

    def enterEvent(self, event):
        self._hovered = True
        self._apply_icon()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._apply_icon()
        super().leaveEvent(event)


class CustomTitleBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_win = parent
        self.setObjectName("CustomTitleBar")
        self.setFixedHeight(38)
        self.start_pos = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 4, 0)
        layout.setSpacing(9)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(20, 20)
        self.icon_label.setStyleSheet("background: transparent;")
        pixmap = logo_pixmap(20)
        if not pixmap.isNull():
            self.icon_label.setPixmap(pixmap)
        else:
            self.icon_label.setPixmap(vector_icon("home-color", "#ff2a5f", 18).pixmap(18, 18))
        layout.addWidget(self.icon_label)

        self.title_label = QLabel(APP_NAME)
        self.title_label.setObjectName("TitleLabel")
        layout.addWidget(self.title_label)
        layout.addStretch()

        self.btn_min = IconButton("minimize", "Minimizar", self, size=13, button=0)
        self.btn_min.setFixedSize(44, 32)
        self.btn_min.clicked.connect(self.parent_win.showMinimized)

        self.btn_max = IconButton("maximize", "Maximizar / Restaurar", self, size=13, button=0)
        self.btn_max.setFixedSize(44, 32)
        self.btn_max.clicked.connect(self.toggle_max_restore)

        self.btn_close = IconButton("close", "Fechar", self, size=13, button=0)
        self.btn_close.setObjectName("BtnClose")
        self.btn_close.setFixedSize(44, 32)
        self.btn_close.clicked.connect(self.parent_win.close)

        for btn in (self.btn_min, self.btn_max, self.btn_close):
            layout.addWidget(btn)

    def apply_theme(self, muted, text, accent="#ff2a5f"):
        self.btn_min.set_icon_colors(muted, text)
        self.btn_max.set_icon_colors(muted, text)
        self.btn_close.set_icon_colors(muted, "#ffffff")

    def toggle_max_restore(self):
        if self.parent_win.isMaximized():
            self.parent_win.showNormal()
            self.btn_max.set_icon_name("maximize")
        else:
            self.parent_win.showMaximized()
            self.btn_max.set_icon_name("restore")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start_pos = event.globalPos() - self.parent_win.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self.start_pos is not None:
            if self.parent_win.isMaximized():
                self.parent_win.showNormal()
                self.btn_max.set_icon_name("maximize")
            self.parent_win.move(event.globalPos() - self.start_pos)
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle_max_restore()


class TabCloseButton(IconButton):
    """Botão de fechar da aba (ícone vetorial em vez de caractere)."""

    def __init__(self, parent=None):
        super().__init__("close", "Fechar aba", parent, size=9, button=18)
        self.setObjectName("TabCloseButton")
        self.set_icon_colors("#8b92a5", "#ffffff")


class ChromeTabBar(QTabBar):
    new_tab_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setElideMode(Qt.ElideRight)
        self.setDocumentMode(True)
        self.setUsesScrollButtons(True)
        self.setIconSize(QSize(16, 16))
        self.setExpanding(False)

        self.add_btn = IconButton("plus", "Nova aba (Ctrl+T)", self, size=14, button=28)
        self.add_btn.setObjectName("NewTabButton")
        self.add_btn.clicked.connect(self.new_tab_requested.emit)

    def apply_theme(self, muted="#8b92a5", accent="#ff2a5f"):
        self.add_btn.set_icon_colors(muted, accent)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_add_button()

    def tabInserted(self, index):
        super().tabInserted(index)
        try:
            btn = TabCloseButton(self)
            btn.clicked.connect(lambda _=False, b=btn: self._on_close_clicked(b))
            self.setTabButton(index, QTabBar.RightSide, btn)
        except Exception:
            pass
        self._position_add_button()

    def _on_close_clicked(self, btn):
        try:
            for i in range(self.count()):
                if self.tabButton(i, QTabBar.RightSide) == btn:
                    tw = self.find_tab_widget()
                    if tw:
                        tw.tabCloseRequested.emit(i)
                    break
        except RuntimeError:
            # O botão pode já ter sido destruído pelo Qt.
            pass

    def tabRemoved(self, index):
        super().tabRemoved(index)
        self._position_add_button()

    def _position_add_button(self):
        count = self.count()
        if count == 0:
            self.add_btn.move(4, 4)
            return
        last_rect = self.tabRect(count - 1)
        x_pos = last_rect.right() + 6 if last_rect.isValid() else self.width() - 34
        x_pos = min(x_pos, self.width() - 34)
        y_pos = (self.height() - self.add_btn.height()) // 2
        self.add_btn.move(max(4, x_pos), max(0, y_pos))

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self.tabAt(event.pos()) == -1:
            self.new_tab_requested.emit()
            return
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton:
            idx = self.tabAt(event.pos())
            if idx != -1:
                tab_widget = self.find_tab_widget()
                if tab_widget:
                    tab_widget.tabCloseRequested.emit(idx)
                return
        super().mouseReleaseEvent(event)

    def find_tab_widget(self):
        w = self.parentWidget()
        while w is not None:
            if isinstance(w, QTabWidget):
                return w
            w = w.parentWidget()
        return None

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        tw = self.find_tab_widget()
        if tw and tw.count() > 1 and delta:
            step = 1 if delta > 0 else -1
            tw.setCurrentIndex((tw.currentIndex() + step) % tw.count())
            event.accept()
            return
        super().wheelEvent(event)


class LoadingProgressBar(QProgressBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaximumHeight(2)
        self.setTextVisible(False)
        self.setRange(0, 100)
        self.setValue(0)
        self.setVisible(False)


class CustomWebEnginePage(QWebEnginePage):
    def __init__(self, profile, main_window, parent=None):
        super().__init__(profile, parent)
        self.main_window = main_window
        # Mantém as seleções vivas enquanto o diálogo está aberto (o objeto
        # pertence ao Chromium e não pode ser coletado antes do select()).
        self._cert_selections = []
        try:
            self.selectClientCertificate.connect(self._on_select_client_certificate)
        except (AttributeError, TypeError, RuntimeError):
            pass

    def certificateError(self, error):
        """Aceita os erros de certificado de servidor.

        O navegador já inicia o Chromium com --ignore-certificate-errors
        (necessário em redes com proxy/inspeção TLS). Devolver True aqui
        garante que a navegação continue em vez de parar numa página de erro.
        """
        try:
            url = error.url().toString()
        except (AttributeError, RuntimeError):
            url = ""
        if url:
            self._log_cert_error(url)
        return True

    def _log_cert_error(self, url):
        try:
            log_path = Path.home() / ".UpgradedOneBrowser" / "certificate_errors.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{stamp} {url}\n")
        except OSError:
            pass

    def _on_select_client_certificate(self, selection):
        """O site pediu um certificado de cliente (mTLS).

        A escolha precisa acontecer *dentro* deste callback: o Chromium só
        envia o certificado se ``select()`` for chamado antes de o sinal
        retornar. Adiar com QTimer faz o servidor receber uma resposta vazia.
        """
        try:
            self._cert_selections.append(selection)
            # Mantém apenas as últimas seleções vivas (objetos pequenos).
            del self._cert_selections[:-5]
        except Exception:
            pass
        self._choose_client_certificate(selection)

    def _choose_client_certificate(self, selection):
        try:
            certs = list(selection.certificates())
        except (AttributeError, RuntimeError, TypeError):
            certs = []

        chosen = None
        if len(certs) == 1:
            chosen = certs[0]
        elif len(certs) > 1:
            try:
                host = self.url().host()
            except (AttributeError, RuntimeError):
                host = ""
            try:
                dialog = CertificateChoiceDialog(certs, host, self.main_window)
                if dialog.exec_() == QDialog.Accepted:
                    chosen = dialog.selected_certificate
            except (RuntimeError, AttributeError):
                chosen = None

        try:
            if chosen is not None:
                selection.select(chosen)
            else:
                selection.selectNone()
        except (AttributeError, RuntimeError, TypeError):
            pass

        if not certs:
            # A mensagem é adiada para não abrir uma janela modal no meio do
            # callback de rede do Chromium.
            QTimer.singleShot(0, self._show_missing_certificate_hint)

    def _show_missing_certificate_hint(self):
        try:
            QMessageBox.information(
                self.main_window, "Certificado digital",
                "O site pediu um certificado digital, mas nenhum certificado "
                "esta disponivel para este perfil.\n\n"
                "Abra o menu lateral > Certificado digital para importar um "
                "arquivo A1 (.pfx/.p12) ou um par PEM e confira se o "
                "certificado ativo corresponde ao site.",
            )
        except RuntimeError:
            pass

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        """Intercepta as ações das páginas internas."""
        try:
            handler = getattr(self.main_window, "handle_internal_action", None)
            if handler is not None and handler(url.toString()):
                return False
        except Exception:
            pass
        try:
            return super().acceptNavigationRequest(url, nav_type, is_main_frame)
        except TypeError:
            return True

    def createWindow(self, _type):
        try:
            if self.main_window and hasattr(self.main_window, "add_tab_for_popup"):
                return self.main_window.add_tab_for_popup()
            return super().createWindow(_type)
        except Exception:
            return None


class WebView(QWebEngineView):
    GESTURE_THRESHOLD = 80

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self._mouse_press_pos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.XButton1:
            self.back(); return
        if event.button() == Qt.XButton2:
            self.forward(); return
        if event.button() == Qt.RightButton:
            self._mouse_press_pos = event.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton and self._mouse_press_pos is not None:
            diff = event.pos() - self._mouse_press_pos
            self._mouse_press_pos = None
            if abs(diff.x()) > self.GESTURE_THRESHOLD and abs(diff.y()) < 50:
                if diff.x() > 0:
                    self.back()
                else:
                    self.forward()
                return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        try:
            page = self.page()
            data = page.contextMenuData() if page is not None else None
        except (AttributeError, RuntimeError):
            data = None

        link = ""
        selected = ""
        if data is not None:
            try:
                link = data.linkUrl().toString()
                selected = data.selectedText()
            except RuntimeError:
                link = selected = ""

        menu = QMenu(self)

        if link:
            act_open = QAction("Abrir em nova aba", self)
            act_open.triggered.connect(lambda _=False, u=link: self.main_window.add_tab(u))
            menu.addAction(act_open)
            act_copy = QAction("Copiar link", self)
            act_copy.triggered.connect(lambda _=False, u=link: QApplication.clipboard().setText(u))
            menu.addAction(act_copy)
            menu.addSeparator()

        act_back = QAction("Voltar", self); act_back.triggered.connect(self.back)
        act_fwd = QAction("Avancar", self); act_fwd.triggered.connect(self.forward)
        act_reload = QAction("Recarregar", self); act_reload.triggered.connect(self.reload)
        menu.addAction(act_back); menu.addAction(act_fwd); menu.addAction(act_reload)
        menu.addSeparator()

        if selected:
            act_search = QAction("Pesquisar selecao", self)
            act_search.triggered.connect(
                lambda _=False, q=selected: self.main_window.add_tab(
                    "https://www.google.com/search?q=" + urllib.parse.quote(q)
                )
            )
            menu.addAction(act_search)
            menu.addSeparator()

        act_dev = QAction("Inspecionar", self)
        act_dev.triggered.connect(lambda checked=False: self.main_window.open_devtools(self))
        menu.addAction(act_dev)

        menu.setStyleSheet(self.main_window.styleSheet())
        menu.exec_(event.globalPos())


# ═══════════════════════════════════════════════════════════════════════════════
# JANELA PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════
_LIVE_PROFILES = []          # mantém perfis vivos enquanto o app existir
_LIVE_INTERCEPTORS = []      # idem para os interceptores (filhos do perfil)
_LIVE_SCRIPTS = []           # idem para os QWebEngineScript injetados
_NAMED_PROFILES = {}         # cache: nome -> QWebEngineProfile


GOOGLE_UA_SCRIPT = r"""
(function(){
  try {
    var hosts = __HOSTS__;
    var host = (location.hostname || '').toLowerCase();
    if (hosts.indexOf(host) === -1) { return; }
    var ua = '__UA__';
    var set = function(obj, prop, value) {
      try {
        Object.defineProperty(obj, prop, {
          get: function(){ return value; },
          configurable: true
        });
      } catch (e) {}
    };
    var del = function(obj, prop) {
      try {
        delete obj[prop];
      } catch (e) {}
    };
    set(navigator, 'userAgent', ua);
    set(navigator, 'appVersion', '5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)');
    set(navigator, 'vendor', 'Google Inc.');
    set(navigator, 'platform', 'Win32');
    set(navigator, 'productSub', '20030107');
    set(navigator, 'vendorSub', '');
    set(navigator, 'language', 'pt-BR');
    set(navigator, 'languages', Object.freeze ? Object.freeze(['pt-BR', 'pt', 'en-US', 'en']) : ['pt-BR', 'pt', 'en-US', 'en']);
    try { del(navigator, 'webdriver'); } catch (e) {}
    try { Object.defineProperty(navigator, 'webdriver', { get: function(){ return undefined; }, configurable: true }); } catch (e) {}
    try { set(navigator, 'maxTouchPoints', 0); } catch(e){}
    try { set(navigator, 'hardwareConcurrency', 8); } catch(e){}
    try { set(navigator, 'deviceMemory', 8); } catch(e){}
    try {
      Object.defineProperty(navigator, 'userAgentData', {
        get: function(){
          return {
            brands: [
              {brand: 'Not/A)Brand', version: '8'},
              {brand: 'Chromium', version: '126'},
              {brand: 'Google Chrome', version: '126'}
            ],
            mobile: false,
            platform: 'Windows',
            getHighEntropyValues: function(hints){
              return Promise.resolve({
                brands: [
                  {brand: 'Not/A)Brand', version: '8.0.0.0'},
                  {brand: 'Chromium', version: '126.0.6478.127'},
                  {brand: 'Google Chrome', version: '126.0.6478.127'}
                ],
                mobile: false,
                platform: 'Windows',
                platformVersion: '15.0.0',
                architecture: 'x86',
                bitness: '64',
                model: '',
                uaFullVersion: '126.0.6478.127',
                fullVersionList: [
                  {brand: 'Not/A)Brand', version: '8.0.0.0'},
                  {brand: 'Chromium', version: '126.0.6478.127'},
                  {brand: 'Google Chrome', version: '126.0.6478.127'}
                ]
              });
            },
            toJSON: function(){ return this; }
          };
        },
        configurable: true
      });
    } catch (e) {}
    try {
      window.chrome = window.chrome || {};
      window.chrome.app = window.chrome.app || {
        isInstalled: false,
        InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
        RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
        getDetails: function(){ return null; },
        getIsInstalled: function(){ return false; },
        installState: function(cb){ if(cb) cb('not_installed'); },
        runningState: function(){ return 'cannot_run'; }
      };
      window.chrome.runtime = window.chrome.runtime || {
        connect: function(){ return { onMessage: { addListener:function(){} }, postMessage:function(){}, disconnect:function(){} }; },
        sendMessage: function(){},
        OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
        OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
        PlatformArch: { ARM: 'arm', ARM64: 'arm64', X86_32: 'x86-32', X86_64: 'x86-64' },
        PlatformNaclArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
        PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
        RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' }
      };
      window.chrome.csi = function() { return { onloadT: Date.now(), startE: Date.now()-500, pageT: 500, tran: 15 }; };
      window.chrome.loadTimes = function() {
        var now = Date.now() / 1000;
        return {
          commitLoadTime: now - 1.3,
          connectionInfo: "h2",
          finishDocumentLoadTime: now - 0.25,
          finishLoadTime: now - 0.15,
          firstPaintAfterLoadTime: now - 0.45,
          firstPaintTime: now - 0.65,
          navigationType: "Other",
          npnNegotiatedProtocol: "h2",
          requestTime: now - 1.4,
          startLoadTime: now - 1.4,
          wasAlternateProtocolAvailable: false,
          wasFetchedViaSpdy: true,
          wasNpnNegotiated: true
        };
      };
    } catch (e) {}
    try {
      var pluginArray = function(){
        var arr = [];
        try {
          arr.push({ name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format', length: 1 });
          arr.push({ name: 'Chromium PDF Plugin', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: 'Portable Document Format', length: 1 });
          arr.push({ name: 'Microsoft Edge PDF Plugin', filename: 'pdf', description: 'Portable Document Format', length: 1 });
          arr.push({ name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer', description: 'Portable Document Format', length: 1 });
        } catch(e){}
        arr.item = function(i){ return arr[i] || null; };
        arr.namedItem = function(n){ for(var i=0;i<arr.length;i++){ if(arr[i] && arr[i].name===n) return arr[i]; } return null; };
        arr.refresh = function(){};
        return arr;
      };
      Object.defineProperty(navigator, 'plugins', { get: function(){ return pluginArray(); }, configurable: true });
      Object.defineProperty(navigator, 'mimeTypes', { get: function(){
        var m = [];
        try {
          m.push({ type: 'application/pdf', description: 'Portable Document Format', suffixes: 'pdf', enabledPlugin: pluginArray()[0] });
          m.push({ type: 'text/pdf', description: 'Portable Document Format', suffixes: 'pdf', enabledPlugin: pluginArray()[0] });
          m.push({ type: 'application/x-google-chrome-pdf', description: 'Portable Document Format', suffixes: 'pdf', enabledPlugin: pluginArray()[0] });
        } catch(e){}
        m.item=function(i){return m[i]||null};
        m.namedItem=function(n){for(var i=0;i<m.length;i++){if(m[i]&&m[i].type===n)return m[i];}return null};
        return m;
      }, configurable: true });
    } catch(e){}
    try { if (window.external && typeof window.external.AddSearchProvider === 'undefined') window.external.AddSearchProvider = function(){}; } catch(e){}
    try { if (typeof window.showModalDialog === 'undefined') window.showModalDialog = function(){}; } catch(e){}
    try { if (typeof window.chrome.webstore === 'undefined') window.chrome.webstore = { install: function(){}, installUrl: '' }; } catch(e){}
  } catch (e) {}
})();
""".replace("__HOSTS__", json.dumps(sorted(GOOGLE_AUTH_HOSTS))).replace(
    "__UA__", GOOGLE_CHROME_USER_AGENT
)


def _install_google_ua_script(profile):
    """Faz o JavaScript das páginas de login do Google ver o UA do Firefox.

    O cabeçalho HTTP é trocado pelo AntiBotInterceptor; este script garante que
    ``navigator.userAgent`` também seja coerente, evitando a deteção de que o
    navegador é embutido.
    """
    try:
        existing = profile.scripts().findScript("google-login-ua")
        # findScript devolve um QWebEngineScript nulo (não None) quando não
        # encontra; por isso é preciso testar isNull().
        if existing is not None and not existing.isNull():
            return
        script = QWebEngineScript()
        script.setName("google-login-ua")
        script.setInjectionPoint(QWebEngineScript.DocumentCreation)
        script.setWorldId(QWebEngineScript.MainWorld)
        script.setRunsOnSubFrames(True)
        script.setSourceCode(GOOGLE_UA_SCRIPT)
        profile.scripts().insert(script)
        # Mantém o objeto vivo: se o wrapper Python for coletado, o script
        # inserido no perfil pode ficar sem código (sourceCode vazio).
        _LIVE_SCRIPTS.append(script)
    except Exception:
        pass


def _install_anti_bot(profile):
    """Instala o interceptor mantendo referência forte e pai correto.

    Sem isso, o objeto Python é coletado e o Chromium chama um endereço
    inválido na próxima requisição (access violation).
    """
    try:
        interceptor = AntiBotInterceptor(profile)
        profile.setUrlRequestInterceptor(interceptor)
        _LIVE_INTERCEPTORS.append(interceptor)
    except Exception:
        pass
    _install_google_ua_script(profile)


# ═══════════════════════════════════════════════════════════════════════════════
# CERTIFICADOS DIGITAIS (A1/PEM + certificados instalados no Windows)
# ═══════════════════════════════════════════════════════════════════════════════
class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


_CRYPT32 = None
_KERNEL32 = None
if sys.platform == "win32":
    try:
        _CRYPT32 = ctypes.WinDLL("crypt32", use_last_error=True)
        _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _CRYPT32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        _CRYPT32.CryptProtectData.restype = wintypes.BOOL
        _CRYPT32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        _CRYPT32.CryptUnprotectData.restype = wintypes.BOOL
        _KERNEL32.LocalFree.argtypes = [ctypes.c_void_p]
        _KERNEL32.LocalFree.restype = ctypes.c_void_p
    except Exception:
        _CRYPT32 = None
        _KERNEL32 = None


def _protect_secret(data):
    """Cifra bytes com a DPAPI do Windows (ligada à conta do usuário)."""
    if not data:
        return b""
    if _CRYPT32 is None or _KERNEL32 is None:
        raise OSError("DPAPI indisponivel")
    buffer = ctypes.create_string_buffer(bytes(data), len(data))
    in_blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    out_blob = _DataBlob()
    ok = _CRYPT32.CryptProtectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        _KERNEL32.LocalFree(out_blob.pbData)


def _unprotect_secret(data):
    """Decifra bytes protegidos por :func:`_protect_secret`."""
    if not data:
        return b""
    if _CRYPT32 is None or _KERNEL32 is None:
        raise OSError("DPAPI indisponivel")
    buffer = ctypes.create_string_buffer(bytes(data), len(data))
    in_blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    out_blob = _DataBlob()
    ok = _CRYPT32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        _KERNEL32.LocalFree(out_blob.pbData)


def _cert_name(cert):
    try:
        names = cert.subjectInfo(QSslCertificate.CommonName) or []
        if names:
            return str(names[0])
    except (AttributeError, RuntimeError):
        pass
    try:
        return cert.subjectDisplayName() or "Certificado"
    except (AttributeError, RuntimeError):
        return "Certificado"


def _cert_display_text(cert):
    name = _cert_name(cert)
    try:
        issuer = cert.issuerDisplayName() or "Desconhecido"
    except (AttributeError, RuntimeError):
        issuer = "Desconhecido"
    try:
        expiry = cert.expiryDate()
        expiry_text = expiry.toString("dd/MM/yyyy") if expiry.isValid() else "?"
        expired = expiry.isValid() and expiry < QDateTime.currentDateTime()
    except (AttributeError, RuntimeError):
        expiry_text = "?"
        expired = False
    try:
        serial_raw = cert.serialNumber()
        try:
            serial = bytes(serial_raw).decode("ascii", "ignore")
        except (TypeError, ValueError):
            serial = str(serial_raw)
    except (AttributeError, RuntimeError):
        serial = ""
    status = "  [EXPIRADO]" if expired else ""
    return (
        f"{name}{status}\n"
        f"Emissor: {issuer}\n"
        f"Valido ate: {expiry_text}    Serial: {serial[:40]}"
    )


def _load_pkcs12(data, password=""):
    """Extrai certificado e chave privada de um arquivo .pfx/.p12."""
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    if not buffer.open(QIODevice.ReadOnly):
        return None, None
    try:
        key = QSslKey()
        cert = QSslCertificate()
        ca_certs = []
        try:
            password_bytes = QByteArray(str(password or "").encode("utf-8"))
        except Exception:
            password_bytes = QByteArray()
        ok = QSslCertificate.importPkcs12(buffer, key, cert, ca_certs, password_bytes)
        if not ok or cert.isNull() or key.isNull():
            return None, None
        return cert, key
    except (RuntimeError, TypeError, ValueError):
        return None, None
    finally:
        try:
            buffer.close()
        except RuntimeError:
            pass


def _load_pem(cert_data, key_data, password=""):
    """Extrai certificado e chave privada de arquivos PEM/DER."""
    try:
        certs = QSslCertificate.fromData(QByteArray(cert_data), QSsl.Pem)
        if not certs:
            certs = QSslCertificate.fromData(QByteArray(cert_data), QSsl.Der)
    except (RuntimeError, TypeError, ValueError):
        certs = []
    cert = next((c for c in certs if c is not None and not c.isNull()), None)
    if cert is None:
        return None, None

    try:
        password_bytes = QByteArray(str(password or "").encode("utf-8"))
    except Exception:
        password_bytes = QByteArray()
    key = QSslKey()
    for algorithm in (QSsl.Rsa, QSsl.Ec, QSsl.Dsa):
        for encoding in (QSsl.Pem, QSsl.Der):
            try:
                candidate = QSslKey(
                    QByteArray(key_data), algorithm, encoding,
                    QSsl.PrivateKey, password_bytes,
                )
            except (RuntimeError, TypeError, ValueError):
                continue
            if not candidate.isNull():
                key = candidate
                break
        if not key.isNull():
            break
    if key.isNull():
        return None, None
    return cert, key


class CertificateManager:
    """Gerencia certificados de cliente do perfil.

    A lista de certificados instalados no Windows é fornecida pelo próprio
    QtWebEngine. Os arquivos A1/PEM importados ficam no store em memória e,
    se o usuário autorizar, são guardados cifrados com DPAPI para as próximas
    sessões.
    """

    def __init__(self, profile, parent=None, storage_dir=None):
        self.profile = profile
        self.parent = parent
        self.storage_dir = Path(storage_dir) if storage_dir else None
        self._saved = {}
        # Certificados importados: id -> (cert, key), mais a ordem de importacao.
        self._entries = {}
        self._order = []
        self._active_id = None
        self._load_saved()
        self._sync_store()

    def _store(self):
        try:
            return self.profile.clientCertificateStore()
        except (AttributeError, RuntimeError):
            return None

    def certificates(self):
        """Certificados importados (nao inclui os do repositorio do Windows)."""
        return [self._entries[cid][0] for cid in self._order if cid in self._entries]

    def count(self):
        return len(self._entries)

    def active_certificate(self):
        entry = self._entries.get(self._active_id or "")
        return entry[0] if entry else None

    def active_id(self):
        return self._active_id

    def _sync_store(self):
        """Mantem no store do QtWebEngine apenas o certificado ativo.

        Limitacao do QtWebEngine 5.15 (ClientCertOverrideStore): quando ha
        certificados importados em memoria, ele devolve apenas o *primeiro*
        que casa com a lista de CAs pedida pelo servidor. Com varios no
        store, o dialogo de escolha nunca receberia mais de um. Deixando so
        o ativo, o resultado fica previsivel e o usuario escolhe no menu.
        """
        store = self._store()
        if store is None:
            return
        try:
            store.clear()
        except (AttributeError, RuntimeError):
            pass
        entry = self._entries.get(self._active_id or "")
        if entry is None:
            return
        cert, key = entry
        try:
            store.add(cert, key)
        except (RuntimeError, TypeError, ValueError):
            pass

    @staticmethod
    def _cert_id(cert):
        try:
            return hashlib.sha256(bytes(cert.toDer())).hexdigest()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return ""

    def _has_cert(self, cert):
        cert_id = self._cert_id(cert)
        return bool(cert_id) and cert_id in self._entries

    def _add_unique(self, cert, key, sync=True):
        if cert is None or key is None or cert.isNull() or key.isNull():
            return False
        cert_id = self._cert_id(cert)
        if not cert_id:
            return False
        if cert_id not in self._entries:
            self._entries[cert_id] = (cert, key)
            self._order.append(cert_id)
        if self._active_id is None:
            self._active_id = cert_id
        if sync:
            self._sync_store()
            self._save()
        return True

    def set_active(self, cert):
        """Define qual certificado importado sera enviado aos sites."""
        cert_id = self._cert_id(cert)
        if not cert_id or cert_id not in self._entries:
            return False
        self._active_id = cert_id
        self._sync_store()
        self._save()
        return True

    def _saved_path(self):
        if self.storage_dir is None:
            return None
        return self.storage_dir / CERT_STORAGE_FILE

    def _load_saved(self):
        path = self._saved_path()
        if path is None or not path.is_file():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError, TypeError):
            return
        entries = payload.get("certificates") if isinstance(payload, dict) else None
        active = payload.get("active") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                kind = entry.get("kind")
                if kind == "pfx":
                    data = _unprotect_secret(base64.b64decode(entry.get("data", "")))
                    password = _unprotect_secret(
                        base64.b64decode(entry.get("password", ""))
                    ).decode("utf-8", "ignore")
                    cert, key = _load_pkcs12(data, password)
                elif kind == "pem":
                    cert_data = _unprotect_secret(
                        base64.b64decode(entry.get("cert", ""))
                    )
                    key_data = _unprotect_secret(
                        base64.b64decode(entry.get("key", ""))
                    )
                    password = _unprotect_secret(
                        base64.b64decode(entry.get("password", ""))
                    ).decode("utf-8", "ignore")
                    cert, key = _load_pem(cert_data, key_data, password)
                else:
                    continue
                if cert is not None and key is not None:
                    self._add_unique(cert, key, sync=False)
                    cert_id = self._cert_id(cert)
                    if cert_id:
                        self._saved[cert_id] = entry
            except Exception:
                continue
        if isinstance(active, str) and active in self._entries:
            self._active_id = active

    def _save(self):
        path = self._saved_path()
        if path is None:
            return False
        if not self._saved and not path.is_file():
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 2,
                "active": self._active_id,
                "certificates": list(self._saved.values()),
            }
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
            return True
        except (OSError, TypeError, ValueError):
            return False

    def _maybe_remember(self, kind, certificate, **payload):
        if self.storage_dir is None:
            return
        cert_id = self._cert_id(certificate)
        if not cert_id or cert_id in self._saved:
            return
        try:
            remember = QMessageBox.question(
                self.parent, "Certificado digital",
                "Guardar este certificado neste computador para as proximas sessoes?\n\n"
                "O arquivo e a senha serao cifrados com a protecao do Windows "
                "(DPAPI), ligada a sua conta de usuario.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            ) == QMessageBox.Yes
        except RuntimeError:
            remember = False
        if not remember:
            return
        entry = {
            "id": cert_id,
            "kind": kind,
            "label": _cert_name(certificate),
            "added": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        try:
            if kind == "pfx":
                entry["data"] = base64.b64encode(
                    _protect_secret(payload.get("data", b""))
                ).decode("ascii")
                entry["password"] = base64.b64encode(
                    _protect_secret(str(payload.get("password", "")).encode("utf-8"))
                ).decode("ascii")
            elif kind == "pem":
                entry["cert"] = base64.b64encode(
                    _protect_secret(payload.get("cert", b""))
                ).decode("ascii")
                entry["key"] = base64.b64encode(
                    _protect_secret(payload.get("key", b""))
                ).decode("ascii")
                entry["password"] = base64.b64encode(
                    _protect_secret(str(payload.get("password", "")).encode("utf-8"))
                ).decode("ascii")
            else:
                return
        except (OSError, ValueError, TypeError):
            return
        self._saved[cert_id] = entry
        self._save()

    def import_pkcs12(self):
        path, _ = QFileDialog.getOpenFileName(
            self.parent, "Importar certificado A1", "",
            "Certificado A1 (*.pfx *.p12 *.pkcs12);;Todos os arquivos (*.*)",
        )
        if not path:
            return False
        password, ok = QInputDialog.getText(
            self.parent, "Senha do certificado",
            "Digite a senha do certificado A1:", QLineEdit.Password,
        )
        if not ok:
            return False
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            QMessageBox.warning(self.parent, "Certificado digital",
                                f"Nao foi possivel ler o arquivo:\n{exc}")
            return False
        cert, key = _load_pkcs12(data, password)
        if cert is None or key is None:
            QMessageBox.warning(
                self.parent, "Certificado digital",
                "Nao foi possivel abrir o certificado.\n\n"
                "Verifique se o arquivo é um A1 (.pfx/.p12) valido e se a senha "
                "esta correta.",
            )
            return False
        if not self._add_unique(cert, key):
            QMessageBox.warning(
                self.parent, "Certificado digital",
                "O certificado foi lido, mas nao foi possivel adiciona-lo ao "
                "navegador. Verifique se ele contem uma chave privada.",
            )
            return False
        self._maybe_remember("pfx", cert, data=data, password=password)
        self.set_active(cert)
        QMessageBox.information(
            self.parent, "Certificado digital",
            f"Certificado importado e definido como ativo:\n\n{_cert_display_text(cert)}",
        )
        return True

    def import_pem(self):
        cert_path, _ = QFileDialog.getOpenFileName(
            self.parent, "Certificado (PEM/DER)", "",
            "Certificado (*.pem *.crt *.cer);;Todos os arquivos (*.*)",
        )
        if not cert_path:
            return False
        key_path, _ = QFileDialog.getOpenFileName(
            self.parent, "Chave privada (PEM/DER)", "",
            "Chave privada (*.pem *.key);;Todos os arquivos (*.*)",
        )
        if not key_path:
            return False
        password, ok = QInputDialog.getText(
            self.parent, "Senha da chave privada",
            "Digite a senha da chave privada (deixe vazio se nao tiver):",
            QLineEdit.Password,
        )
        if not ok:
            return False
        try:
            cert_data = Path(cert_path).read_bytes()
            key_data = Path(key_path).read_bytes()
        except OSError as exc:
            QMessageBox.warning(self.parent, "Certificado digital",
                                f"Nao foi possivel ler os arquivos:\n{exc}")
            return False
        cert, key = _load_pem(cert_data, key_data, password)
        if cert is None or key is None:
            QMessageBox.warning(
                self.parent, "Certificado digital",
                "Nao foi possivel abrir o certificado/chave.\n\n"
                "Use um certificado PEM/DER e a chave privada correspondente. "
                "Se a chave estiver protegida, confira a senha.",
            )
            return False
        if not self._add_unique(cert, key):
            QMessageBox.warning(
                self.parent, "Certificado digital",
                "O certificado foi lido, mas nao foi possivel adiciona-lo ao navegador.",
            )
            return False
        self._maybe_remember(
            "pem", cert, cert=cert_data, key=key_data, password=password
        )
        self.set_active(cert)
        QMessageBox.information(
            self.parent, "Certificado digital",
            f"Certificado importado e definido como ativo:\n\n{_cert_display_text(cert)}",
        )
        return True

    def remove(self, cert):
        cert_id = self._cert_id(cert)
        if not cert_id:
            return
        self._entries.pop(cert_id, None)
        if cert_id in self._order:
            self._order.remove(cert_id)
        if cert_id in self._saved:
            del self._saved[cert_id]
        if self._active_id == cert_id:
            self._active_id = self._order[0] if self._order else None
        self._sync_store()
        self._save()

    def clear(self):
        self._entries.clear()
        self._order.clear()
        self._active_id = None
        self._saved.clear()
        self._sync_store()
        self._save()


class CertificateDialog(QDialog):
    """Janela de gerenciamento dos certificados do perfil."""

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("Certificado digital")
        self.resize(700, 470)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title_row = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(vector_icon("certificate", "#ffffff", 26).pixmap(26, 26))
        title_row.addWidget(icon)
        title = QLabel("Certificados digitais")
        title.setStyleSheet("font-size: 20px; font-weight: 800;")
        title_row.addWidget(title)
        title_row.addStretch()
        layout.addLayout(title_row)

        subtitle = QLabel(
            "O certificado marcado como ativo e o que o navegador envia aos "
            "sites que pedem autenticacao. Certificados instalados no Windows "
            "tambem sao oferecidos automaticamente. Importe aqui o seu A1 "
            "(.pfx/.p12) ou um par PEM (certificado + chave)."
        )
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.active_label = QLabel()
        self.active_label.setWordWrap(True)
        layout.addWidget(self.active_label)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(False)
        self.list.currentItemChanged.connect(lambda *_: self._update_buttons())
        self.list.itemDoubleClicked.connect(lambda *_: self.set_active_selected())
        layout.addWidget(self.list, 1)

        buttons = QHBoxLayout()
        self.btn_pfx = QPushButton("Importar A1 (.pfx/.p12)")
        self.btn_pem = QPushButton("Importar PEM")
        self.btn_active = QPushButton("Usar este certificado")
        self.btn_remove = QPushButton("Remover")
        self.btn_clear = QPushButton("Remover todos")
        self.btn_close = QPushButton("Fechar")
        self.btn_pfx.clicked.connect(self.import_pfx)
        self.btn_pem.clicked.connect(self.import_pem)
        self.btn_active.clicked.connect(self.set_active_selected)
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_clear.clicked.connect(self.remove_all)
        self.btn_close.clicked.connect(self.accept)
        buttons.addWidget(self.btn_pfx)
        buttons.addWidget(self.btn_pem)
        buttons.addStretch()
        buttons.addWidget(self.btn_active)
        buttons.addWidget(self.btn_remove)
        buttons.addWidget(self.btn_clear)
        buttons.addWidget(self.btn_close)
        layout.addLayout(buttons)

        self.refresh()

    def refresh(self):
        self.list.clear()
        certs = self.manager.certificates()
        active_id = self.manager.active_id()
        for cert in certs:
            cert_id = self.manager._cert_id(cert)
            is_active = bool(active_id) and cert_id == active_id
            item = QListWidgetItem(
                ("\u2713 " if is_active else "   ") + _cert_display_text(cert)
            )
            item.setData(Qt.UserRole, cert)
            self.list.addItem(item)
        if not certs:
            item = QListWidgetItem(
                "Nenhum certificado digital importado.\n"
                "Importe um arquivo A1 ou PEM para autenticar em sites que pedem certificado."
            )
            item.setFlags(Qt.NoItemFlags)
            self.list.addItem(item)
        elif self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        active = self.manager.active_certificate()
        if active is not None:
            active_text = "Certificado ativo: <b>" + esc(_cert_name(active)) + "</b>"
        else:
            active_text = (
                "Nenhum certificado ativo. Importe um certificado ou selecione "
                "um da lista e clique em <b>Usar este certificado</b>."
            )
        self.active_label.setText(active_text)
        has_certs = bool(certs)
        self.btn_clear.setEnabled(has_certs)
        self._update_buttons()

    def _update_buttons(self):
        item = self.list.currentItem()
        cert = item.data(Qt.UserRole) if item is not None else None
        is_active = (
            cert is not None
            and self.manager.active_id()
            and self.manager._cert_id(cert) == self.manager.active_id()
        )
        self.btn_remove.setEnabled(cert is not None)
        self.btn_active.setEnabled(cert is not None and not is_active)

    def set_active_selected(self):
        item = self.list.currentItem()
        if item is None:
            return
        cert = item.data(Qt.UserRole)
        if cert is None:
            return
        if self.manager.set_active(cert):
            self.refresh()

    def import_pfx(self):
        if self.manager.import_pkcs12():
            self.refresh()

    def import_pem(self):
        if self.manager.import_pem():
            self.refresh()

    def remove_selected(self):
        item = self.list.currentItem()
        if item is None:
            return
        cert = item.data(Qt.UserRole)
        if cert is None:
            return
        reply = QMessageBox.question(
            self, "Remover certificado",
            f"Remover o certificado abaixo?\n\n{_cert_display_text(cert)}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.manager.remove(cert)
            self.refresh()

    def remove_all(self):
        reply = QMessageBox.question(
            self, "Remover certificados",
            "Remover todos os certificados deste perfil?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.manager.clear()
            self.refresh()


class CertificateChoiceDialog(QDialog):
    """Pedido de certificado feito por um site (autenticação mTLS)."""

    def __init__(self, certs, host, parent=None):
        super().__init__(parent)
        self.selected_certificate = None
        self.setWindowTitle("Certificado digital")
        self.resize(650, 380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel("O site pediu um certificado digital")
        title.setStyleSheet("font-size: 18px; font-weight: 800;")
        layout.addWidget(title)

        info = QLabel(
            f"<b>{esc(host or 'Site')}</b> solicitou autenticacao com certificado. "
            "Escolha qual certificado deseja usar:"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.list = QListWidget()
        for cert in certs:
            item = QListWidgetItem(_cert_display_text(cert))
            item.setData(Qt.UserRole, cert)
            self.list.addItem(item)
        if certs:
            self.list.setCurrentRow(0)
        layout.addWidget(self.list, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancelar")
        use = QPushButton("Usar certificado")
        cancel.clicked.connect(self.reject)
        use.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(use)
        layout.addLayout(buttons)

    def accept(self):
        item = self.list.currentItem()
        if item is None:
            return
        cert = item.data(Qt.UserRole)
        if cert is None:
            return
        self.selected_certificate = cert
        super().accept()


def get_web_profile(profile_name=None, is_private=False, folder=None):
    """Cria/retorna um QWebEngineProfile com tempo de vida seguro.

    Perfis não podem ser destruídos antes das páginas que os usam, caso
    contrário o QtWebEngine aborta o processo no encerramento.
    """
    app = QApplication.instance()
    if is_private:
        # No Qt5, um perfil sem storageName já é off-the-record.
        profile = QWebEngineProfile(app)
        try:
            profile.setPersistentCookiesPolicy(QWebEngineProfile.NoPersistentCookies)
        except AttributeError:
            pass
        _LIVE_PROFILES.append(profile)
        _install_anti_bot(profile)
        return profile

    if not profile_name:
        profile = QWebEngineProfile.defaultProfile()
        _install_anti_bot(profile)
        return profile

    cached = _NAMED_PROFILES.get(profile_name)
    if cached is not None:
        return cached

    profile = QWebEngineProfile(profile_name, app)
    if folder is not None:
        folder = Path(folder)
        try:
            profile.setPersistentStoragePath(str(folder / "storage"))
            profile.setCachePath(str(folder / "cache"))
        except Exception:
            pass
    _NAMED_PROFILES[profile_name] = profile
    _LIVE_PROFILES.append(profile)
    _install_anti_bot(profile)
    return profile


class MainWindow(QMainWindow):
    INTERNAL_URLS = (
        "upgraded_one://home", "upgraded_one://settings",
        "upgraded_one://downloads", "upgraded_one://history", "upgraded_one://bookmarks",
    )

    def __init__(self, profile_name=None, is_private=False, profile_manager=None):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.is_private = is_private
        self.profile_manager = profile_manager
        self.profile_name = profile_name
        self.profile_data = {}
        self.zoom_factor = 1.0
        self.closed_tabs = []
        self._private_windows = []
        self._devtools = None

        if is_private:
            self.dark_mode = True
            self.accent_color = PRIVATE_COLORS["accent"]
            self.bg_secondary = PRIVATE_COLORS["secondary"]
            self.bg_main = PRIVATE_COLORS["main"]
            self.bg_home = PRIVATE_COLORS["home"]
        else:
            self.profile_data = (profile_manager.get(profile_name) if profile_manager else {}) or {}
            self.dark_mode = bool(self.profile_data.get("dark_mode", True))
            pal = DEFAULT_COLORS["dark" if self.dark_mode else "light"]
            self.accent_color = safe_color(self.profile_data.get("accent_color"), pal["accent"])
            self.bg_secondary = safe_color(self.profile_data.get("bg_secondary"), pal["secondary"])
            self.bg_main = safe_color(self.profile_data.get("bg_main"), pal["main"])
            self.bg_home = safe_color(self.profile_data.get("bg_home"), pal["home"])

        folder = None
        if is_private:
            self.profile = get_web_profile(is_private=True)
            self.db = None
            title_prefix = "[ANONIMO] "
        else:
            try:
                folder = Path(self.profile_data.get("folder") or (PROFILES_DIR / "default"))
            except (TypeError, ValueError):
                folder = PROFILES_DIR / "default"
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError:
                folder = PROFILES_DIR / "default"
                folder.mkdir(parents=True, exist_ok=True)
            self.db = LocalDatabase(folder)
            self.profile = get_web_profile(profile_name, folder=folder)
            title_prefix = f"[{profile_name}] " if profile_name else ""

        try:
            self.profile.setHttpUserAgent(USER_AGENT)
        except Exception:
            pass

        ws = self.profile.settings()
        for attr, value in (
            ("JavascriptEnabled", True),
            ("LocalStorageEnabled", not is_private),
            ("WebGLEnabled", True),
            ("PluginsEnabled", True),
            ("FullScreenSupportEnabled", True),
            ("AllowRunningInsecureContent", True),
            ("LocalContentCanAccessRemoteUrls", True),
            ("JavascriptCanOpenWindows", True),
            ("ScreenCaptureEnabled", False),
            ("FocusOnNavigationEnabled", False),
            ("JavascriptCanAccessClipboard", True),
            ("TouchIconsEnabled", True),
        ):
            try:
                ws.setAttribute(getattr(QWebEngineSettings, attr), value)
            except (AttributeError, TypeError):
                pass

        try:
            self.profile.downloadRequested.connect(self.on_download_requested)
        except Exception:
            pass
        self.download_manager = DownloadManager()

        # Certificados digitais: cada perfil tem o seu conjunto. Os
        # certificados instalados no Windows já aparecem automaticamente; os
        # importados (A1/PEM) podem ser guardados cifrados na pasta do perfil.
        cert_folder = None
        if not is_private and folder is not None:
            cert_folder = folder / "certificates"
        self.certificate_manager = CertificateManager(self.profile, self, cert_folder)

        self.setWindowTitle(f"{title_prefix}{APP_NAME}")
        self.setWindowIcon(app_icon())
        self.favicons = FaviconCache(self)
        self.resize(1400, 880)
        self._center()

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.setCentralWidget(root)

        self.title_bar = CustomTitleBar(self)
        self.title_bar.title_label.setText(f"{title_prefix}{APP_NAME}")
        root_layout.addWidget(self.title_bar)

        self.main_widget = QWidget()
        self.main_widget.setObjectName("CentralWidget")
        main_layout = QHBoxLayout(self.main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        root_layout.addWidget(self.main_widget)

        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(0, 10, 0, 10)
        sb_layout.setSpacing(2)

        self._icon_buttons = []

        def sb_btn(icon, tooltip, callback, size=20):
            btn = IconButton(icon, tooltip, sidebar, size=size, button=40)
            btn.clicked.connect(callback)
            self._icon_buttons.append(btn)
            sb_layout.addWidget(btn, 0, Qt.AlignHCenter)
            return btn

        def sb_separator():
            line = QFrame(sidebar)
            line.setObjectName("SidebarSeparator")
            line.setFixedHeight(1)
            line.setFrameShape(QFrame.NoFrame)
            sb_layout.addWidget(line)
            return line

        sb_btn("home", "Inicio", self.go_home)
        sb_btn("back", "Voltar (Alt+Esquerda)", self.current_browser_back)
        sb_btn("forward", "Avancar (Alt+Direita)", self.current_browser_forward)
        sb_btn("reload", "Recarregar (Ctrl+R)", self.current_browser_reload)
        sb_separator()
        sb_btn("downloads", "Downloads (Ctrl+J)", self.open_downloads_window)
        sb_btn("history", "Historico (Ctrl+H)", self.open_history_page)
        sb_btn("star", "Favoritos (Ctrl+Shift+O)", self.open_bookmarks_page)
        sb_btn("certificate", "Certificado digital", self.open_certificate_manager)
        sb_layout.addStretch()

        self.btn_theme = IconButton(
            "moon" if self.dark_mode else "sun",
            "Alternar tema claro/escuro", sidebar, size=20, button=40,
        )
        self.btn_theme.clicked.connect(self.toggle_dark_mode)
        self._icon_buttons.append(self.btn_theme)
        sb_layout.addWidget(self.btn_theme, 0, Qt.AlignHCenter)

        sb_btn("accent", "Cor de destaque", self.choose_accent_color)
        sb_btn("panel", "Cor dos paineis", self.choose_panel_color)
        sb_btn("background", "Cor de fundo", self.choose_bg_color)
        sb_btn("home-color", "Cor da Home", self.choose_home_bg_color)
        sb_separator()
        sb_btn("settings", "Configuracoes", self.open_settings_page)
        if not is_private:
            sb_btn("incognito", "Nova janela anonima", self.open_private_window)
        sb_btn("power", "Sair", self.close)

        # A barra lateral rola quando a janela fica baixa (evita ícones cortados).
        sidebar_wrap = QScrollArea()
        sidebar_wrap.setObjectName("SidebarScroll")
        sidebar_wrap.setWidgetResizable(True)
        sidebar_wrap.setFrameShape(QFrame.NoFrame)
        sidebar_wrap.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sidebar_wrap.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        sidebar_wrap.setFixedWidth(62)
        sidebar_wrap.setWidget(sidebar)
        main_layout.addWidget(sidebar_wrap)

        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        self.bookmark_bar = QToolBar()
        self.bookmark_bar.setMovable(False)
        self.bookmark_bar.setFixedHeight(34)
        self.bookmark_bar.setIconSize(QSize(16, 16))
        self.update_bookmark_bar()
        cl.addWidget(self.bookmark_bar)

        nav = QToolBar()
        nav.setMovable(False)
        nav.setIconSize(QSize(17, 17))

        def nav_btn(icon, tooltip, callback, size=17):
            btn = IconButton(icon, tooltip, nav, size=size, button=30)
            btn.clicked.connect(callback)
            self._icon_buttons.append(btn)
            nav.addWidget(btn)
            return btn

        nav_btn("back", "Voltar (Alt+Esquerda)", self.current_browser_back)
        nav_btn("forward", "Avancar (Alt+Direita)", self.current_browser_forward)
        self.act_reload = nav_btn("reload", "Recarregar (Ctrl+R)", self.current_browser_reload)
        nav_btn("stop", "Parar carregamento", self.current_browser_stop)
        nav_btn("home", "Inicio", self.go_home)

        self.url_bar = QLineEdit()
        self.url_bar.setPlaceholderText("Pesquisar ou digitar endereco web")
        self.url_bar.setClearButtonEnabled(True)
        self.url_bar.setFixedHeight(34)
        self.url_bar.returnPressed.connect(self.go_url)
        self.url_search_icon = self.url_bar.addAction(
            vector_icon("search", "#8b92a5", 16), QLineEdit.LeadingPosition)
        nav.addWidget(self.url_bar)

        self.search_combo = QComboBox()
        self.search_combo.addItems(list(SEARCH_ENGINES.keys()))
        engine = self.profile_data.get("search_engine", "Google")
        if isinstance(engine, str) and engine in SEARCH_ENGINES:
            self.search_combo.setCurrentText(engine)
        self.search_combo.setFixedWidth(112)
        self.search_combo.setFixedHeight(30)
        self.search_combo.currentTextChanged.connect(self._persist_search_engine)
        nav.addWidget(self.search_combo)

        nav_btn("star", "Adicionar favorito (Ctrl+D)", self.add_bookmark_current)
        nav_btn("search", "Localizar na pagina (Ctrl+F)", self.show_find_bar)
        nav_btn("zoom-in", "Aumentar zoom (Ctrl++)", lambda: self.set_zoom(0.1))
        nav_btn("zoom-out", "Diminuir zoom (Ctrl+-)", lambda: self.set_zoom(-0.1))
        nav_btn("fullscreen", "Tela cheia (F11)", self.toggle_fullscreen)

        self.progress = LoadingProgressBar()
        cl.addWidget(self.progress)

        self.tabs = QTabWidget()
        self.tab_bar = ChromeTabBar()
        self.tabs.setTabBar(self.tab_bar)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self.tab_changed)
        self.tab_bar.new_tab_requested.connect(lambda: self.add_tab())
        cl.addWidget(self.tabs)
        cl.insertWidget(2, nav)

        main_layout.addWidget(content)

        self.find_bar = QWidget()
        self.find_bar.setFixedHeight(36)
        self.find_bar.hide()
        fl = QHBoxLayout(self.find_bar)
        fl.setContentsMargins(8, 0, 8, 0)
        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Localizar...")
        self.find_input.setFixedWidth(300)
        self.find_input.textChanged.connect(self.find_text)
        self.find_input.returnPressed.connect(self.find_next)
        fl.addWidget(self.find_input)
        lbl = QLabel("(Enter=proximo, Shift+Enter=anterior, Esc=fechar)")
        lbl.setStyleSheet("color: #8b92a5; background: transparent;")
        fl.addWidget(lbl)
        fl.addStretch()
        close_find = IconButton("close", "Fechar (Esc)", self.find_bar, size=12, button=26)
        close_find.clicked.connect(self.find_bar.hide)
        fl.addWidget(close_find)
        cl.addWidget(self.find_bar)

        self.setup_shortcuts()
        self.update_styles()
        self.add_tab()

    def _center(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        qr = self.frameGeometry()
        qr.moveCenter(screen.availableGeometry().center())
        self.move(qr.topLeft())

    def _persist(self, key, value):
        if self.profile_manager and self.profile_name:
            self.profile_manager.update(self.profile_name, key, value)

    def _persist_search_engine(self, text):
        if text in SEARCH_ENGINES:
            self._persist("search_engine", text)

    def setup_shortcuts(self):
        shortcuts = [
            ("Ctrl+T", lambda: self.add_tab()),
            ("Ctrl+W", self.close_current_tab),
            ("Ctrl+Shift+T", self.restore_closed_tab),
            ("Ctrl+L", lambda: (self.url_bar.setFocus(), self.url_bar.selectAll())),
            ("Ctrl+D", self.add_bookmark_current),
            ("Ctrl+F", self.show_find_bar),
            ("Ctrl+R", self.current_browser_reload),
            ("F5", self.current_browser_reload),
            ("F11", self.toggle_fullscreen),
            ("Ctrl++", lambda: self.set_zoom(0.1)),
            ("Ctrl+-", lambda: self.set_zoom(-0.1)),
            ("Ctrl+0", lambda: self.set_zoom(0, reset=True)),
            ("Esc", self.handle_escape),
            ("Alt+Left", self.current_browser_back),
            ("Alt+Right", self.current_browser_forward),
            ("Ctrl+Tab", lambda: self.tabs.setCurrentIndex((self.tabs.currentIndex() + 1) % max(1, self.tabs.count()))),
            ("Ctrl+Shift+Tab", lambda: self.tabs.setCurrentIndex((self.tabs.currentIndex() - 1) % max(1, self.tabs.count()))),
            ("Ctrl+J", self.open_downloads_window),
            ("Ctrl+H", self.open_history_page),
            ("Ctrl+Shift+B", self.open_bookmarks_page),
            ("Ctrl+Shift+O", self.open_bookmarks_page),
            ("Ctrl+,", self.open_settings_page),
            ("Ctrl+Shift+N", self.open_private_window),
            ("Ctrl+Q", self.close),
        ]
        for seq, cb in shortcuts:
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(cb)

    def handle_escape(self):
        if self.isFullScreen():
            self.toggle_fullscreen()
            return
        self.find_bar.hide()
        b = self.current_browser()
        if b:
            b.stop()

    def update_styles(self):
        pal = theme_palette(self.accent_color, self.bg_secondary, self.bg_main,
                            self.dark_mode, self.is_private)
        self.setStyleSheet(generate_css(self.accent_color, self.bg_secondary, self.bg_main,
                                        self.is_private, self.dark_mode))
        self.progress.setStyleSheet(
            "QProgressBar{border:none;background:transparent;height:2px}"
            "QProgressBar::chunk{border-radius:1px;background-color:%s}" % pal["accent"]
        )
        self.bookmark_bar.setStyleSheet(
            f"QToolBar{{background:{self.bg_secondary};border:none;"
            f"border-bottom:1px solid {pal['border']};padding:2px 8px;spacing:2px;}}"
        )
        self.find_bar.setStyleSheet(
            f"QWidget{{background:{self.bg_secondary};border-top:1px solid {pal['border']};}}"
        )

        # Ícones vetoriais acompanham o tema (cinza em repouso, destaque no hover).
        self.title_bar.apply_theme(pal["muted"], pal["text"], pal["accent"])
        for btn in list(getattr(self, "_icon_buttons", [])):
            try:
                btn.set_icon_colors(pal["muted"], pal["accent"])
            except RuntimeError:
                continue
        try:
            self.btn_theme.set_icon_name("moon" if self.dark_mode else "sun")
            self.tab_bar.apply_theme(pal["muted"], pal["accent"])
            self.url_search_icon.setIcon(vector_icon("search", pal["muted"], 16))
        except (AttributeError, RuntimeError):
            pass

        app = QApplication.instance()
        if app:
            p = app.palette()
            p.setColor(QPalette.Window, QColor(self.bg_main))
            p.setColor(QPalette.Base, QColor(self.bg_main))
            p.setColor(QPalette.WindowText, QColor(pal["text"]))
            p.setColor(QPalette.Text, QColor(pal["text"]))
            app.setPalette(p)

        # A cor de fundo de cada página interna (a Home usa a cor própria dela).
        for i in range(self.tabs.count()):
            b = self.tabs.widget(i)
            if not b:
                continue
            try:
                page = b.page()
                if page is not None:
                    kind = b.property("internal_page") or ""
                    bg = self.bg_home if kind == "home" else self.bg_main
                    page.setBackgroundColor(QColor(bg))
            except (RuntimeError, AttributeError):
                pass

    def update_bookmark_bar(self):
        self.bookmark_bar.clear()
        if self.is_private or not self.db:
            return
        bookmarks = self.db.get_bookmarks()
        for b in bookmarks[:15]:
            burl = b.get("url") or ""
            t = shorten(b.get("title") or burl, 18)
            act = QAction(t, self.bookmark_bar)
            act.setToolTip(burl)
            act.triggered.connect(lambda checked=False, u=burl: self.add_tab(u))
            domain = urlparse(burl).netloc
            icon = self.favicons.icon(domain, lambda ic, a=act: self._apply_favicon_action(a, ic))
            if not icon.isNull():
                act.setIcon(icon)
            else:
                # Reserva: selo com a inicial do site (sempre "aparece logo").
                act.setIcon(self._letter_icon(t or domain, domain))
            self.bookmark_bar.addAction(act)
        if bookmarks:
            self.bookmark_bar.addSeparator()
        act_all = QAction("Todos...", self)
        act_all.setIcon(vector_icon("bookmarks", "#8b92a5", 16))
        act_all.triggered.connect(self.open_bookmarks_page)
        self.bookmark_bar.addAction(act_all)

    @staticmethod
    def _apply_favicon_action(action, icon):
        try:
            if icon is not None and not icon.isNull():
                action.setIcon(icon)
        except RuntimeError:
            # A ação já foi destruída (barra reconstruída).
            pass

    @staticmethod
    def _letter_icon(label, domain="") -> QIcon:
        """Ícone temporário com a inicial do site (usado até o favicon chegar)."""
        size = 16
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        palette = ("#ff2a5f", "#a855f7", "#3b82f6", "#10b981",
                   "#f59e0b", "#ef4444", "#ec4899", "#6366f1")
        text = "".join(ch for ch in str(label or domain or "?") if ch.isalnum())
        letter = (text[:1] or "?").upper()
        try:
            index = int(hashlib.md5(str(domain or label).encode("utf-8", "ignore")).hexdigest(), 16)
        except (TypeError, ValueError):
            index = 0
        painter = QPainter(pm)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(palette[index % len(palette)]))
            painter.drawRoundedRect(0, 0, size, size, 4, 4)
            font = QFont("Segoe UI", 9)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(pm.rect(), Qt.AlignCenter, letter)
        finally:
            painter.end()
        return QIcon(pm)

    def add_tab(self, url="home", background=False):
        browser = WebView(self)
        page = CustomWebEnginePage(self.profile, self, browser)
        browser.setPage(page)
        browser.setProperty("is_home", False)
        browser.setProperty("internal_page", "")
        browser.setProperty("zoom", 1.0)
        browser.setProperty("loading", False)

        browser.urlChanged.connect(lambda qurl, b=browser: self.handle_url_change(qurl, b))
        browser.titleChanged.connect(lambda title, b=browser: self.on_title_changed(title, b))
        browser.iconChanged.connect(lambda icon, b=browser: self.on_icon_changed(icon, b))
        browser.loadStarted.connect(lambda b=browser: self._on_load_started(b))
        browser.loadProgress.connect(lambda v, b=browser: self.on_load_progress(v, b))
        browser.loadFinished.connect(lambda ok, b=browser: self.on_load_finished(ok, b))

        i = self.tabs.addTab(browser, "Nova Guia")
        if not background:
            self.tabs.setCurrentIndex(i)

        if url is None:
            # Usado por popups (window.open): o Qt navega na página retornada.
            return browser
        if url in ("home", "upgraded_one://home"):
            self.load_speed_dial(browser)
        elif url == "upgraded_one://downloads":
            self.load_downloads_page(browser)
        elif url == "upgraded_one://history":
            self.load_history_page(browser)
        elif url == "upgraded_one://bookmarks":
            self.load_bookmarks_page(browser)
        elif url == "upgraded_one://settings":
            self.load_settings_page(browser)
        else:
            try:
                browser.setUrl(QUrl(url))
            except (RuntimeError, TypeError):
                pass
        return browser

    def add_tab_for_popup(self):
        return self.add_tab(url=None, background=True).page()

    def close_tab(self, i):
        if not (0 <= i < self.tabs.count()):
            return
        if self.tabs.count() <= 1:
            # Fechar a última aba fecha a janela (comportamento de navegador).
            self.close()
            return
        browser = self.tabs.widget(i)
        if browser is not None:
            try:
                url = browser.url().toString()
                if url and not url.startswith(("upgraded_one://", "data:", "about:")):
                    self.closed_tabs.append(url)
                    if len(self.closed_tabs) > 20:
                        self.closed_tabs.pop(0)
            except RuntimeError:
                pass
        self.tabs.removeTab(i)
        if browser is not None:
            try:
                browser.stop()
            except RuntimeError:
                pass
            # A página é filha da view e será destruída junto com ela.
            browser.deleteLater()

    def close_current_tab(self):
        if self.tabs.count() == 0:
            self.close()
            return
        self.close_tab(self.tabs.currentIndex())

    def restore_closed_tab(self):
        if self.closed_tabs:
            self.add_tab(self.closed_tabs.pop())

    def current_browser(self):
        return self.tabs.currentWidget()

    def current_browser_back(self):
        b = self.current_browser()
        if b:
            b.back()

    def current_browser_forward(self):
        b = self.current_browser()
        if b:
            b.forward()

    def current_browser_reload(self):
        b = self.current_browser()
        if b:
            b.reload()

    def current_browser_stop(self):
        b = self.current_browser()
        if b:
            b.stop()

    def go_home(self):
        b = self.current_browser()
        if b:
            self.load_speed_dial(b)

    def go_url(self):
        text = self.url_bar.text().strip()
        if not text:
            return
        internal = {
            "upgraded_one://home": self.go_home,
            "upgraded_one://settings": self.open_settings_page,
            "upgraded_one://downloads": self.open_downloads_window,
            "upgraded_one://history": self.open_history_page,
            "upgraded_one://bookmarks": self.open_bookmarks_page,
        }
        if text in internal:
            internal[text]()
            return
        if text.startswith(("http://", "https://")):
            url = text
        elif is_valid_url(text):
            url = "https://" + text
        else:
            engine = self.search_combo.currentText()
            template = SEARCH_ENGINES.get(engine, SEARCH_ENGINES["Google"])
            url = template.format(urllib.parse.quote_plus(text))
        b = self.current_browser()
        if b:
            b.setUrl(QUrl(url))

    def handle_url_change(self, qurl, browser):
        try:
            url_str = qurl.toString()
        except RuntimeError:
            return

        if "upgraded_one://history?action=clear" in url_str:
            self.clear_history_from_page()
            QTimer.singleShot(100, lambda b=browser: self._safe_load_history(b))
            return

        try:
            if not (url_str.startswith("data:text/html") or "upgraded_one://" in url_str):
                browser.setProperty("is_home", False)
                browser.setProperty("internal_page", "")
                page = browser.page()
                if page is not None:
                    page.setBackgroundColor(QColor(self.bg_main))

            if browser == self.current_browser():
                kind = browser.property("internal_page") or ""
                if kind:
                    self.url_bar.setText("upgraded_one://" + kind)
                else:
                    self.url_bar.setText(url_str)
        except RuntimeError:
            pass

    def _safe_load_history(self, browser):
        try:
            if self.tabs.indexOf(browser) != -1:
                self.load_history_page(browser)
        except RuntimeError:
            pass

    def _on_load_started(self, browser):
        try:
            browser.setProperty("loading", True)
            if browser == self.current_browser():
                self.progress.setVisible(True)
        except RuntimeError:
            pass

    def tab_changed(self, index):
        try:
            browser = self.tabs.widget(index)
            if not browser:
                return
            self.handle_url_change(browser.url(), browser)
            z = browser.property("zoom") or 1.0
            browser.setZoomFactor(float(z))
            # QWebEngineView do Qt5 não tem isLoading(): usamos uma flag própria.
            self.progress.setVisible(bool(browser.property("loading")))
        except (RuntimeError, TypeError, ValueError):
            pass

    def on_title_changed(self, title, browser):
        try:
            idx = self.tabs.indexOf(browser)
            if idx == -1:
                return
            if browser.property("is_home"):
                self.tabs.setTabText(idx, "Home")
                return
            if not title or title.startswith(("data:", "upgraded_one://")) or "charset=UT" in title:
                return
            self.tabs.setTabText(idx, shorten(title, 28))
        except RuntimeError:
            pass

    def on_icon_changed(self, icon, browser):
        try:
            idx = self.tabs.indexOf(browser)
            if idx == -1 or icon is None:
                return
            if browser.property("is_home"):
                logo = app_icon()
                if not logo.isNull():
                    self.tabs.setTabIcon(idx, logo)
            elif not icon.isNull():
                self.tabs.setTabIcon(idx, icon)
        except RuntimeError:
            pass

    def on_load_progress(self, value, browser=None):
        # Só a aba visível controla a barra de progresso.
        if browser is not None:
            try:
                if browser != self.current_browser():
                    return
            except RuntimeError:
                return
        self.progress.setValue(value)
        self.progress.setVisible(value < 100)

    def on_load_finished(self, ok, browser):
        try:
            browser.setProperty("loading", False)
            if browser == self.current_browser():
                self.progress.setValue(100)
                self.progress.setVisible(False)
            if ok and self.db and browser == self.current_browser():
                url_str = browser.url().toString()
                if (url_str and not url_str.startswith(("upgraded_one://", "data:", "about:"))
                        and not browser.property("is_home")):
                    title = browser.title() or url_str
                    if not title.startswith(("data:", "upgraded_one://")):
                        self.db.add_history(url_str, title)
        except RuntimeError:
            pass

    def on_download_requested(self, download):
        try:
            filename = download.suggestedFileName() or "download"
            default_dir = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
            try:
                os.makedirs(default_dir, exist_ok=True)
            except OSError:
                default_dir = str(Path.home())
            default_path = os.path.join(default_dir, filename)
            filepath, _ = QFileDialog.getSaveFileName(self, "Salvar Arquivo", default_path)
            if not filepath:
                try:
                    download.cancel()
                except Exception:
                    pass
                return
            download.setPath(filepath)
            download.accept()
            self.download_manager.add_download(download)
        except (RuntimeError, AttributeError):
            pass

    def _colors(self):
        return {"accent": self.accent_color, "bg_secondary": self.bg_secondary,
                "bg_main": self.bg_main, "bg_home": self.bg_home}

    def _prepare_internal_page(self, browser, is_home=False, kind=""):
        browser.setProperty("is_home", is_home)
        # O URL real de uma página criada com setHtml() é um data: enorme, então
        # guardamos o tipo da página para poder recarregá-la e mostrar um
        # endereço amigável na barra.
        browser.setProperty("internal_page", kind or ("home" if is_home else ""))
        try:
            page = browser.page()
            if page is not None:
                page.setBackgroundColor(QColor(self.bg_home if is_home else self.bg_main))
        except (RuntimeError, AttributeError):
            pass
        self._set_internal_tab_icon(browser)

    def _set_internal_tab_icon(self, browser):
        """Páginas internas usam a logo do app como ícone da aba."""
        try:
            idx = self.tabs.indexOf(browser)
            icon = app_icon()
            if idx != -1 and not icon.isNull():
                self.tabs.setTabIcon(idx, icon)
        except RuntimeError:
            pass

    def load_speed_dial(self, browser):
        self._prepare_internal_page(browser, is_home=True)
        engine = self.search_combo.currentText()
        template = SEARCH_ENGINES.get(engine, SEARCH_ENGINES["Google"])
        bookmarks = self.db.get_bookmarks() if self.db else []
        html = InternalPages.speed_dial(self._colors(), self.is_private, bookmarks,
                                        template, self.dark_mode)
        browser.setHtml(html, QUrl("upgraded_one://home"))
        idx = self.tabs.indexOf(browser)
        if idx != -1:
            self.tabs.setTabText(idx, "Home")
            icon = app_icon()
            self.tabs.setTabIcon(idx, icon)

    def load_downloads_page(self, browser):
        self._prepare_internal_page(browser, kind="downloads")
        a = self.accent_color
        content = f"""
<div class="header">
    <div class="title-wrap">
        <div class="title-icon">{svg_icon('download', a, 22)}</div>
        <div>
            <h1>Downloads</h1>
            <p class="subtitle">Arquivos baixados neste perfil</p>
        </div>
    </div>
</div>
<div class="panel">
    <h3>{svg_icon('download', 'currentColor', 16)}Janela de downloads</h3>
    <p>O historico completo dos downloads fica na janela dedicada.</p>
    <p>Abra com <b>Ctrl+J</b> ou pelo icone de downloads na barra lateral.</p>
</div>
"""
        html = InternalPages._page("Downloads", self._colors(), self.dark_mode, content)
        browser.setHtml(html, QUrl("upgraded_one://downloads"))
        idx = self.tabs.indexOf(browser)
        if idx != -1:
            self.tabs.setTabText(idx, "Downloads")

    def load_history_page(self, browser):
        self._prepare_internal_page(browser, kind="history")
        history = self.db.get_history() if self.db else []
        html = InternalPages.history_page(self._colors(), history, self.dark_mode)
        browser.setHtml(html, QUrl("upgraded_one://history"))
        idx = self.tabs.indexOf(browser)
        if idx != -1:
            self.tabs.setTabText(idx, "Historico")

    def load_bookmarks_page(self, browser):
        self._prepare_internal_page(browser, kind="bookmarks")
        bookmarks = self.db.get_bookmarks() if self.db else []
        html = InternalPages.bookmarks_page(self._colors(), bookmarks, self.dark_mode)
        browser.setHtml(html, QUrl("upgraded_one://bookmarks"))
        idx = self.tabs.indexOf(browser)
        if idx != -1:
            self.tabs.setTabText(idx, "Favoritos")

    def load_settings_page(self, browser):
        self._prepare_internal_page(browser, kind="settings")
        data = self.profile_data if not self.is_private else {"name": "Anonimo"}
        try:
            cert_count = self.certificate_manager.count()
            active_cert = self.certificate_manager.active_certificate()
        except (AttributeError, RuntimeError):
            cert_count = 0
            active_cert = None
        cert_active = _cert_name(active_cert) if active_cert is not None else ""
        html = InternalPages.settings_page(
            self._colors(), data, self.dark_mode,
            cert_count=cert_count, cert_active=cert_active,
        )
        browser.setHtml(html, QUrl("upgraded_one://settings"))
        idx = self.tabs.indexOf(browser)
        if idx != -1:
            self.tabs.setTabText(idx, "Configuracoes")

    def add_bookmark_current(self):
        b = self.current_browser()
        if not b or not self.db:
            if self.is_private:
                QMessageBox.information(self, "Favoritos", "Favoritos estao desativados no modo anonimo.")
            return
        try:
            url = b.url().toString()
            if not url or url.startswith(("upgraded_one://", "data:", "about:")):
                return
            title = b.title() or ""
        except RuntimeError:
            return
        if not title or title.startswith(("data:", "upgraded_one://")) or len(title) > 200:
            title = urlparse(url).netloc or urlparse(url).path or "Pagina sem titulo"
        self.db.add_bookmark(url, title)
        self.update_bookmark_bar()
        self.refresh_internal_pages()
        try:
            QMessageBox.information(self, "Favorito", f"Adicionado: {shorten(title, 30)}")
        except RuntimeError:
            pass

    def show_find_bar(self):
        self.find_bar.show()
        self.find_input.setFocus()
        self.find_input.selectAll()

    def find_text(self, text):
        b = self.current_browser()
        if b:
            try:
                b.findText(text)
            except RuntimeError:
                pass

    def find_next(self):
        mods = QApplication.keyboardModifiers()
        b = self.current_browser()
        if not b:
            return
        try:
            if mods & Qt.ShiftModifier:
                b.findText(self.find_input.text(), QWebEnginePage.FindBackward)
            else:
                b.findText(self.find_input.text())
        except RuntimeError:
            pass

    def set_zoom(self, delta, reset=False):
        b = self.current_browser()
        if not b:
            return
        self.zoom_factor = 1.0 if reset else max(0.25, min(5.0, self.zoom_factor + delta))
        try:
            b.setZoomFactor(self.zoom_factor)
            b.setProperty("zoom", self.zoom_factor)
        except RuntimeError:
            pass

    def toggle_fullscreen(self):
        if self.isFullScreen():
            if getattr(self, "_was_maximized", False):
                self.showMaximized()
            else:
                self.showNormal()
            self.title_bar.show()
            self.bookmark_bar.show()
        else:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()
            self.title_bar.hide()
            self.bookmark_bar.hide()

    def open_devtools(self, view):
        try:
            page = view.page()
            if page is None:
                return
            if self._devtools is not None:
                try:
                    self._devtools.close()
                    self._devtools.deleteLater()
                except RuntimeError:
                    pass
                self._devtools = None
            dev = QWebEngineView()
            dev.setWindowTitle("DevTools — Upgraded One")
            dev.resize(1000, 600)
            page.setDevToolsPage(dev.page())
            dev.show()
            self._devtools = dev
        except RuntimeError:
            pass

    def open_downloads_window(self):
        try:
            self.download_manager.show()
            self.download_manager.raise_()
            self.download_manager.activateWindow()
        except RuntimeError:
            pass

    def open_history_page(self):
        self.add_tab("upgraded_one://history")

    def open_bookmarks_page(self):
        self.add_tab("upgraded_one://bookmarks")

    def open_settings_page(self):
        self.add_tab("upgraded_one://settings")

    def open_certificate_manager(self):
        try:
            dialog = CertificateDialog(self.certificate_manager, self)
            dialog.exec_()
            self.refresh_internal_pages()
        except (RuntimeError, AttributeError):
            pass

    def import_certificate_dialog(self):
        """Importa um certificado A1 (.pfx/.p12) a partir de um atalho."""
        try:
            if self.certificate_manager.import_pkcs12():
                self.refresh_internal_pages()
        except (RuntimeError, AttributeError):
            pass

    def open_private_window(self):
        try:
            priv = MainWindow(is_private=True, profile_manager=self.profile_manager)
        except Exception:
            return
        self._private_windows.append(priv)
        priv.destroyed.connect(lambda *_, w=priv: self._discard_private(w))
        priv.show()

    def _discard_private(self, win):
        try:
            self._private_windows = [w for w in self._private_windows if w is not win]
        except RuntimeError:
            pass

    # ─── Ações das páginas internas (limpar cache/histórico) ────────────────
    def handle_internal_action(self, url_str):
        """Executa ações disparadas pelas páginas internas.

        Chamado por CustomWebEnginePage.acceptNavigationRequest: devolve True
        quando a URL era uma ação interna (e a navegação deve ser cancelada).
        """
        if not isinstance(url_str, str) or not url_str.startswith(INTERNAL_ACTION_BASE):
            return False
        action = url_str[len(INTERNAL_ACTION_BASE):].split("?")[0].split("#")[0].strip("/").lower()
        handlers = {
            "clear-history": self.clear_history_action,
            "clear-cache": self.clear_cache_action,
            "clear-all": self.clear_all_action,
        }
        handler = handlers.get(action)
        if handler is not None:
            handler()
            return True

        # Personalizacao vinda da pagina de Configuracoes. Os dialogos modais
        # nao podem abrir dentro deste callback de navegacao, por isso o
        # QTimer.singleShot adia a execucao para o proximo ciclo do event loop.
        if action == "theme":
            QTimer.singleShot(0, self.toggle_dark_mode)
            return True
        if action.startswith("color/"):
            chooser = {
                "accent": self.choose_accent_color,
                "panel": self.choose_panel_color,
                "main": self.choose_bg_color,
                "home": self.choose_home_bg_color,
            }.get(action.split("/", 1)[1].strip())
            if chooser is None:
                return False
            QTimer.singleShot(0, chooser)
            return True
        if action == "certificate/manage":
            QTimer.singleShot(0, self.open_certificate_manager)
            return True
        if action == "certificate/import":
            QTimer.singleShot(0, self.import_certificate_dialog)
            return True
        return False

    def _ask(self, title, text):
        try:
            reply = QMessageBox.question(
                self, title, text,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            return reply == QMessageBox.Yes
        except RuntimeError:
            return False

    def _notify(self, title, text):
        try:
            QMessageBox.information(self, title, text)
        except RuntimeError:
            pass

    def clear_history_action(self):
        if not self.db:
            self._notify("Historico", "O historico nao e salvo no modo anonimo.")
            return
        if not self._ask("Limpar historico",
                         "Apagar todo o historico de navegacao deste perfil?"):
            return
        self.db.clear_history()
        self.refresh_internal_pages()
        self._notify("Historico", "Historico de navegacao apagado com sucesso!")
        return True

    def clear_cache_action(self):
        if not self._ask("Limpar cache",
                         "Apagar o cache de paginas (arquivos temporarios) deste perfil?"):
            return
        self.clear_cache()
        self._notify("Cache", "Cache limpo com sucesso!")
        return True

    def clear_all_action(self):
        if not self._ask("Limpar cache e historico",
                         "Apagar o cache de paginas e todo o historico deste perfil?"):
            return
        self.clear_cache()
        if self.db:
            self.db.clear_history()
        self.refresh_internal_pages()
        self._notify("Privacidade", "Cache e historico apagados com sucesso!")
        return True

    def clear_cache(self):
        """Limpa o cache HTTP/visitados do perfil e os favicons guardados."""
        profile = self.profile
        try:
            profile.clearHttpCache()
        except (AttributeError, RuntimeError):
            pass
        try:
            profile.clearAllVisitedLinks()
        except (AttributeError, RuntimeError):
            pass
        try:
            self.favicons.forget_all()
        except (AttributeError, RuntimeError):
            pass
        # Cache em disco: só remove subpastas de cache do próprio perfil para
        # não apagar dados permanentes (cookies, logins, storage).
        try:
            cache_path = str(profile.cachePath() or "").strip()
        except (AttributeError, RuntimeError):
            cache_path = ""
        if cache_path:
            try:
                cache_dir = Path(cache_path)
                if cache_dir.is_dir() and cache_dir != Path.home():
                    for child in cache_dir.iterdir():
                        name = child.name.lower()
                        if "cache" not in name and "gpu" not in name:
                            continue
                        try:
                            if child.is_dir():
                                shutil.rmtree(child, ignore_errors=True)
                            else:
                                child.unlink()
                        except OSError:
                            pass
            except (OSError, RuntimeError, ValueError):
                pass

    def refresh_internal_pages(self, kinds=None):
        """Recarrega as páginas internas abertas depois de limpar dados."""
        wanted = set(kinds) if kinds else {"home", "history", "bookmarks", "settings"}
        for i in range(self.tabs.count()):
            browser = self.tabs.widget(i)
            if browser is None:
                continue
            try:
                kind = browser.property("internal_page") or ""
            except RuntimeError:
                continue
            if kind not in wanted:
                continue
            try:
                if kind == "home":
                    self.load_speed_dial(browser)
                elif kind == "history":
                    self.load_history_page(browser)
                elif kind == "bookmarks":
                    self.load_bookmarks_page(browser)
                elif kind == "settings":
                    self.load_settings_page(browser)
                elif kind == "downloads":
                    self.load_downloads_page(browser)
            except RuntimeError:
                continue

    def clear_history_from_page(self):
        # Mantido por compatibilidade com links antigos (upgraded_one://...).
        self.clear_history_action()

    def toggle_dark_mode(self):
        old_pal = DEFAULT_COLORS["dark" if self.dark_mode else "light"]
        self.dark_mode = not self.dark_mode
        new_pal = DEFAULT_COLORS["dark" if self.dark_mode else "light"]
        # Só troca as cores que ainda estavam no padrão do tema anterior, para
        # não apagar a personalização feita pelo usuário.
        if self.accent_color.lower() == old_pal["accent"].lower():
            self.accent_color = new_pal["accent"]
        if self.bg_main.lower() == old_pal["main"].lower():
            self.bg_main = new_pal["main"]
        if self.bg_secondary.lower() == old_pal["secondary"].lower():
            self.bg_secondary = new_pal["secondary"]
        if self.bg_home.lower() == old_pal["home"].lower():
            self.bg_home = new_pal["home"]

        self.update_styles()
        self.refresh_internal_pages()
        self._persist("dark_mode", self.dark_mode)
        self._persist("accent_color", self.accent_color)
        self._persist("bg_main", self.bg_main)
        self._persist("bg_secondary", self.bg_secondary)
        self._persist("bg_home", self.bg_home)

    def closeEvent(self, event):
        try:
            for w in list(self._private_windows):
                try:
                    w.close()
                except RuntimeError:
                    pass
            self._private_windows = []

            if self._devtools is not None:
                try:
                    self._devtools.close()
                    self._devtools.deleteLater()
                except RuntimeError:
                    pass
                self._devtools = None

            try:
                self.download_manager.close()
                self.download_manager.deleteLater()
            except RuntimeError:
                pass

            # Libera as páginas antes do perfil ser destruído.
            for i in reversed(range(self.tabs.count())):
                b = self.tabs.widget(i)
                self.tabs.removeTab(i)
                if b is not None:
                    try:
                        b.stop()
                    except RuntimeError:
                        pass
                    b.deleteLater()
        except Exception:
            pass
        super().closeEvent(event)

    def _choose_color(self, current, title, key):
        color = QColorDialog.getColor(QColor(current), self, title)
        if color.isValid():
            return color.name()
        return None

    def apply_custom_color(self, key, value):
        """Aplica e salva uma cor personalizada, recarregando as páginas internas."""
        value = safe_color(value, getattr(self, key, "#ff2a5f"))
        setattr(self, key, value)
        self.update_styles()
        self.refresh_internal_pages()
        self._persist(key, value)

    def choose_accent_color(self):
        c = self._choose_color(self.accent_color, "Cor de destaque", "accent_color")
        if c:
            self.apply_custom_color("accent_color", c)

    def choose_panel_color(self):
        c = self._choose_color(self.bg_secondary, "Cor dos paineis", "bg_secondary")
        if c:
            self.apply_custom_color("bg_secondary", c)

    def choose_bg_color(self):
        c = self._choose_color(self.bg_main, "Cor de fundo", "bg_main")
        if c:
            self.apply_custom_color("bg_main", c)

    def choose_home_bg_color(self):
        c = self._choose_color(self.bg_home, "Cor da Home", "bg_home")
        if c:
            self.apply_custom_color("bg_home", c)


# ═══════════════════════════════════════════════════════════════════════════════
# SELETOR DE PERFIS
# ═══════════════════════════════════════════════════════════════════════════════
class ProfileCard(QWidget):
    clicked = pyqtSignal(str)
    delete_clicked = pyqtSignal(str)

    def __init__(self, name, accent="#ff2a5f", parent=None):
        super().__init__(parent)
        name = str(name or "").strip() or "Perfil"
        self.profile_name = name
        self.setFixedSize(172, 196)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"""
            ProfileCard {{
                background-color: #11141d;
                border: 1px solid #232838;
                border-radius: 18px;
            }}
            ProfileCard:hover {{
                background-color: #171b26;
                border: 1px solid {accent};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 18, 12, 12)

        avatar = QLabel(name[0].upper())
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setFixedSize(64, 64)
        avatar.setStyleSheet(f"""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 {accent}, stop:1 {rgba(accent, 0.45)});
            color: white;
            font-size: 27px;
            font-weight: 800;
            border-radius: 32px;
        """)
        layout.addWidget(avatar, alignment=Qt.AlignCenter)

        name_label = QLabel(name)
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setStyleSheet(
            "color: #e6ebf5; font-size: 14px; font-weight: 700; background: transparent;")
        name_label.setWordWrap(True)
        layout.addWidget(name_label)

        hint = QLabel("Clique para abrir")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color: #8b92a5; font-size: 10.5px; background: transparent;")
        layout.addWidget(hint)

        layout.addStretch()

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addStretch()
        del_btn = IconButton("trash", "Excluir perfil", self, size=12, button=26)
        del_btn.set_icon_colors("#8b92a5", "#ef4444")
        del_btn.clicked.connect(lambda: self.delete_clicked.emit(self.profile_name))
        bottom.addWidget(del_btn)
        layout.addLayout(bottom)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.profile_name)


class ProfileSelector(QDialog):
    def __init__(self, profile_manager, parent=None):
        super().__init__(parent)
        self.profile_manager = profile_manager
        self.selected_profile = None
        self.setWindowTitle(f"{APP_NAME} — Seletor de Perfis")
        self.setMinimumSize(720, 540)
        self.setStyleSheet("QDialog { background-color: #0b0c10; border: 1px solid #1a1c23; }")

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        header = QWidget()
        header.setFixedHeight(154)
        header.setStyleSheet(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            " stop:0 #ff2a5f, stop:0.55 #d946a8, stop:1 #a855f7);"
        )
        hlayout = QVBoxLayout(header)
        hlayout.setContentsMargins(0, 16, 0, 14)
        hlayout.setSpacing(6)
        hlayout.setAlignment(Qt.AlignCenter)

        logo = QLabel()
        logo.setFixedSize(48, 48)
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet("background: rgba(0,0,0,0.20); border-radius: 14px;")
        pm = logo_pixmap(34)
        if not pm.isNull():
            logo.setPixmap(pm)
        else:
            logo.setPixmap(vector_icon("home-color", "#ffffff", 26).pixmap(26, 26))
        hlayout.addWidget(logo, alignment=Qt.AlignCenter)

        title = QLabel("UPGRADED ONE")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: white; font-size: 30px; font-weight: 900; letter-spacing: 4px; background: transparent;")
        hlayout.addWidget(title)

        subtitle = QLabel("ESCOLHA SEU PERFIL")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: rgba(255,255,255,0.82); font-size: 11px; font-weight: 700; letter-spacing: 2.4px; background: transparent;")
        hlayout.addWidget(subtitle)
        main_layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: #0b0c10; border: none;")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        container = QWidget()
        self.grid_layout = QGridLayout(container)
        self.grid_layout.setSpacing(20)
        self.grid_layout.setContentsMargins(30, 30, 30, 30)
        self.grid_layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        scroll.setWidget(container)
        main_layout.addWidget(scroll)

        footer = QWidget()
        footer.setFixedHeight(74)
        footer.setStyleSheet("background: #07080a; border-top: 1px solid #1a1c23;")
        flayout = QHBoxLayout(footer)
        flayout.setContentsMargins(22, 0, 22, 0)
        flayout.setSpacing(12)

        btn_new = QPushButton("NOVO PERFIL")
        btn_new.setIcon(vector_icon("plus", "#ffffff", 16))
        btn_new.setIconSize(QSize(16, 16))
        btn_new.setFixedHeight(40)
        btn_new.setCursor(Qt.PointingHandCursor)
        btn_new.setStyleSheet("""
            QPushButton { background: #ff2a5f; color: white; border: none; border-radius: 10px;
                          padding: 0 22px; font-size: 12.5px; font-weight: 700; letter-spacing: .4px; }
            QPushButton:hover { background: #ff4777; }
        """)
        btn_new.clicked.connect(self.create_profile)
        flayout.addWidget(btn_new)

        flayout.addStretch()

        btn_anon = QPushButton("MODO ANONIMO")
        btn_anon.setIcon(vector_icon("incognito", "#ffffff", 17))
        btn_anon.setIconSize(QSize(17, 17))
        btn_anon.setFixedHeight(40)
        btn_anon.setCursor(Qt.PointingHandCursor)
        btn_anon.setStyleSheet("""
            QPushButton { background: #a855f7; color: white; border: none; border-radius: 10px;
                          padding: 0 22px; font-size: 12.5px; font-weight: 700; letter-spacing: .4px; }
            QPushButton:hover { background: #b46cf5; }
        """)
        btn_anon.clicked.connect(self.open_anonymous)
        flayout.addWidget(btn_anon)

        main_layout.addWidget(footer)

        self.refresh_profiles()

    def refresh_profiles(self):
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        profiles = self.profile_manager.list()
        colors = ["#ff2a5f", "#a855f7", "#3b82f6", "#10b981", "#f59e0b",
                  "#ef4444", "#ec4899", "#6366f1"]
        for i, name in enumerate(profiles):
            card = ProfileCard(name, colors[i % len(colors)])
            card.clicked.connect(self.on_profile_clicked)
            card.delete_clicked.connect(self.on_delete_clicked)
            self.grid_layout.addWidget(card, i // 3, i % 3)

    def on_profile_clicked(self, name):
        self.selected_profile = name
        self.accept()

    def on_delete_clicked(self, name):
        reply = QMessageBox.question(
            self, "Excluir Perfil",
            f"Tem certeza que deseja excluir o perfil '{name}'?\n\nTodos os dados serao perdidos.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.profile_manager.delete(name)
            self.refresh_profiles()

    def create_profile(self):
        name, ok = QInputDialog.getText(self, "Novo Perfil", "Nome do perfil:")
        if ok and name.strip():
            if self.profile_manager.create(name.strip()):
                self.refresh_profiles()
            else:
                QMessageBox.warning(self, "Erro", "Perfil ja existe ou nome invalido.")

    def open_anonymous(self):
        self.selected_profile = "__anonymous__"
        self.accept()


# ═══════════════════════════════════════════════════════════════════════════════
# PONTO DE ENTRADA
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    install_exception_hook()

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("UpgradedOne")
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    pm = ProfileManager()
    if not pm.list():
        pm.create("Padrao")

    selector = ProfileSelector(pm)
    if not icon.isNull():
        selector.setWindowIcon(icon)
    try:
        result = selector.exec_()
    except Exception:
        result = QDialog.Rejected
    if result != QDialog.Accepted or not selector.selected_profile:
        return 0

    if selector.selected_profile == "__anonymous__":
        window = MainWindow(is_private=True, profile_manager=pm)
    else:
        window = MainWindow(profile_name=selector.selected_profile, profile_manager=pm)
    window.show()

    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())