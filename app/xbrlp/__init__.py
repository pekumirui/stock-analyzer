"""XBRLP: Lightweight Python XBRL Parser for Japanese financial data.

Vendored from https://gist.github.com/kawasin73/346ed6a24382a2b238e1ff0d10f3f40c
Do not edit these files. Wrapper lives in app/xbrl/.
"""

from .file_loader import FileLoader
from .parser import Arc, Fact, Label, Parser, QName

__version__ = "0.1.0"
__all__ = ["Parser", "Fact", "QName", "Arc", "Label", "FileLoader"]
