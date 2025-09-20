# Batch tester — Nóminas con GPT-5

Este paquete te permite probar el pipeline de cálculo de nóminas en **lote** contra GPT-5 con **Structured Outputs**.

## Requisitos
- Python 3.10+
- Ficheros del proyecto en una carpeta con:
  - `payroll_pipeline.py` (del paso anterior)
  - `schemas/payroll_input.schema.json`
  - `schemas/payroll_result.schema.json`
- `pip install --upgrade openai jsonschema python-dotenv`
- Variable `OPENAI_API_KEY` en el entorno
- (Opcional) `OPENAI_MODEL` (por defecto `gpt-5`)

## Archivos creados aquí
- `batch_run.py` — ejecutor por lotes (lee JSONL y escupe resultados por archivo)
- `samples/inputs.jsonl` — 2 casos de prueba
- `outputs/` — carpeta destino (se crea sola)

## Cómo ejecutar
```bash
# 1) Crea y activa entorno
python -m venv .venv && source .venv/bin/activate

# 2) Instala dependencias
pip install --upgrade openai jsonschema python-dotenv

# 3) Configura la API Key
export OPENAI_API_KEY="sk-..."

# 4) Asegúrate de tener payroll_pipeline.py y los schemas en ./schemas/
#    (usa los que ya te he dado).

# 5) Lanza el batch con el JSONL de ejemplo
python batch_run.py --input samples/inputs.jsonl --workers 2

# (Opcional) forzar modelo o variar concurrencia
OPENAI_MODEL=gpt-5-thinking python batch_run.py --input samples/inputs.jsonl --workers 4
```

## Formato de entrada (JSONL)
Cada línea es un objeto que cumple **PayrollInputSchema**.
Ejemplo mínimo:
```json
{"period":{"year":2025,"month":9,"payroll_days":30,"calendar":{"national_holidays":[],"regional_holidays":[],"local_holidays":[]}},"region_config":{"ccaa":"Cataluña"},"worker":{"nif":"123","address":{"province":"Barcelona","municipality":"Barcelona"},"form145":{"marital_status":"single","children":0,"dependents_other":0,"disability_pct":0},"contribution_group":1},"contract":{"type":"indefinido","hours_per_week":40,"work_schedule":"full_time"},"collective_agreement":{"code":"REGCON-XXXX","scope":"Sector","level":"provincial","salary_table_version":"2025-01","category":"Grupo A","pay_structure":{"extra_pay_count":14,"extra_prorated":false},"allowances":[]},"compensation":{"base_salary_month":2200.0,"variables":[],"overtime":[]},"incidents":[]}
```

## Resultados
- Por cada entrada válida, se crea `outputs/<index>_<CCAA>_<MM-YYYY>.json` con un **PayrollResult** válido.
- Si hay errores, se genera `outputs/errors.ndjson` con el motivo por línea.

## Consejos
- Usa `--workers` para acelerar (I/O bound). No abuses para no topar rate limits.
- Mantén `temperature=0` en `payroll_pipeline.py` para reproducibilidad.
- Versiona tus schemas y registra en `trace.tables_version` las fuentes (Orden de cotización, SMI, IRPF).
