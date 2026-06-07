#!/bin/bash
# Cognitect FreeCAD Smoke Test
# Tests that FreeCAD headless runs correctly on this server

set -e

APPIMAGE="/data/workspace/freecad/FreeCAD.AppImage"
SQUASHFS="/data/workspace/cognitect/squashfs-root"
FREECADCMD="$SQUASHFS/usr/bin/freecadcmd"
FREECAD_LIBS="$SQUASHFS/usr/lib:$SQUASHFS/usr/lib/x86_64-linux-gnu"
VENV="/data/workspace/cognitect/.venv/bin/python3"

echo "=== Cognitect FreeCAD Smoke Test ==="
echo ""

# Test 1: AppImage exists
echo -n "[1] FreeCAD AppImage exists... "
if [ -f "$APPIMAGE" ]; then echo "PASS"; else echo "FAIL — not found at $APPIMAGE"; exit 1; fi

# Test 2: Extracted AppImage exists
echo -n "[2] Extracted squashfs-root exists... "
if [ -d "$SQUASHFS" ]; then echo "PASS"; else echo "FAIL — run: cd /data/workspace/cognitect && /data/workspace/freecad/FreeCAD.AppImage --appimage-extract"; exit 1; fi

# Test 3: FreeCAD import
echo -n "[3] FreeCAD import + version... "
RESULT=$(cd "$SQUASHFS" && LD_LIBRARY_PATH="$FREECAD_LIBS" "$FREECADCMD" -c "import FreeCAD; print('OK:', FreeCAD.Version()[0])" 2>&1 | tail -1)
if echo "$RESULT" | grep -q "OK:"; then echo "PASS ($RESULT)"; else echo "FAIL: $RESULT"; exit 1; fi

# Test 4: Python deps
echo -n "[4] Python deps (kiwisolver, ezdxf, anthropic)... "
RESULT=$("$VENV" -c "import kiwisolver, ezdxf, anthropic; print('OK')" 2>&1)
if echo "$RESULT" | grep -q "OK"; then echo "PASS"; else echo "FAIL: $RESULT"; exit 1; fi

# Test 5: kiwisolver solve
echo -n "[5] kiwisolver basic solve... "
RESULT=$("$VENV" -c "
import kiwisolver as k
solver = k.Solver()
x = k.Variable('x')
solver.addConstraint(x >= 10)
solver.addConstraint(x <= 20)
solver.updateVariables()
assert 10 <= x.value() <= 20, f'x={x.value()}'
print('OK:', x.value())
" 2>&1)
if echo "$RESULT" | grep -q "OK:"; then echo "PASS ($RESULT)"; else echo "FAIL: $RESULT"; exit 1; fi

# Test 6: ezdxf DXF creation
echo -n "[6] ezdxf DXF creation... "
RESULT=$("$VENV" -c "
import ezdxf, tempfile, os
doc = ezdxf.new('R2010')
msp = doc.modelspace()
msp.add_lwpolyline([(0,0),(10,0),(10,10),(0,10),(0,0)])
with tempfile.NamedTemporaryFile(suffix='.dxf', delete=False) as f:
    tmp = f.name
doc.saveas(tmp)
size = os.path.getsize(tmp)
os.unlink(tmp)
print(f'OK: {size} bytes')
" 2>&1)
if echo "$RESULT" | grep -q "OK:"; then echo "PASS ($RESULT)"; else echo "FAIL: $RESULT"; exit 1; fi

echo ""
echo "=== ALL TESTS PASSED ==="
echo ""
echo "FreeCAD invocation for engine:"
echo "  cd $SQUASHFS"
echo "  LD_LIBRARY_PATH=\"$FREECAD_LIBS\" $FREECADCMD -c \"<your script>\""
