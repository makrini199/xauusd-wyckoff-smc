#!/usr/bin/env bash
# Descarga velas de 1 minuto de XAUUSD desde Dukascopy (gratis) con dukascopy-node.
# Uso: scripts/descargar_datos.sh 2023-01-01 2024-12-31
set -euo pipefail
DESDE="${1:-2024-01-01}"
HASTA="${2:-2024-12-31}"
mkdir -p data
npx --yes dukascopy-node -i xauusd -from "$DESDE" -to "$HASTA" -t m1 -f csv -v -dir data
echo "Datos guardados en data/"
