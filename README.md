# 🍊 OJ Archiver 🍊

You probably already know what this does so like, I'll just give the details.

This uses Selenium!

| Category         | Naming pattern         |
|-------------------|------------------------|
| `Practice N`      | `Na, Nb, Nc, ...`      |
| `Lab Exercise N`   | `lNa, lNb, lNc, ...`   |
| `Mock HOPE N`      | `mhNa, mhNb, mhNc, ...`|

These can be edited / more can be added

## To Run
```bash
uv sync
.venv/Scripts/activate.ps1 # or source bin/.venv/activate
py archiver.py
```

After running, it'll open a Chrome window and from there you manually log in, click into the exercise you want to archive, then press `Enter` in the terminal.

Files will be saved in a directory called `OJ Archive`, which you can change of course!
