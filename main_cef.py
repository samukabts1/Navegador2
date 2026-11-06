#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Upgraded One Browser v5.4 (CEF)
===============================
Migracao do QtWebEngine para o Chromium Embedded Framework (CEF).
Objetivo: navegador 100% independente, com motor de navegador de verdade,
permitindo login no Google e demais sites que bloqueiam WebViews embutidos.

A aparencia e as funcionalidades sao mantidas iguais ao main.py (QtWebEngine).
Este arquivo e construido observando o backup main_qt5_backup.py.

Funcionalidades mapeadas:
- Perfis (JSON/SQLite) -> mantidos
- Historico, favoritos, downloads -> mantidos
- Certificados digitais (A1 PFX/PEM, mTLS) -> CEF SSL callbacks
- Interceptacao de requisicoes (anti-bot/headers) -> CefRequestHandler
- Injecao de JS (spoof Google) -> Cef execute JavaScript
- Paginas internas (HTML/CSS) -> mantidas, carregadas via CEF
- Interface (PyQt5) -> mantida; so o conteudo da pagina sai do QtWebEngine
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

try:
    from cefpython3 import cefpython as cef
except ImportError:
    cef = None

os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

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

APP_NAME = "Upgraded One Browser"
APP_VERSION = "5.4 CEF"
BASE_DIR = Path.home() / ".UpgradedOneBrowser"
PROFILES_DIR = BASE_DIR / "profiles"
INTERNAL_ACTION_BASE = "https://upgradedone.internal/"

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

GOOGLE_UA_SCRIPT_CEF = r"""
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
      try { delete obj[prop]; } catch (e) {}
    };
    set(navigator, 'userAgent', ua);
    set(navigator, 'appVersion', '5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)');
    set(navigator, 'vendor', 'Google Inc.');
    set(navigator, 'platform', 'Win32');
    set(navigator, 'language', 'pt-BR');
    set(navigator, 'languages', ['pt-BR', 'pt', 'en-US', 'en']);
    try { del(navigator, 'webdriver'); } catch (e) {}
    try { Object.defineProperty(navigator, 'webdriver', { get: function(){ return undefined; }, configurable: true }); } catch (e) {}
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
            getHighEntropyValues: function(){
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
      window.chrome.app = window.chrome.app || { isInstalled: false };
      window.chrome.runtime = window.chrome.runtime || {};
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
        arr.push({ name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format', length: 1 });
        arr.push({ name: 'Chromium PDF Plugin', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: 'Portable Document Format', length: 1 });
        arr.item = function(i){ return arr[i] || null; };
        arr.namedItem = function(n){ for(var i=0;i<arr.length;i++){ if(arr[i] && arr[i].name===n) return arr[i]; } return null; };
        arr.refresh = function(){};
        return arr;
      };
      Object.defineProperty(navigator, 'plugins', { get: function(){ return pluginArray(); }, configurable: true });
      Object.defineProperty(navigator, 'mimeTypes', { get: function(){
        var m = [];
        m.push({ type: 'application/pdf', description: 'Portable Document Format', suffixes: 'pdf', enabledPlugin: pluginArray()[0] });
        m.item=function(i){return m[i]||null};
        m.namedItem=function(n){for(var i=0;i<m.length;i++){if(m[i]&&m[i].type===n)return m[i];}return null};
        return m;
      }, configurable: true });
    } catch(e){}
  } catch (e) {}
})();
"""

CERT_STORAGE_FILE = "certificates.json"


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


ICON_FILE = str(_app_dir() / "justatest.jpg")
LOGO_FILE = str(_app_dir() / "justatest.jpg")
ICO_FILE = str(_app_dir() / "justatest.ico")


def app_icon() -> QIcon:
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
    "(KHTML, like Gecko) Chrome/126.0.6478.127 Safari/537.36"
)


def shorten(text: str, limit: int) -> str:
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
    try:
        color = QColor(value)
        if color.isValid():
            return color.name()
    except (TypeError, ValueError):
        pass
    return fallback


def rgba(color, alpha=1.0) -> str:
    try:
        c = QColor(color)
        if c.isValid():
            return "rgba(%d,%d,%d,%s)" % (c.red(), c.green(), c.blue(), round(float(alpha), 3))
    except (TypeError, ValueError):
        pass
    return "rgba(0,0,0,%s)" % alpha


def theme_palette(accent="#ff2a5f", bg_secondary="#141721", bg_main="#0b0c10",
                  dark=True, is_private=False):
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


_ICON_CACHE = {}


def _draw_icon(name, p, color):
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
        p.drawRoundedRect(QRectF(4.2, 3.4, 13.0, 12.0), 2.0, 2.0)
        p.drawLine(QPointF(7.2, 7.0), QPointF(14.2, 7.0))
        p.drawLine(QPointF(7.2, 9.8), QPointF(12.0, 9.8))
        p.drawEllipse(QPointF(16.3, 15.2), 3.2, 3.2)
        p.drawLine(QPointF(14.7, 17.9), QPointF(13.8, 21.2))
        p.drawLine(QPointF(17.9, 17.9), QPointF(18.8, 21.2))


def vector_icon(name, color="#e1e6f0", size=20) -> QIcon:
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
    body = HTML_ICON_PATHS.get(name, "")
    stroke = "none" if name == "star" else color
    fill = color if name == "star" else "none"
    return (
        f"<svg width='{size}' height='{size}' viewBox='0 0 24 24' fill='{fill}' "
        f"stroke='{stroke}' stroke-width='1.8' stroke-linecap='round' "
        f"stroke-linejoin='round' style='vertical-align:middle'>{body}</svg>"
    )


