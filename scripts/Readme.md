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
    pyinstaller --clean --onedir --distpath "app\build\windows\portable" --name "Luminary" app\src\backend\app.py
    ```

- copy the frontend (html/css/js) into the built app

    `app.py` resolves its frontend folder as `BASE_DIR.parent / "frontend"` — it needs to live
    inside the `Luminary\` output folder, alongside `Luminary.exe`. `--add-data` isn't used for
    this since the source layout doesn't match the frozen layout, so copy it manually instead:

    `xcopy /E /I /Y "app\src\frontend" "app\build\windows\portable\Luminary\frontend"`

- bundle ffmpeg/ffprobe (always, regardless of what's installed on this machine)

    `app.py` calls `ffmpeg`/`ffprobe` by bare name via `subprocess`, relying on `PATH`. Rather than
    depending on the build machine already having ffmpeg installed — end users of the packaged app
    won't have it either — `build-windows.bat` downloads a static build automatically and caches it
    in `.ffmpeg-cache\windows\` so it's only fetched once:

    ```
    powershell -Command "Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile '.ffmpeg-cache\windows\ffmpeg.zip'"
    powershell -Command "Expand-Archive -Path '.ffmpeg-cache\windows\ffmpeg.zip' -DestinationPath '.ffmpeg-cache\windows\extracted' -Force"
    rem copy ffmpeg.exe / ffprobe.exe out of the extracted ffmpeg-*\bin\ folder into .ffmpeg-cache\windows\
    ```

    Then copy the cached binaries into the build — Windows searches an exe's own folder before
    `PATH`, so this is picked up automatically, no launcher wrapper needed:

    ```
    copy /Y ".ffmpeg-cache\windows\ffmpeg.exe" "app\build\windows\portable\Luminary\ffmpeg.exe"
    copy /Y ".ffmpeg-cache\windows\ffprobe.exe" "app\build\windows\portable\Luminary\ffprobe.exe"
    ```

    If there's no internet access at build time, the script falls back to a system-installed
    ffmpeg if one happens to be present, and otherwise skips bundling — the app still runs, just
    with limited video metadata/thumbnails.

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
    pyinstaller --clean --onedir --distpath "app/build/linux/portable" --name "Luminary" app/src/backend/app.py
    ```

- copy the frontend (html/css/js) into the built app

    `app.py` resolves its frontend folder as `BASE_DIR.parent / "frontend"` — it needs to live
    inside the `Luminary/` output folder, alongside the `Luminary` binary. `--add-data` isn't used
    for this since the source layout doesn't match the frozen layout, so copy it manually instead:

    `cp -r "app/src/frontend" "app/build/linux/portable/Luminary/frontend"`

- bundle ffmpeg/ffprobe (always, regardless of what's installed on this machine)

    Same reasoning as Windows — end users won't have ffmpeg installed, so `build-linux.sh`
    downloads a static build (from johnvansickle.com, arch-detected via `uname -m`) and caches it
    in `.ffmpeg-cache/linux/` so it's only fetched once:

    ```
    curl -fL -o .ffmpeg-cache/linux/ffmpeg.tar.xz \
        "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
    tar -xf .ffmpeg-cache/linux/ffmpeg.tar.xz -C .ffmpeg-cache/linux --strip-components=1
    ```

    Then copy the cached binaries into the build. Unlike Windows, Linux does not search a
    binary's own folder for bare command names — a plain `./Luminary` would miss a copy placed
    next to it — so also generate a launcher that puts that folder on `PATH` first:

    ```
    cp .ffmpeg-cache/linux/ffmpeg  app/build/linux/portable/Luminary/ffmpeg
    cp .ffmpeg-cache/linux/ffprobe app/build/linux/portable/Luminary/ffprobe
    chmod +x app/build/linux/portable/Luminary/ffmpeg app/build/linux/portable/Luminary/ffprobe
    ```

    ```bash
    #!/usr/bin/env bash
    DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    export PATH="$DIR:$PATH"
    exec "$DIR/Luminary" "$@"
    ```
    Save that as `Luminary/run-luminary.sh`, `chmod +x` it, and launch the app through it instead
    of `./Luminary` directly.

    If there's no internet access at build time (or the architecture isn't `x86_64`/`aarch64`),
    the script falls back to a system-installed ffmpeg if one happens to be present, and otherwise
    skips bundling — the app still runs, just with limited video metadata/thumbnails.

- deactivate `venv-linux`

    `deactivate`