#!/usr/bin/env bash
# smoke_test_freecad.sh — verify FreeCAD AppImage + Python deps are functional
set -euo pipefail

FREECAD_DIR="/data/workspace/freecad"
FREECAD_APPIMAGE="$FREECAD_DIR/FreeCAD.AppImage"
FREECAD_URL="https://github.com/FreeCAD/FreeCAD/releases/download/1.0.0/FreeCAD_1.0.0-conda-Linux-x86_64-py311.AppImage"
PASS_COUNT=0
FAIL_COUNT=0

# Use venv python if available, else system python
VENV_PYTHON="/data/workspace/cognitect/.venv/bin/python3"
if [ -f "$VENV_PYTHON" ]; then
    PYTHON="$VENV_PYTHON"
else
    PYTHON="python3"
fi

echo "============================================="
echo "  Cognitect — FreeCAD Smoke Test"
echo "============================================="

# ── Helper functions ──────────────────────────────────────────────────────────
pass() { echo "[PASS] $1"; ((PASS_COUNT++)); }
fail() { echo "[FAIL] $1"; ((FAIL_COUNT++)); }

# ── Test 1: Python deps ───────────────────────────────────────────────────────
echo ""
echo "--- Test 1: Python dependencies ---"
if "$PYTHON" -c "import kiwisolver; import ezdxf; import anthropic; import fastapi; import pydantic; print('All Python deps OK')" 2>&1; then
    pass "Python dependencies importable"
else
    fail "Python dependencies missing — run: pip install -r requirements.txt"
fi

# ── Test 2: FreeCAD AppImage presence / download ─────────────────────────────
echo ""
echo "--- Test 2: FreeCAD AppImage ---"
mkdir -p "$FREECAD_DIR"

if [ -f "$FREECAD_APPIMAGE" ]; then
    echo "FreeCAD AppImage already present at $FREECAD_APPIMAGE"
else
    echo "FreeCAD AppImage not found. Attempting download..."
    echo "URL: $FREECAD_URL"
    if command -v wget &>/dev/null; then
        wget -q --show-progress -O "$FREECAD_APPIMAGE" "$FREECAD_URL" && echo "Download complete."
    elif command -v curl &>/dev/null; then
        curl -L --progress-bar -o "$FREECAD_APPIMAGE" "$FREECAD_URL" && echo "Download complete."
    else
        fail "Neither wget nor curl available. Cannot download FreeCAD AppImage."
        echo ""
        echo "Manual steps:"
        echo "  wget -O $FREECAD_APPIMAGE $FREECAD_URL"
        FREECAD_APPIMAGE=""
    fi
fi

if [ -n "$FREECAD_APPIMAGE" ] && [ -f "$FREECAD_APPIMAGE" ]; then
    chmod +x "$FREECAD_APPIMAGE"
    pass "FreeCAD AppImage present and executable"
else
    fail "FreeCAD AppImage not available (download may have failed)"
fi

# ── Test 3: FreeCAD headless invocation ──────────────────────────────────────
echo ""
echo "--- Test 3: FreeCAD headless Python ---"
if [ -f "$FREECAD_APPIMAGE" ]; then
    FREECAD_TEST_SCRIPT=$(mktemp /tmp/freecad_test_XXXXXX.py)
    cat > "$FREECAD_TEST_SCRIPT" << 'PYEOF'
import FreeCAD
import Part
import Draft

print("FreeCAD OK:", FreeCAD.Version())
print("FreeCAD version string:", FreeCAD.Version()[0], FreeCAD.Version()[1])

# Minimal geometry test: create a wire rectangle
doc = FreeCAD.newDocument("SmokeTest")
rect = doc.addObject("Part::Box", "TestBox")
rect.Length = 10.0
rect.Width = 8.0
rect.Height = 0.1  # thin slab ~ floor plan
doc.recompute()
print("Part::Box creation OK — Volume:", rect.Shape.Volume)
doc.save("/tmp/cognitect_smoke_test.FCStd")
print("Document save OK")
FreeCAD.closeDocument("SmokeTest")
print("FREECAD_SMOKE_PASS")
PYEOF

    set +e
    FREECAD_OUTPUT=$("$FREECAD_APPIMAGE" --appimage-extract-and-run --headless -c "exec(open('$FREECAD_TEST_SCRIPT').read())" 2>&1)
    FREECAD_EXIT=$?
    set -e
    rm -f "$FREECAD_TEST_SCRIPT"

    echo "$FREECAD_OUTPUT" | tail -10

    if echo "$FREECAD_OUTPUT" | grep -q "FREECAD_SMOKE_PASS"; then
        pass "FreeCAD headless Python execution"
        VERSION_LINE=$(echo "$FREECAD_OUTPUT" | grep "FreeCAD OK:" | head -1)
        echo "  → $VERSION_LINE"
    else
        fail "FreeCAD headless execution failed (exit code: $FREECAD_EXIT)"
        echo "  Full output:"
        echo "$FREECAD_OUTPUT"
    fi
else
    echo "[SKIP] FreeCAD AppImage not available — skipping headless test"
fi

# ── Test 4: ezdxf basic DXF creation ─────────────────────────────────────────
echo ""
echo "--- Test 4: ezdxf DXF creation ---"
if "$PYTHON" - << 'PYEOF' 2>&1; then
import ezdxf
doc = ezdxf.new('R2010')
msp = doc.modelspace()
# Add a simple rectangle representing a room
msp.add_lwpolyline([(0, 0), (20, 0), (20, 15), (0, 15), (0, 0)], close=True)
msp.add_text("LIVING ROOM", dxfattribs={"height": 1.0}).set_placement((2, 7))
doc.saveas("/tmp/cognitect_smoke_test.dxf")
print("ezdxf DXF creation OK")
PYEOF
    pass "ezdxf DXF creation"
else
    fail "ezdxf DXF creation failed"
fi

# ── Test 5: kiwisolver basic solve ────────────────────────────────────────────
echo ""
echo "--- Test 5: kiwisolver basic constraint solve ---"
if python3 - << 'PYEOF' 2>&1; then
import kiwisolver
import math

solver = kiwisolver.Solver()
x = kiwisolver.Variable("x")
w = kiwisolver.Variable("w")
h = kiwisolver.Variable("h")

solver.addConstraint((x >= 0.0) | "required")
solver.addConstraint((w >= 4.0) | "required")
solver.addConstraint((h >= 4.0) | "required")
# Target: ~300 sqft room, roughly square → side ≈ sqrt(300) ≈ 17.3
side = math.sqrt(300.0)
solver.addConstraint((w == side) | "strong")
solver.addConstraint((h == side) | "strong")
solver.updateVariables()
area = w.value() * h.value()
assert abs(area - 300.0) / 300.0 < 0.01, f"Area mismatch: {area}"
print(f"kiwisolver solve OK: w={w.value():.2f}, h={h.value():.2f}, area={area:.1f} sqft")
PYEOF
    pass "kiwisolver basic constraint solve"
else
    fail "kiwisolver constraint solve failed"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "============================================="
echo "  Results: $PASS_COUNT passed, $FAIL_COUNT failed"
echo "============================================="
if [ "$FAIL_COUNT" -eq 0 ]; then
    echo "  ✅ ALL SMOKE TESTS PASSED"
    exit 0
else
    echo "  ❌ SOME TESTS FAILED"
    exit 1
fi
