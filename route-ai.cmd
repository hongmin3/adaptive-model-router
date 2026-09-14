@echo off
setlocal
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
py -m adaptive_model_router.cli %*