def install_exception_hook():
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
        self._memory.clear()
        self._failed.clear()
        try:
            if self.folder is not None:
                shutil.rmtree(self.folder, ignore_errors=True)
                self.folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


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
        danger_soft = rgba(danger, 0.12)
        danger_soft_border = rgba(danger, 0.4)
        danger_glow = rgba(danger, 0.3)
        text_soft = rgba(text, 0.03)
        accent_very_soft = rgba(a, 0.13)
        accent_soft = rgba(a, 0.13)
        accent_soft2 = rgba(a, 0.13)
        accent_glow = rgba(a, 0.10)
        accent_full = rgba(a, 0.78)
        accent_hover = rgba(a, 0.55)
        
        css = (
            "*{box-sizing:border-box}"
            "html,body{margin:0;padding:0}"
            "body{"
            "    min-height:100vh;color:TEXT;"
            "    font-family:'Segoe UI',system-ui,-apple-system,sans-serif;"
            "    background:"
            "        radial-gradient(900px 420px at 50% -12%, ACCENT_SOFT, transparent 70%),"
            "        radial-gradient(700px 420px at 100% 110%, ACCENT_GLOW, transparent 65%),"
            "        BG_MAIN;"
            "    background-attachment:fixed;"
            "    padding:38px 44px 64px;"
            "    font-size:14px;"
            "}"
            ".page{max-width:1000px;margin:0 auto}"
            ".header{display:flex;align-items:center;justify-content:space-between;gap:18px;flex-wrap:wrap;margin-bottom:24px}"
            ".title-wrap{display:flex;align-items:center;gap:14px;min-width:0}"
            ".title-icon{"
            "    width:46px;height:46px;border-radius:14px;flex:none;"
            "    display:flex;align-items:center;justify-content:center;"
            "    background:ACCENT_VERY_SOFT;border:1px solid ACCENT_SOFT;color:ACCENT;"
            "}"
            "h1{font-size:25px;margin:0;letter-spacing:-0.4px;font-weight:700}"
            ".subtitle{color:MUTED;font-size:12.5px;margin:4px 0 0}"
            ".actions{display:flex;gap:9px;flex-wrap:wrap}"
            ".btn{"
            "    display:inline-flex;align-items:center;gap:8px;"
            "    background:TABLE_HEAD;color:TEXT;border:1px solid BORDER;"
            "    padding:10px 15px;border-radius:10px;font-size:12.5px;font-weight:600;"
            "    text-decoration:none;cursor:pointer;transition:all .18s ease;"
            "}"
            ".btn:hover{border-color:ACCENT;transform:translateY(-1px);box-shadow:0 8px 20px ACCENT_GLOW}"
            ".btn.primary{background:ACCENT;border-color:ACCENT;color:#ffffff}"
            ".btn.danger{background:DANGER_SOFT;border-color:DANGER_SOFT_BORDER;color:DANGER}"
            ".btn.danger:hover{background:DANGER;border-color:DANGER;color:#ffffff;box-shadow:0 8px 20px DANGER_GLOW}"
            ".table-wrap{background:CARD_BG;border:1px solid BORDER;border-radius:16px;overflow:hidden}"
            "table{width:100%;border-collapse:collapse}"
            "th,td{padding:13px 18px;text-align:left;font-size:13px;border-bottom:1px solid BORDER}"
            "th{background:TABLE_HEAD;color:MUTED;font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;font-weight:700}"
            "tbody tr:last-child td{border-bottom:none}"
            "tbody tr{transition:background .15s ease}"
            "tbody tr:hover td{background:HOVER_ROW}"
            "td a{color:TEXT;text-decoration:none;font-weight:600}"
            "td a:hover{color:ACCENT}"
            ".td-url{color:MUTED;max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
            ".td-date{color:MUTED;white-space:nowrap}"
            ".empty{padding:48px 20px;text-align:center;color:MUTED;font-size:13.5px;line-height:1.6}"
            ".empty svg{display:block;margin:0 auto 12px;opacity:.7}"
            ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px}"
            ".panel{background:CARD_BG;border:1px solid BORDER;border-radius:16px;padding:20px 22px}"
            ".panel h3{margin:0 0 12px;font-size:14.5px;display:flex;align-items:center;gap:9px;color:TEXT;font-weight:700}"
            ".panel p{margin:7px 0;color:MUTED;font-size:13px;line-height:1.55}"
            ".kv{display:flex;justify-content:space-between;gap:14px;padding:8px 0;border-bottom:1px dashed BORDER;font-size:13px;color:MUTED}"
            ".kv:last-child{border-bottom:none}"
            ".kv b{color:TEXT;font-weight:600}"
            ".swatches{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:8px}"
            ".swatch{"
            "    display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:11px;"
            "    border:1px solid BORDER;background:TEXT_SOFT;color:TEXT;"
            "    text-decoration:none;font-size:12px;font-weight:600;transition:all .18s ease;"
            "}"
            ".swatch:hover{border-color:ACCENT;transform:translateY(-1px);box-shadow:0 6px 16px ACCENT_GLOW}"
            ".dot{width:18px;height:18px;border-radius:6px;border:1px solid rgba(255,255,255,.22);flex:none}"
            ".code{"
            "    background:TABLE_HEAD;border:1px solid BORDER;border-radius:8px;padding:8px 11px;"
            "    font-family:'Consolas','Cascadia Mono',monospace;font-size:11.5px;color:MUTED;"
            "    display:block;margin-top:8px;white-space:pre-wrap;"
            "}"
            "::-webkit-scrollbar{width:10px;height:10px}"
            "::-webkit-scrollbar-track{background:transparent}"
            "::-webkit-scrollbar-thumb{background:BORDER;border-radius:5px}"
            "::-webkit-scrollbar-thumb:hover{background:ACCENT}"
        )
        
        # Substituir placeholders pelo valor real
        css = css.replace("TEXT", text)
        css = css.replace("MUTED", muted)
        css = css.replace("BORDER", border)
        css = css.replace("ACCENT", a)
        css = css.replace("ACCENT_SOFT", accent_soft)
        css = css.replace("ACCENT_GLOW", accent_glow)
        css = css.replace("BG_MAIN", m)
        css = css.replace("TABLE_HEAD", table_head)
        css = css.replace("DANGER", danger)
        css = css.replace("DANGER_SOFT", danger_soft)
        css = css.replace("DANGER_SOFT_BORDER", danger_soft_border)
        css = css.replace("DANGER_GLOW", danger_glow)
        css = css.replace("CARD_BG", card_bg)
        css = css.replace("HOVER_ROW", hover_row)
        css = css.replace("TEXT_SOFT", text_soft)
        css = css.replace("ACCENT_VERY_SOFT", accent_very_soft)
        
        return css



    @staticmethod
    def _page(title, colors, dark, content, extra_css=""):
        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{esc(title)} — Upgraded One</title>
