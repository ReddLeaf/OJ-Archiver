# 🍊 OJ Archiver 🍊

Archives each problem of an OJ exercise into a single PDF with an interactive TOC.

You can choose to either only save the final PDF, or also save each individual problem as its own PDF.

This uses Selenium!

## To Run
```bash
uv sync
.venv/Scripts/activate.ps1 # or source .venv/bin/activate
py archiver.py
```

After running, it'll open a Chrome window and from there you manually log in, click into the exercise you want to archive, then press `Enter` in the terminal.

Files will be saved in a directory called `OJ Archive`.

### Note
Table of contents hyperlinks don't seem to work on GDrive (this is a limitation on GDrive's part, I think), but they do work after downloading the PDFs
