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
- deactivate `venv-win`

    `deactivate`

### Linux

- create python vertual environment

    `python3 -m venv venv-linux`

- activate python vertual environment

    `source venv-linux/bin/activate`

- install pyinstaller

    `pip install pyinstaller`

- [optional] delete dist directory

    `rmdir /s /q dist/linux/portable/`


- build aplication using pyinstaller

    ```
    pyinstaller --clean --onedir --add-data "index.html:." --distpath "dist/linux/portable" --name "Luminary" app.py
    ```

- deactivate `venv-linux`

    `deactivate`