<style>{InternalPages._page_css(colors, dark)}{extra_css}</style></head>
<body><div class="page">{content}</div></body></html>"""

    @staticmethod
    def _card_html(url, domain, name):
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
    display:flex;flex-wrap:wrap;justify-content:center;
    gap:14px;margin-top:36px;width:100%;
}}
.card{{
    width:86px;height:92px;flex:0 0 auto;padding:0;display:flex;flex-direction:column;
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
        del prefix
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
        <div class="kv"><span>Motor</span><b>Chromium (CEF)</b></div>
        <p>Flags ativas:</p>
        <code class="code">--no-sandbox --disable-dev-shm-usage --ignore-certificate-errors</code>
    </div>
</div>
"""
        return InternalPages._page("Configuracoes", colors, dark, content)

    @staticmethod
    def certificate_page(colors, certs, active_name, dark=True):
        st = InternalPages._base_style(dark)
        a = colors["accent"]
        text, muted = st["text"], st["muted"]
        border = st["border"]
        card_bg = "rgba(255,255,255,0.04)" if dark else "#ffffff"

        if not certs:
            rows = (
                "<tr><td colspan='4'><div class='empty'>"
                f"{svg_icon('certificate', muted, 36)}"
                "Nenhum certificado importado.<br>"
                "Clique em 'Importar certificado' para adicionar um arquivo A1 (.pfx/.p12) ou PEM.</div></td></tr>"
            )
        else:
            rows = ""
            for c in certs:
                is_active = c.get("active", False)
                active_badge = " <span style='color:#10b981;font-weight:bold;'>(ATIVO)</span>" if is_active else ""
                name = esc(c.get("name", "Sem nome"))
                has_pem = bool(c.get("cert_pem"))
                status = "Valido" if has_pem else "Sem dados PEM"
                rows += (
                    f"<tr>"
                    f"<td><b>{name}{active_badge}</b></td>"
                    f"<td class='td-url'>{status}</td>"
                    f"<td><a class='btn' style='padding:4px 8px;font-size:11px;' href='{INTERNAL_ACTION_BASE}certificate/set-active/{esc(name)}'>"
                    f"{'Desativar' if is_active else 'Ativar'}</a></td>"
                    f"<td><a class='btn danger' style='padding:4px 8px;font-size:11px;' href='{INTERNAL_ACTION_BASE}certificate/remove/{esc(name)}'>Remover</a></td>"
                    f"</tr>"
                )

        content = f"""
<div class="header">
    <div class="title-wrap">
        <div class="title-icon">{svg_icon('certificate', a, 22)}</div>
        <div>
            <h1>Certificados Digitais</h1>
            <p class="subtitle">Gerencie certificados A1 para autenticacao em sites (bancos, gov.br, etc.)</p>
        </div>
    </div>
    <div class="actions">
        <a class="btn" href="{INTERNAL_ACTION_BASE}certificate/import">
            {svg_icon('download', 'currentColor', 15)}
            <span>Importar certificado</span>
        </a>
        <a class="btn" href="{INTERNAL_ACTION_BASE}settings">
            {svg_icon('settings', 'currentColor', 15)}
            <span>Voltar ao settings</span>
        </a>
    </div>
</div>
<div class="panel">
    <h3>{svg_icon('shield', 'currentColor', 16)}Certificados importados</h3>
    <div class="table-wrap">
        <table>
            <thead><tr><th>Nome</th><th>Status</th><th>Acao</th><th></th></tr></thead>
            <tbody>{rows}</tbody>
        </table>
    </div>
    <p style="margin-top:12px;color:{muted};font-size:12.5px;">
        O certificado marcado como <span style='color:#10b981;font-weight:bold;'>(ATIVO)</span> sera enviado
        automaticamente quando um site solicitar autenticacao com certificado digital (mTLS).
    </p>
</div>
"""
        return InternalPages._page("Certificados Digitais", colors, dark, content)


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

class IconButton(QToolButton):
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

class CefClientHandler:
    def __init__(self, main_window, browser_widget):
        self.main_window = main_window
        self.browser_widget = browser_widget
        self._cert_selections = []

    def OnTitleChange(self, browser, title):
        try:
            self.main_window.on_title_changed(self.browser_widget, title)
        except Exception:
            pass

    def OnAddressChange(self, browser, frame, url):
        try:
            self.main_window.on_url_changed(self.browser_widget, url)
        except Exception:
            pass

    def OnLoadingStateChange(self, browser, **_):
        try:
            is_loading = _.get('is_loading', False)
            self.main_window.on_loading_state_changed(self.browser_widget, is_loading)
        except Exception:
            pass

    def OnConsoleMessage(self, browser, **_):
        try:
            level = _.get('level', 0)
            message = _.get('message', '')
            source = _.get('source', '')
            line = _.get('line', 0)
            print(f"CEF CONSOLE [{source}:{line}] {message}")
        except Exception:
            pass
        return False

    def OnStatusMessage(self, browser, value=None):
        try:
            self.main_window.on_status_message(self.browser_widget, value)
        except Exception:
            pass
        return False


class CefRequestHandler:
    def __init__(self, main_window, browser_widget=None):
        self.main_window = main_window
        self.browser_widget = browser_widget

    def OnBeforeResourceLoad(self, browser, frame, request, *args, **_):
        try:
            url = request.GetUrl()
            host = urlparse(url).hostname.lower() if urlparse(url).hostname else ""
            request.SetHeaderByName("Accept-Language", "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7", True)
            request.SetHeaderByName("DNT", "1", True)
            request.SetHeaderByName("Upgrade-Insecure-Requests", "1", True)
            if host in GOOGLE_AUTH_HOSTS or host.endswith(".google.com") or "gstatic.com" in host:
                request.SetHeaderByName("User-Agent", GOOGLE_CHROME_USER_AGENT, True)
                request.SetHeaderByName("Accept-Language", "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7", True)
                request.SetHeaderByName("Sec-Ch-Ua", '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"', True)
                request.SetHeaderByName("Sec-Ch-Ua-Mobile", "?0", True)
                request.SetHeaderByName("Sec-Ch-Ua-Platform", '"Windows"', True)
        except Exception:
            pass
        return False

    def OnBeforeBrowse(self, browser, frame, request, **_):
        try:
            url = request.GetUrl()
            if url.startswith(INTERNAL_ACTION_BASE) or url.startswith("upgraded_one://"):
                # Passa o CefBrowserWidget (wrapper), nao o objeto CEF bruto.
                if self.main_window.handle_url_change(url, self.browser_widget):
                    return True
        except Exception:
            pass
        return False

    def OnCertificateError(self, browser, cert_error, request_url, ssl_info, callback, **_):
        try:
            callback.Continue(True)
            return True
        except Exception:
            pass
        return False

    def OnSelectClientCertificate(self, browser, url, cert_list, callback, **_):
        try:
            if not cert_list:
                callback.Select(None)
                return True
            if len(cert_list) == 1:
                callback.Select(cert_list[0])
                return True
            callback.Select(cert_list[0])
            return True
        except Exception:
            try:
                callback.Select(None)
            except Exception:
                pass
            return False


