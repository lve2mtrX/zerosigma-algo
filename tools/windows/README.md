# Windows Desktop Launcher

`Launch_ZeroSigma_Algo_Cockpit.bat` provides a desktop-style way to run the
local ZeroSigma Algo Cockpit on Windows. It:

1. Checks that the repository and `.venv` exist.
2. Starts Streamlit minimized and headless on `127.0.0.1:8501`.
3. Opens the cockpit in a dedicated Microsoft Edge app window.
4. Waits for that Edge app window to close.
5. Kills the process listening on port `8501` and closes the launcher terminal.

This launcher is local only. It does not enable broker execution or place
orders.

## Put the launcher on the Desktop

From the repository root, run:

```powershell
.\tools\windows\copy_launcher_to_desktop.ps1
```

The helper copies the batch file to the current user's Desktop without
requiring administrator access. It asks before replacing an existing Desktop
copy. To replace it without a prompt:

```powershell
.\tools\windows\copy_launcher_to_desktop.ps1 -Force
```

You can also copy `Launch_ZeroSigma_Algo_Cockpit.bat` manually or create a
shortcut to it.

## Use on another Dropbox machine

The launcher is intentionally PC-specific. Open the copied `.bat` file in a
text editor and change this line to the repository's location on that machine:

```bat
set "REPO=C:\Users\danca\Dropbox\Trading\ZeroSigma\zerosigma-algo"
```

The repository must contain a working `.venv` at `.venv\Scripts\activate.bat`.

## Why Edge app mode is used

Edge's `--app` mode gives the cockpit its own window instead of opening a
normal browser tab. The launcher uses `start /wait` on that app window as the
lifecycle signal: closing the window tells the launcher to stop Streamlit and
close its terminal. If Edge is unavailable, the launcher opens the default
browser and waits for manual confirmation because a normal browser tab cannot
provide the same reliable close signal.

## Port cleanup warning

When the Edge app window closes, the launcher force-stops the process listening
on port `8501`. Do not run another service on port `8501` while using this
launcher. If the port is already occupied before launch, stop that process or
change `PORT` and `URL` together in the batch file.

## Troubleshooting

- **Repo folder not found:** Edit the batch file's `REPO` value so it matches
  the local Dropbox/repository path.
- **Venv not found:** Create `.venv` in the repository and install the project,
  or update the launcher if the environment lives elsewhere.
- **Port already in use:** Stop the existing service on `8501`, or change both
  `PORT` and `URL`. Cleanup stops whichever process is listening on that port.
- **Edge not found:** Install Microsoft Edge in its standard location, or use
  the default-browser fallback and press a key when ready to stop Streamlit.
- **Browser opens twice:** Close any separately started Streamlit process and
  confirm the launcher command still includes `--server.headless true`.
- **Dashboard does not close cleanly:** Close the dedicated Edge app window,
  then close any remaining minimized Streamlit terminal. Check port `8501`
  with `Get-NetTCPConnection -LocalPort 8501 -State Listen`.
