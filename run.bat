@echo off
REM 本地开发：带热重载
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
