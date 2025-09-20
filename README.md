# Payroll Batch + Pipeline (GPT-5)

## Estructura
- `payroll_pipeline.py` — pipeline con preflight de datos (missing-policy) y Structured Outputs
- `batch_run.py` — ejecutor por lotes con `--missing-policy ask|default|fail`
- `schemas/` — JSON Schemas (input/result) compatibles con Structured Outputs “estricto”
- `samples/inputs.jsonl` — casos de ejemplo
- `outputs/` — carpeta de resultados

## Uso rápido
```bash
cd project
python -m venv .venv && source .venv/bin/activate
pip install --upgrade openai jsonschema python-dotenv
export OPENAI_API_KEY="sk-..."
python batch_run.py --input samples/inputs.jsonl --workers 2 --missing-policy default
# Interactivo:
python batch_run.py --input samples/inputs.jsonl --missing-policy ask
```
