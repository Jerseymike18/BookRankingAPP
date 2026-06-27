"""Unit tests for research_layer._extract_json."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

# Import only _extract_json without triggering the rest of research_layer's imports.
import importlib, types

# Minimal stub so research_layer can be imported without numpy/pandas/anthropic.
for mod in ["numpy", "pandas", "anthropic", "predict_engine"]:
    if mod not in sys.modules:
        stub = types.ModuleType(mod)
        if mod == "predict_engine":
            stub.WORKBOOK = "stub"
        sys.modules[mod] = stub

import research_layer as rl

EXPECTED = {"Plot": 7.5, "Depth": 8.0}

def test_clean_json():
    assert rl._extract_json('{"Plot": 7.5, "Depth": 8.0}') == EXPECTED

def test_fenced_json():
    src = '```json\n{"Plot": 7.5, "Depth": 8.0}\n```'
    assert rl._extract_json(src) == EXPECTED

def test_fenced_no_lang():
    src = '```\n{"Plot": 7.5, "Depth": 8.0}\n```'
    assert rl._extract_json(src) == EXPECTED

def test_trailing_fence():
    src = '{"Plot": 7.5, "Depth": 8.0}\n```'
    assert rl._extract_json(src) == EXPECTED

def test_json_plus_prose():
    src = '{"Plot": 7.5, "Depth": 8.0}\n\nHere is some explanatory text.'
    assert rl._extract_json(src) == EXPECTED

def test_leading_prose():
    src = 'Sure! Here you go:\n{"Plot": 7.5, "Depth": 8.0}'
    assert rl._extract_json(src) == EXPECTED

def test_no_fence_plain():
    src = '{"Plot": 7.5, "Depth": 8.0}'
    assert rl._extract_json(src) == EXPECTED

def test_array():
    src = '[1, 2, 3]'
    assert rl._extract_json(src) == [1, 2, 3]

if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {fn.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
