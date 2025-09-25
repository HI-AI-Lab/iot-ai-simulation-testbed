#!/usr/bin/env bash
set -euo pipefail

TB="${TB:-/workspace/testbed}"
SRC="$TB/bridge/src"
CLS="$TB/bridge/classes"
JLIBS="$TB/jlibs"
OUT="$JLIBS/cooja-py4j-bridge.jar"

# Where Docker put the Java-side Py4J jar (for javac classpath only)
PY4J_JAR="${PY4J_JAR:-/opt/py4j/py4j-0.10.9.7.jar}"

# Where to drop the bridge jar so COOJA sees it
COOJA_LIB="${COOJA_LIB:-/workspace/contiki-ng/tools/cooja/dist/lib}"

# Preconditions
[ -f "$PY4J_JAR" ] || { echo "[bridge] Py4J JAR not found: $PY4J_JAR"; exit 1; }
[ -d "$SRC" ] || { echo "[bridge] Source dir missing: $SRC"; exit 1; }

mkdir -p "$CLS" "$JLIBS"

# Rebuild only if needed
need_build=1
if [ -f "$OUT" ]; then
  if find "$SRC" -name '*.java' -newer "$OUT" | read; then
    need_build=1
  else
    need_build=0
  fi
fi

if [ "$need_build" -eq 1 ]; then
  echo "[bridge] Compiling…"
  javac -cp "$PY4J_JAR" -d "$CLS" $(find "$SRC" -name '*.java' | sort)
  jar cf "$OUT" -C "$CLS" .
  echo "[bridge] Built: $OUT"
else
  echo "[bridge] Up-to-date: $OUT"
fi

# Install ONLY the bridge jar into COOJA (Py4J jar is handled by Docker)
if [ -d "$COOJA_LIB" ]; then
  install -m0644 -T "$OUT" "$COOJA_LIB/$(basename "$OUT")"
  echo "[bridge] Installed bridge -> $COOJA_LIB/$(basename "$OUT")"
else
  echo "[bridge] NOTE: $COOJA_LIB not found; mount contiki-ng and rerun." >&2
fi
