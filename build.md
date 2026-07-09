# Build Instructions for the application

## Teckstack

We will use `pyinstaller` to create executable application for this app.

## How to build

- Assumes that the relevent venv have been enabled

### Windows

- create python vertual environment

    `python -m venv venv-win`

- activate python vertual environment

    `venv-win\Scripts\activate`

- install pyinstaller

    `pip install pyinstaller`

- [optional] delete dist directory

    `rmdir /s /q dist\windows\portable\`


- build aplication using pyinstaller

    ```
    pyinstaller --clean --onedir --add-data "index.html;." --distpath "dist\windows\portable" --name "Luminary" app.py
    ```

### Linux

[try to run `run.sh`] - no need, we package all neccassary things to the executable
