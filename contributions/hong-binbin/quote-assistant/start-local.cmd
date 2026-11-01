@echo off
setlocal
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Project Python environment not found. See local_lab\README.md.
  pause
  popd
  exit /b 1
)
".venv\Scripts\python.exe" -m streamlit run local_lab\app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
popd
endlocal

