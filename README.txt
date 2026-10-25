SCXFYY'S MUSIC BAR — WINDOWS APP BUILD

This project is prepared to build a standalone Windows .exe. The person RUNNING
the finished .exe does not need Python installed. Python is used only by the
online build service to compile the app.

HOW TO GET THE EXE:
1. Create/sign in to a GitHub account.
2. Create a new repository (for example: scxfyy-music-bar).
3. Upload all files and folders from this ZIP to the repository, including the
   .github folder. Keep the folder structure.
4. Open the repository's Actions tab.
5. Select "Build Windows EXE" and click "Run workflow" (or push to main).
6. When the run finishes successfully, open that workflow run and download the
   artifact named "scxfyy-Music-Bar-Windows".
7. Extract the downloaded ZIP and run "scxfyy Music Bar.exe".

NOTES:
- This app is for Windows and reads Windows' system-wide media session.
- Windows SmartScreen may show a warning because the EXE is not code-signed.
- The build artifact is created by GitHub Actions; this ZIP is the source/build
  package, not the compiled EXE itself.
- Controls in the app: drag to move, click the lock icon or press Ctrl+Alt+L
  to toggle click-through lock, and click X to close.