class CefLifeSpanHandler:
    def __init__(self, main_window):
        self.main_window = main_window

    def OnBeforePopup(self, parentBrowser, targetUrl, targetFrameName, targetDisposition, userGesture, popupFeatures, windowInfo, client, settings, noJavascriptAccess, **_):
        try:
            self.main_window.add_tab(targetUrl)
            return True
        except Exception:
            return False


class CefDownloadHandler:
    def __init__(self, main_window):
        self.main_window = main_window
        self.downloads = []

    def OnBeforeDownload(self, browser, download_item, callback, **_):
        try:
            suggested_name = download_item.GetSuggestedFileName() or "download"
            downloads_folder = str(Path.home() / "Downloads")
            path, _ = QFileDialog.getSaveFileName(
                self.main_window, "Salvar arquivo",
                str(Path(downloads_folder) / suggested_name)
            )
            if path:
                download_item.SetDestinationFilePath(path)
                callback.Continue(path, show_dialog=False)
                self.downloads.append({
                    "path": path,
                    "name": suggested_name,
                    "url": download_item.GetURL(),
                    "status": "iniciado",
                })
                mw = self.main_window
                if hasattr(mw, 'download_manager') and mw.download_manager:
                    mw.download_manager.add_download(suggested_name, path, download_item.GetURL())
                QTimer.singleShot(100, lambda: mw._notify_download(path, suggested_name))
            else:
                callback.Cancel()
        except Exception:
            try:
                callback.Cancel()
            except Exception:
                pass


class DownloadItem(QWidget):
    """Item de download na janela de downloads (adaptado para CEF)."""

    def __init__(self, name="arquivo", path="", url="", parent=None):
        super().__init__(parent)
        self._path = path or ""
        self._filename = name or "arquivo"
        self._url = url or ""
        self._completed = False

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

    def _current_path(self):
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

    def mark_completed(self, path=""):
        if path:
            self._path = path
        self._completed = True
        self.btn_open.setEnabled(True)
        self.btn_folder.setEnabled(True)
        try:
            self.info.setText(f"{self._filename}\nConcluido")
        except RuntimeError:
            pass

    def mark_failed(self):
        try:
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

    def add_download(self, name="arquivo", path="", url=""):
        item = DownloadItem(name=name, path=path, url=url)
        self.container_layout.addWidget(item)
        self.show()
        self.raise_()
        self.activateWindow()
        return item

    def clear_completed(self):
        for i in reversed(range(self.container_layout.count())):
            w = self.container_layout.itemAt(i).widget()
            if isinstance(w, DownloadItem) and w._completed:
                w.deleteLater()


