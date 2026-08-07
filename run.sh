#!/usr/bin/env bash
# 本地开发：带热重载
python -m uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}" --reload
