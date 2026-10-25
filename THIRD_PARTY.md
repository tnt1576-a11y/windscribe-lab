# Third-party components

Windscribe Lab's original Python source is MIT licensed. The portable build also contains unmodified runtime components with their own licenses. They are dynamically linked or supplied as separate files; no restriction is imposed on replacing them for permitted modifications/debugging. No commercial Qt license is required when using the open-source licensing option and satisfying its terms.

| Component | Version in this build | License / corresponding source |
| --- | --- | --- |
| CPython | 3.14.4 | PSF and incorporated component licenses; [corresponding source](https://github.com/python/cpython/tree/v3.14.4). |
| PySide6 Essentials / Shiboken6 | 6.11.1 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only; [corresponding source](https://code.qt.io/cgit/pyside/pyside-setup.git/tree/?h=v6.11.1). |
| Qt Core / GUI / Widgets / Network and plugins | 6.11.1 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only, with additional third-party components; [Qt Base source](https://code.qt.io/cgit/qt/qtbase.git/tree/?h=v6.11.1), [Qt licenses](https://doc.qt.io/qt-6/licenses-used-in-qt.html). |
| OpenSSL | supplied with Python and Qt | Apache-2.0 for OpenSSL 3; [source and license](https://www.openssl.org/source/). |
| SQLite | supplied with Python | Public domain; [source](https://sqlite.org/src/doc/trunk/README.md). |
| ICU | Provided by Windows | Qt uses the ICU library supplied with Windows 10/11. No separately bundled ICU binary. |
| PyInstaller bootloader | 6.19.0 | GPL with bootloader exception; [license and source](https://github.com/pyinstaller/pyinstaller/tree/v6.19.0). |

Full license texts are supplied in `licenses/`. The Qt project's [official package description](https://pypi.org/project/PySide6/) documents its open-source licensing alternatives.

The public server catalog is metadata obtained from Windscribe's official assets endpoint. Windscribe's client and VPN engines are not redistributed. AutoScribe and Windscribe Analyzer code, dependencies, and assets are not included.

The application icon, navigation icons, and control glyphs are original assets drawn with Qt. No external icon set or font is bundled.