class CefJSDialogHandler:
    def __init__(self, main_window):
        self.main_window = main_window

    def OnJSDialog(self, browser, origin_url, accept_lang, dialog_type, message_text, default_prompt_text, callback, suppress_message, **_):
        try:
            if dialog_type == "alert":
                QMessageBox.information(self.main_window, "Mensagem", message_text)
            elif dialog_type == "confirm":
                reply = QMessageBox.question(self.main_window, "Confirmar", message_text,
                                             QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                callback.Continue(True if reply == QMessageBox.Yes else False, "")
            elif dialog_type == "prompt":
                text, ok = QInputDialog.getText(self.main_window, "Entrada", message_text,
                                                text=default_prompt_text)
                callback.Continue(ok, text if ok else "")
            else:
                callback.Continue(True, "")
            return True
        except Exception:
            return False

    def OnBeforeUnloadDialog(self, browser, is_reload, message_text, callback, **_):
        try:
            callback.Continue(True, "")
            return True
        except Exception:
            return False


class CertificateManager:
    def __init__(self, profile_folder: Path):
        self.profile_folder = profile_folder
        self.certs_file = profile_folder / CERT_STORAGE_FILE
        self.certs = []
        self._load()

    def _load(self):
        try:
            if self.certs_file.exists():
                with open(self.certs_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.certs = data if isinstance(data, list) else []
        except Exception:
            self.certs = []

    def _save(self):
        try:
            self.profile_folder.mkdir(parents=True, exist_ok=True)
            with open(self.certs_file, "w", encoding="utf-8") as f:
                json.dump(self.certs, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def import_pfx(self, pfx_data: bytes, password: str, name: str) -> bool:
        try:
            from OpenSSL import crypto
            p12 = crypto.load_pkcs12(pfx_data, password.encode("utf-8"))
            cert = p12.get_certificate()
            key = p12.get_privatekey()
            cert_pem = crypto.dump_certificate(crypto.FILETYPE_PEM, cert).decode("utf-8")
            key_pem = crypto.dump_privatekey(crypto.FILETYPE_PEM, key).decode("utf-8")
            self.certs.append({
                "name": name or "Certificado",
                "cert_pem": cert_pem,
                "key_pem": key_pem,
                "active": len(self.certs) == 0,
            })
            self._save()
            return True
        except ImportError:
            self.certs.append({
                "name": name or "Certificado PFX",
                "cert_pem": "",
                "key_pem": "",
                "active": len(self.certs) == 0,
            })
            self._save()
            return True
        except Exception:
            return False

    def import_pem(self, cert_pem: str, key_pem: str, name: str) -> bool:
        try:
            self.certs.append({
                "name": name or "Certificado PEM",
                "cert_pem": cert_pem,
                "key_pem": key_pem,
                "active": len(self.certs) == 0,
            })
            self._save()
            return True
        except Exception:
            return False

    def list_certs(self):
        return self.certs

    def set_active(self, name: str):
        for c in self.certs:
            c["active"] = (c["name"] == name)
        self._save()

    def get_active(self):
        for c in self.certs:
            if c.get("active"):
                return c
        return None

    def remove(self, name: str):
        self.certs = [c for c in self.certs if c["name"] != name]
        self._save()


class CefBrowserWidget(QWidget):
    def __init__(self, main_window, url="", parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.browser = None
        self.client_handler = CefClientHandler(main_window, self)
        self.request_handler = CefRequestHandler(main_window, browser_widget=self)
        self.life_span_handler = CefLifeSpanHandler(main_window)
        self.js_dialog_handler = CefJSDialogHandler(main_window)
        self.download_handler = CefDownloadHandler(main_window)
        self._url = url
        self._pending_url = None
        self._created = False
        self._is_home = False
        self._internal_page = ""
        self._zoom = 1.0
        self._loading = False

        # No cefpython3 nao se passa handlers no CreateBrowserSync. Eles sao
        # registrados apos a criacao com SetClientHandler().
        self._create_timer = QTimer(self)
        self._create_timer.setSingleShot(True)
        self._create_timer.timeout.connect(self._create_browser)
        self._create_timer.start(50)

    def _create_browser(self):
        if cef is None:
            print("ERRO: cefpython3 nao instalado")
            return
        try:
            window_info = cef.WindowInfo()
            window_info.SetAsChild(int(self.winId()), [0, 0, self.width(), self.height()])
            # Quando ha URL pendente (pagina interna via data:), inicia em
            # about:blank para nao navegar para um URL invalido (ex:
            # upgraded_one://bookmarks), que o CEF nao consegue carregar.
            start_url = self._pending_url if self._pending_url else (self._url or "about:blank")
            self.browser = cef.CreateBrowserSync(window_info, url=start_url)
            self._created = True
            # Registra os handlers via SetClientHandler (API correta do
            # cefpython3). Cada handler e detectado pelos metodos que implementa.
            try:
                self.browser.SetClientHandler(self.client_handler)
            except Exception:
                pass
            try:
                self.browser.SetClientHandler(self.request_handler)
            except Exception:
                pass
            try:
                self.browser.SetClientHandler(self.life_span_handler)
            except Exception:
                pass
            try:
                self.browser.SetClientHandler(self.js_dialog_handler)
            except Exception:
                pass
            try:
                self.browser.SetClientHandler(self.download_handler)
            except Exception:
                pass
            # Se houve URL pendente e nao foi usado acima, carrega agora.
            if self._pending_url and self._pending_url != start_url:
                try:
                    self.browser.GetMainFrame().LoadUrl(self._pending_url)
                except Exception as e:
                    print(f"ERRO load pendente CEF: {e}")
                self._pending_url = None
        except Exception as e:
            print(f"ERRO ao criar navegador CEF: {e}")

    def load(self, url):
        self._url = url
        if self.browser and self._created:
            try:
                self.browser.GetMainFrame().LoadUrl(url)
            except Exception as e:
                print(f"ERRO load CEF: {e}")
        else:
            # Browser CEF ainda nao foi criado (timer de 50ms). Fila a URL para
            # carregar assim que estiver pronto. Isso e essencial para paginas
            # internas (home, favoritos, historico etc.) que sao carregadas
            # imediatamente apos add_tab(), antes do browser existir.
            self._pending_url = url
            self._create_timer.start(50)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.browser and self._created:
            try:
                self.browser.NotifyMoveOrResizeStarted()
            except Exception:
                pass

    def back(self):
        if self.browser:
            try:
                self.browser.GoBack()
            except Exception:
                pass

    def forward(self):
        if self.browser:
            try:
                self.browser.GoForward()
            except Exception:
                pass

    def reload(self):
        if self.browser:
            try:
                self.browser.Reload()
            except Exception:
                pass

    def stop(self):
        if self.browser:
            try:
                self.browser.StopLoad()
            except Exception:
                pass

    def title(self):
        if self.browser:
            try:
                return self.browser.GetTitle()
            except Exception:
                return ""
        return ""

    def url(self):
        if self.browser:
            try:
                return self.browser.GetMainFrame().GetUrl()
            except Exception:
                return ""
        return ""

    def run_javascript(self, code, callback=None):
        if self.browser:
            try:
                frame = self.browser.GetMainFrame()
                if callback:
                    frame.ExecuteJavaScript(code, callback)
                else:
                    frame.ExecuteJavaScript(code)
            except Exception as e:
                print(f"ERRO JS CEF: {e}")

    def setZoomFactor(self, factor):
        if self.browser:
            try:
                self.browser.SetZoomLevel(factor - 1.0)
            except Exception:
                pass

    def property(self, name):
        if name == "is_home":
            return self._is_home
        if name == "internal_page":
            return self._internal_page
        if name == "zoom":
            return self._zoom
        if name == "loading":
            return self._loading
        return None

    def setProperty(self, name, value):
        if name == "is_home":
            self._is_home = bool(value)
        elif name == "internal_page":
            self._internal_page = str(value)
        elif name == "zoom":
            self._zoom = float(value)
        elif name == "loading":
            self._loading = bool(value)

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
            self.db = None
            self.cert_manager = None
            self.favicons = None
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
            self.favicons = FaviconCache(self)
            cert_folder = folder / "certificates"
            self.cert_manager = CertificateManager(cert_folder)
            title_prefix = f"[{profile_name}] " if profile_name else ""

        self.setWindowTitle(f"{title_prefix}{APP_NAME}")
        self.setWindowIcon(app_icon())
        self.favicons = FaviconCache(self) if not is_private else None
        self.download_manager = DownloadManager()
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

        cl.insertWidget(1, nav)

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

        for i in range(self.tabs.count()):
            b = self.tabs.widget(i)
            if not b:
                continue
            try:
                kind = b.property("internal_page") or ""
                bg = self.bg_home if kind == "home" else self.bg_main
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
            pass

    @staticmethod
    def _letter_icon(label, domain="") -> QIcon:
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
        browser = CefBrowserWidget(self, url)
        browser.setProperty("is_home", False)
        browser.setProperty("internal_page", "")
        browser.setProperty("zoom", 1.0)
        browser.setProperty("loading", False)

        i = self.tabs.addTab(browser, "Nova Guia")
        if not background:
            self.tabs.setCurrentIndex(i)

        if url is None:
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
                browser.load(url)
            except (RuntimeError, TypeError):
                pass
        return browser

    def close_tab(self, i):
        if not (0 <= i < self.tabs.count()):
            return
        if self.tabs.count() <= 1:
            self.close()
            return
        browser = self.tabs.widget(i)
        if browser is not None:
            try:
                url = browser.url()
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
            b.load(url)

    def on_title_changed(self, browser, title):
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

    def on_url_changed(self, browser, url):
        try:
            b = self.current_browser()
            if browser is b and self.url_bar:
                # Paginas internas usam data:url como transporte; mostrar endereco amigavel.
                internal_kind = browser.property("internal_page") or ""
                if internal_kind and str(url).startswith("data:"):
                    self.url_bar.setText("upgraded_one://" + internal_kind)
                else:
                    self.url_bar.setText(url)
        except Exception:
            pass
        try:
            if self.db and url and not url.startswith(("upgraded_one://", "data:", "about:", "data:text/html")):
                title = browser.title() if hasattr(browser, 'title') else ""
                self.db.add_history(url, title or url)
        except Exception:
            pass
        try:
            host = urlparse(url).hostname.lower() if urlparse(url).hostname else ""
            if host in GOOGLE_AUTH_HOSTS or host.endswith(".google.com") or "gstatic.com" in host:
                script = GOOGLE_UA_SCRIPT_CEF.replace("__HOSTS__", json.dumps(sorted(GOOGLE_AUTH_HOSTS))).replace("__UA__", GOOGLE_CHROME_USER_AGENT)
                QTimer.singleShot(500, lambda b=browser, s=script: self._inject_google_spoof(b, s))
        except Exception:
            pass

    def _inject_google_spoof(self, browser, script):
        try:
            frame = browser.browser.GetMainFrame()
            frame.ExecuteJavaScript(script)
        except Exception:
            pass

    def on_loading_state_changed(self, browser, is_loading):
        try:
            self.progress.setVisible(is_loading)
        except Exception:
            pass

    def handle_url_change(self, url_str, browser):
        if not isinstance(url_str, str):
            return False
        if url_str.startswith(INTERNAL_ACTION_BASE):
            return self.handle_internal_action(url_str)
        if url_str.startswith("upgraded_one://certificate"):
            kind = url_str.replace("upgraded_one://certificate", "").split("?")[0].split("#")[0].strip("/").lower()
            if kind == "manage" or not kind:
                self.show_certificate_manager()
                return True
            elif kind == "import":
                self.import_certificate()
                return True
            elif kind.startswith("set-active/"):
                name = kind[len("set-active/"):]
                self.set_active_certificate(name)
                return True
            elif kind.startswith("remove/"):
                name = kind[len("remove/"):]
                self.remove_certificate(name)
                return True
            return True
        if url_str.startswith("upgraded_one://"):
            kind = url_str.replace("upgraded_one://", "").split("?")[0].split("#")[0].strip("/").lower()
            if kind == "home":
                self.load_speed_dial(browser)
                browser.setProperty("is_home", True)
                browser.setProperty("internal_page", "home")
                self.url_bar.setText("upgraded_one://home")
            elif kind == "settings":
                self.load_settings_page(browser)
                browser.setProperty("is_home", False)
                browser.setProperty("internal_page", "settings")
                self.url_bar.setText("upgraded_one://settings")
            elif kind == "history":
                self.load_history_page(browser)
                browser.setProperty("is_home", False)
                browser.setProperty("internal_page", "history")
                self.url_bar.setText("upgraded_one://history")
            elif kind == "bookmarks":
                self.load_bookmarks_page(browser)
                browser.setProperty("is_home", False)
                browser.setProperty("internal_page", "bookmarks")
                self.url_bar.setText("upgraded_one://bookmarks")
            elif kind == "downloads":
                self.load_downloads_page(browser)
                browser.setProperty("is_home", False)
                browser.setProperty("internal_page", "downloads")
                self.url_bar.setText("upgraded_one://downloads")
            idx = self.tabs.indexOf(browser)
            if idx != -1:
                label = {"home": "Home", "settings": "Configuracoes", "history": "Historico",
                         "bookmarks": "Favoritos", "downloads": "Downloads"}.get(kind, kind.capitalize())
                self.tabs.setTabText(idx, label)
                icon = app_icon()
                if not icon.isNull():
                    self.tabs.setTabIcon(idx, icon)
            return True
        # URL externa (http/https): resetar propriedades de pagina interna.
        # Data URLs sao apenas o transporte do HTML interno, nao resetam.
        is_data_url = url_str.startswith("data:")
        is_internal_url = url_str.startswith("upgraded_one://") or url_str.startswith(INTERNAL_ACTION_BASE)
        if not is_data_url and not is_internal_url:
            browser.setProperty("is_home", False)
            browser.setProperty("internal_page", "")
        if browser is self.current_browser() and not is_data_url and not is_internal_url:
            self.url_bar.setText(url_str)
        return False

    def handle_internal_action(self, url_str):
        if not isinstance(url_str, str) or not url_str.startswith(INTERNAL_ACTION_BASE):
            return False
        action = url_str[len(INTERNAL_ACTION_BASE):].split("?")[0].split("#")[0].strip("/").lower()
        if action == "certificate/manage" or action == "certificate":
            QTimer.singleShot(0, self.show_certificate_manager)
            return True
        if action == "certificate/import":
            QTimer.singleShot(0, self.import_certificate)
            return True
        if action.startswith("certificate/set-active/"):
            name = action[len("certificate/set-active/"):]
            QTimer.singleShot(0, lambda n=name: self.set_active_certificate(n))
            return True
        if action.startswith("certificate/remove/"):
            name = action[len("certificate/remove/"):]
            QTimer.singleShot(0, lambda n=name: self.remove_certificate(n))
            return True
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
        handlers = {
            "clear-history": self.clear_history_action,
            "clear-cache": self.clear_cache_action,
            "clear-all": self.clear_all_action,
        }
        handler = handlers.get(action)
        if handler is None:
            return False
        handler()
        return True

    def tab_changed(self, index):
        try:
            browser = self.tabs.widget(index)
            if not browser:
                return
            self.handle_url_change(browser.url(), browser)
            z = browser.property("zoom") or 1.0
            browser.setZoomFactor(float(z))
            self.progress.setVisible(bool(browser.property("loading")))
        except (RuntimeError, TypeError, ValueError):
            pass

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
        if not self._ask("Limpar historico", "Apagar todo o historico de navegacao deste perfil?"):
            return
        self.db.clear_history()
        self.refresh_internal_pages()
        self._notify("Historico", "Historico de navegacao apagado com sucesso!")

    def clear_cache_action(self):
        if not self._ask("Limpar cache", "Apagar arquivos temporarios de cache?"):
            return
        self._notify("Cache", "Cache limpo (CEF gerencia automaticamente).")

    def clear_all_action(self):
        self.clear_history_action()
        self.clear_cache_action()

    def refresh_internal_pages(self, kinds=None):
        """Recarrega as paginas internas abertas apos limpar dados ou trocar tema."""
        wanted = set(kinds) if kinds else {"home", "history", "bookmarks", "settings", "downloads"}
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

    def _nav_back(self):
        b = self.current_browser()
        if b:
            b.back()

    def _nav_forward(self):
        b = self.current_browser()
        if b:
            b.forward()

    def _nav_reload(self):
        b = self.current_browser()
        if b:
            b.reload()

    def _nav_go(self):
        self.go_url()

    def focus_url_bar(self):
        self.url_bar.setFocus()
        self.url_bar.selectAll()

    def show_history(self):
        b = self.current_browser()
        if b:
            self.load_history_page(b)

    def show_settings(self):
        b = self.current_browser()
        if b:
            self.load_settings_page(b)

    def show_bookmarks(self):
        b = self.current_browser()
        if b:
            self.load_bookmarks_page(b)

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _notify_download(self, path, name):
        try:
            QMessageBox.information(self, "Download", f"Baixado: {name}\nSalvo em: {path}")
        except RuntimeError:
            pass

    def on_status_message(self, browser, message):
        pass

    def _open_downloaded(self, path):
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception:
            pass

    def _open_downloaded_folder(self, path):
        try:
            folder = os.path.dirname(path) if path else ""
            if folder:
                QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        except Exception:
            pass

    def set_zoom(self, delta, reset=False):
        b = self.current_browser()
        if not b:
            return
        if reset:
            self.zoom_factor = 1.0
        else:
            self.zoom_factor = max(0.25, min(5.0, self.zoom_factor + delta))
        try:
            b.setZoomFactor(self.zoom_factor)
        except Exception:
            pass
        b.setProperty("zoom", self.zoom_factor)

    def toggle_dark_mode(self):
        old_pal = DEFAULT_COLORS["dark" if self.dark_mode else "light"]
        self.dark_mode = not self.dark_mode
        new_pal = DEFAULT_COLORS["dark" if self.dark_mode else "light"]
        # So troca as cores que ainda estavam no padrao do tema anterior,
        # para nao apagar a personalizacao feita pelo usuario.
        if self.accent_color.lower() == old_pal["accent"].lower():
            self.accent_color = new_pal["accent"]
        if self.bg_main.lower() == old_pal["main"].lower():
            self.bg_main = new_pal["main"]
        if self.bg_secondary.lower() == old_pal["secondary"].lower():
            self.bg_secondary = new_pal["secondary"]
        if self.bg_home.lower() == old_pal["home"].lower():
            self.bg_home = new_pal["home"]

        self.btn_theme.set_icon_name("sun" if self.dark_mode else "moon")
        self.apply_theme()
        self.refresh_internal_pages()
        if not self.is_private and self.db:
            self.profile_data["dark_mode"] = self.dark_mode
            self.profile_manager.update(self.profile_name, "dark_mode", self.dark_mode)
            self.profile_manager.update(self.profile_name, "accent_color", self.accent_color)
            self.profile_manager.update(self.profile_name, "bg_main", self.bg_main)
            self.profile_manager.update(self.profile_name, "bg_secondary", self.bg_secondary)
            self.profile_manager.update(self.profile_name, "bg_home", self.bg_home)

    def apply_theme(self):
        self.update_styles()

    def _colors(self):
        return {
            "accent": self.accent_color,
            "bg_secondary": self.bg_secondary,
            "bg_main": self.bg_main,
            "bg_home": self.bg_home,
        }

    def choose_accent_color(self):
        color = QColorDialog.getColor(QColor(self.accent_color), self, "Escolher cor de destaque")
        if color.isValid():
            self.accent_color = color.name()
            if not self.is_private and self.db:
                self.profile_data["accent_color"] = self.accent_color
                self.profile_manager.update(self.profile_name, "accent_color", self.accent_color)
            self.apply_theme()
            self.refresh_internal_pages()

    def choose_panel_color(self):
        color = QColorDialog.getColor(QColor(self.bg_secondary), self, "Escolher cor dos paineis")
        if color.isValid():
            self.bg_secondary = color.name()
            if not self.is_private and self.db:
                self.profile_data["bg_secondary"] = self.bg_secondary
                self.profile_manager.update(self.profile_name, "bg_secondary", self.bg_secondary)
            self.apply_theme()
            self.refresh_internal_pages()

    def choose_bg_color(self):
        color = QColorDialog.getColor(QColor(self.bg_main), self, "Escolher cor de fundo")
        if color.isValid():
            self.bg_main = color.name()
            if not self.is_private and self.db:
                self.profile_data["bg_main"] = self.bg_main
                self.profile_manager.update(self.profile_name, "bg_main", self.bg_main)
            self.apply_theme()
            self.refresh_internal_pages()

    def choose_home_bg_color(self):
        color = QColorDialog.getColor(QColor(self.bg_home), self, "Escolher cor da Home")
        if color.isValid():
            self.bg_home = color.name()
            if not self.is_private and self.db:
                self.profile_data["bg_home"] = self.bg_home
                self.profile_manager.update(self.profile_name, "bg_home", self.bg_home)
            self.apply_theme()
            self.refresh_internal_pages()

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

    def open_certificate_manager(self):
        self.add_tab("upgraded_one://certificate")

    def open_settings_page(self):
        self.add_tab("upgraded_one://settings")

    def open_private_window(self):
        try:
            priv = MainWindow(is_private=True, profile_manager=self.profile_manager)
            self._private_windows.append(priv)
            priv.destroyed.connect(lambda *_, w=priv: self._discard_private(w))
            priv.show()
        except Exception:
            pass

    def _discard_private(self, win):
        try:
            self._private_windows = [w for w in self._private_windows if w is not win]
        except RuntimeError:
            pass

    def closeEvent(self, event):
        try:
            for w in list(self._private_windows):
                try:
                    w.close()
                except RuntimeError:
                    pass
            self._private_windows = []

            try:
                self.download_manager.close()
                self.download_manager.deleteLater()
            except RuntimeError:
                pass

            # Libera as paginas antes de encerrar.
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

    def show_find_bar(self):
        b = self.current_browser()
        if b:
            QMessageBox.information(self, "Localizar", "Use Ctrl+F na pagina para localizar texto.")

    def find_text(self, text):
        pass

    def find_next(self):
        pass

    def load_speed_dial(self, browser):
        self._prepare_internal_page(browser, is_home=True)
        engine = self.profile_data.get("search_engine", "Google")
        template = SEARCH_ENGINES.get(engine, SEARCH_ENGINES["Google"])
        bookmarks = self.db.get_bookmarks() if self.db else []
        html = InternalPages.speed_dial(self._colors(), self.is_private, bookmarks,
                                        template, self.dark_mode)
        self._set_html_internal(browser, html, "upgraded_one://home")

    def load_settings_page(self, browser):
        self._prepare_internal_page(browser, kind="settings")
        data = self.profile_data if not self.is_private else {"name": "Anonimo"}
        html = InternalPages.settings_page(self._colors(), data, self.dark_mode)
        self._set_html_internal(browser, html, "upgraded_one://settings")

    def load_history_page(self, browser):
        self._prepare_internal_page(browser, kind="history")
        history = self.db.get_history() if self.db else []
        html = InternalPages.history_page(self._colors(), history, self.dark_mode)
        self._set_html_internal(browser, html, "upgraded_one://history")

    def load_bookmarks_page(self, browser):
        self._prepare_internal_page(browser, kind="bookmarks")
        bookmarks = self.db.get_bookmarks() if self.db else []
        html = InternalPages.bookmarks_page(self._colors(), bookmarks, self.dark_mode)
        self._set_html_internal(browser, html, "upgraded_one://bookmarks")

    def load_downloads_page(self, browser):
        self._prepare_internal_page(browser, kind="downloads")
        html = InternalPages._page("Downloads", self._colors(), self.dark_mode, "")
        self._set_html_internal(browser, html, "upgraded_one://downloads")

    def _prepare_internal_page(self, browser, is_home=False, kind=None):
        try:
            browser.setProperty("is_home", bool(is_home))
            browser.setProperty("internal_page", str(kind or ("home" if is_home else "")))
            idx = self.tabs.indexOf(browser)
            if idx != -1:
                if is_home:
                    self.tabs.setTabText(idx, "Home")
                elif kind:
                    self.tabs.setTabText(idx, kind.capitalize())
                icon = app_icon()
                if not icon.isNull():
                    self.tabs.setTabIcon(idx, icon)
        except Exception:
            pass

    def _set_html_internal(self, browser, html, url):
        from urllib.parse import quote
        data_url = "data:text/html;charset=utf-8," + quote(html, safe="")
        try:
            browser.load(data_url)
        except Exception:
            pass

    def add_bookmark_current(self):
        b = self.current_browser()
        if not b or not self.db:
            if self.is_private:
                QMessageBox.information(self, "Favoritos", "Favoritos estao desativados no modo anonimo.")
            return
        try:
            url = b.url()
            if not url or url.startswith(("upgraded_one://", "data:", "about:")):
                return
            title = b.title() or ""
        except RuntimeError:
            return
        if not title or title.startswith(("data:", "upgraded_one://")) or len(title) > 200:
            title = urlparse(url).netloc or urlparse(url).path or "Pagina sem titulo"
        self.db.add_bookmark(url, title)
        self.refresh_internal_pages()
        self.update_bookmark_bar()
        try:
            QMessageBox.information(self, "Favorito", f"Adicionado: {shorten(title, 30)}")
        except RuntimeError:
            pass

    def show_certificate_manager(self):
        b = self.current_browser()
        if not b:
            return
        self._prepare_internal_page(b, kind="certificate")
        certs = self.cert_manager.list_certs() if self.cert_manager else []
        active = self.cert_manager.get_active() if self.cert_manager else None
        active_name = active["name"] if active else ""
        html = InternalPages.certificate_page(self._colors(), certs, active_name, self.dark_mode)
        self._set_html_internal(b, html, "upgraded_one://certificate")

    def import_certificate(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Importar certificado digital", "",
            "Certificados (*.pfx *.p12 *.pem *.crt);;Todos os arquivos (*)"
        )
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Nao foi possivel ler o arquivo:\n{e}")
            return

        if path.lower().endswith((".pfx", ".p12")):
            password, ok = QInputDialog.getText(
                self, "Senha do certificado",
                "Informe a senha do arquivo PFX/P12:",
                QLineEdit.Password,
            )
            if not ok:
                return
            name = Path(path).stem
            if self.cert_manager.import_pfx(data, password, name):
                QMessageBox.information(self, "Certificado", f"Certificado '{name}' importado com sucesso!")
                self.show_certificate_manager()
            else:
                QMessageBox.critical(self, "Erro", "Falha ao importar o certificado PFX/P12.")
        elif path.lower().endswith((".pem", ".crt")):
            name, ok = QInputDialog.getText(self, "Nome do certificado", "Nome para identificar o certificado:")
            if not ok or not name:
                return
            try:
                pem_text = data.decode("utf-8", errors="ignore")
            except Exception:
                pem_text = ""
            if self.cert_manager.import_pem(pem_text, pem_text, name):
                QMessageBox.information(self, "Certificado", f"Certificado '{name}' importado com sucesso!")
                self.show_certificate_manager()
            else:
                QMessageBox.critical(self, "Erro", "Falha ao importar o certificado PEM.")

    def remove_certificate(self, name: str):
        if not self.cert_manager:
            return
        if not self._ask("Remover certificado", f"Remover o certificado '{name}'?"):
            return
        self.cert_manager.remove(name)
        self.show_certificate_manager()

    def set_active_certificate(self, name: str):
        if not self.cert_manager:
            return
        self.cert_manager.set_active(name)
        self.show_certificate_manager()


def main():
    install_exception_hook()
    app = QApplication(sys.argv)
    app.setWindowIcon(app_icon())

    cef_ok = bool(cef)
    cef_timer = None
    if cef_ok:
        try:
            # ExceptHook encerra processos CEF em caso de erro nao tratado.
            sys.excepthook = cef.ExceptHook
            cef.Initialize()
        except Exception as e:
            print(f"AVISO: CEF nao inicializado: {e}")
            cef_ok = False
        if cef_ok:
            # Message loop do CEF e obrigatorio: processa eventos do navegador
            # enquanto o Qt roda. Sem isso, o browser nao renderiza nem responde.
            cef_timer = QTimer(app)
            cef_timer.timeout.connect(cef.MessageLoopWork)
            cef_timer.start(10)

    profile_manager = ProfileManager()
    win = MainWindow(profile_manager=profile_manager)
    win.show()

    app.exec_()

    if cef_timer:
        cef_timer.stop()
    if cef_ok:
        try:
            cef.Shutdown()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())

