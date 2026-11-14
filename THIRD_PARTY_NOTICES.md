# Third-party software

chat-capture-to-html bundles no third-party code. It uses these packages. The first three are installed with the tool; the rest only with the `rapidocr` or `test` extras.

| Package, version tested | Used for | Licence, as read from the wheel | Without it |
|---|---|---|---|
| NumPy 2.5.4 | Image arrays, lining up the shots | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 (the wheel's licence expression) | Required |
| Pillow 13.0.0 | Reading the PNG screenshots | MIT-CMU | Required |
| opencv-python-headless 5.0.0.93 | Finding bubbles and their edges | MIT for the packaging (LICENSE.txt); Apache-2.0 for OpenCV itself; the other libraries in the wheel's `cv2/` folder, which differ by platform, keep their own licences, some of them LGPL (LICENSE-3RD-PARTY.txt) | Required |
| rapidocr-onnxruntime 1.2.3 | Fallback OCR engine (`pip install .[rapidocr]`) | Apache-2.0; its bundled PP-OCR models come from PaddleOCR, also Apache-2.0 | Tesseract is used; with neither engine the tool stops with exit code `3` unless `--no-ocr` is given |
| onnxruntime 1.29.0 | Installed with RapidOCR | MIT | |
| opencv-python 5.0.0.93 | Installed with RapidOCR | Apache-2.0, with the same bundled libraries as above | |
| pyclipper 1.4.0, Shapely 2.2.0, PyYAML 6.0.3, six 1.17.0 | Installed with RapidOCR | MIT, BSD-3-Clause, MIT, MIT | |
| pytest 9.2.0 | Running the tests (`pip install .[test]`) | MIT | Not needed to use the tool |

Tesseract is a separate program, not a Python package. The tool runs it as a command and includes none of its code. Tesseract and its language data are Apache-2.0. Tested with Tesseract 5.3.4 on Linux; on Windows the README points to the UB Mannheim installer.

The window uses Tkinter, which comes with Python under the Python Software Foundation licence; Tcl/Tk is under a BSD-style licence. On Linux it is installed separately, for example `python3-tk`.

The logo in `assets/` was made for this project and is under the project's own licence.
