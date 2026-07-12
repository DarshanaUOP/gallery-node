# Build Instructions for the application

## Teckstack

We will use `pyinstaller` to create executable application for this app.

## How to build

- Assumes that the relevent venv have been enabled
- All commands below are run from the project root (the folder containing `app/`, `requirements.txt`, `run.sh`)

### Windows

- create python vertual environment

    `python -m venv venv-win`

- activate python vertual environment

    `venv-win\Scripts\activate`

- install pyinstaller

    `pip install -r requirements.txt`

- [optional] delete dist directory

    `rmdir /s /q app\build\windows\portable\`


- build aplication using pyinstaller

    ```
    pyinstaller --clean --onedir --add-data "app\src\frontend\index.html;." --add-data "app\src\frontend\static;static" --distpath "app\build\windows\portable" --name "Luminary" app\src\backend\app.py
    ```
- deactivate `venv-win`

    `deactivate`

### Linux

- create python vertual environment

    `python3 -m venv venv-linux`

- activate python vertual environment

    `source venv-linux/bin/activate`

- install pyinstaller

    `pip install -r requirements.txt`

- [optional] delete dist directory

    `rm -rf app/build/linux/portable/`


- build aplication using pyinstaller

    ```
    pyinstaller --clean --onedir --add-data "app/src/frontend/index.html:." --add-data "app/src/frontend/static:static" --distpath "app/build/linux/portable" --name "Luminary" app/src/backend/app.py
    ```

- deactivate `venv-linux`

    `deactivate`