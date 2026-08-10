# 根级 conftest.py
#
# 作用：让 pytest 把仓库根目录加入 sys.path，使 tests/ 与 evals/ 下的测试
# 能够 `from app... import ...`。
#
# 背景：CI 用裸 `pytest`（非 `python -m pytest`）调用，且仓库无 pytest.ini /
# pyproject.toml / setup.py / tests/__init__.py，否则 app 包不可导入，
# 收集阶段即报 ModuleNotFoundError: No module named 'app'。
# 本文件是 pytest 官方推荐的最小修法，无需改动打包或 CI 调用方式。
