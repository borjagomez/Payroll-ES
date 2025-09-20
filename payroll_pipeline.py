# payroll_pipeline.py — with preflight + missing-policy and Structured Outputs (schema strict)
from __future__ import annotations
import os, json, pathlib
from dataclasses import dataclass
from typing import Any, Dict, List
from jsonschema import Draft202012Validator, exceptions as js_exc
from openai import OpenAI
from dotenv import load_dotenv

# ----------------------------
# 0) Config
# ----------------------------
load_dotenv()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("Falta OPENAI_API_KEY en el entorno")
client = OpenAI(api_key=OPENAI_API_KEY)

ROOT = pathlib.Path(__file__).parent
SCHEMA_INPUT_PATH = ROOT / "schemas" / "payroll_input.schema.json"
SCHEMA_OUTPUT_PATH = ROOT / "schemas" / "payroll_result.schema.json"

def _load_json(path: pathlib.Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

PAYROLL_INPUT_SCHEMA: dict = _load_json(SCHEMA_INPUT_PATH)
PAYROLL_RESULT_SCHEMA: dict = _load_json(SCHEMA_OUTPUT_PATH)

# ----------------------------
# 1) CCAA → IRPF map
# ----------------------------
CCAA_REGION_MAP: dict[str, dict] = {
    "Andalucía": {"irpf_regime": "AEAT"},
    "Aragón": {"irpf_regime": "AEAT"},
    "Principado de Asturias": {"irpf_regime": "AEAT"},
    "Illes Balears": {"irpf_regime": "AEAT"},
    "Canarias": {"irpf_regime": "AEAT"},
    "Cantabria": {"irpf_regime": "AEAT"},
    "Castilla-La Mancha": {"irpf_regime": "AEAT"},
    "Castilla y León": {"irpf_regime": "AEAT"},
    "Cataluña": {"irpf_regime": "AEAT"},
    "Comunitat Valenciana": {"irpf_regime": "AEAT"},
    "Extremadura": {"irpf_regime": "AEAT"},
    "Galicia": {"irpf_regime": "AEAT"},
    "Comunidad de Madrid": {"irpf_regime": "AEAT"},
    "Región de Murcia": {"irpf_regime": "AEAT"},
    "La Rioja": {"irpf_regime": "AEAT"},
    "Comunidad Foral de Navarra": {"irpf_regime": "FORAL_NAVARRA"},
    "País Vasco": {"irpf_regime": "FORAL_PV"},
    "Ceuta": {"irpf_regime": "AEAT"},
    "Melilla": {"irpf_regime": "AEAT"},
}

# ----------------------------
# 2) Prompt
# ----------------------------
BASE_PROMPT = """
Eres un motor experto en cálculo de nóminas en España (año en curso).
Objetivo: devolver una nómina correcta y trazable a partir de un JSON de entrada que cumple el “PayrollInputSchema”.

Instrucciones de cálculo y validación:
1) Jurisdicción y tablas:
   - Determina la jurisdicción IRPF con input.region_config.irpf_regime.
   - Si "AEAT": usa algoritmo y parámetros estatales del ejercicio.
   - Si "FORAL_NAVARRA": usa tablas y reglas de Navarra.
   - Si "FORAL_PV": selecciona la diputación foral según worker.address.province
     ("Araba/Álava", "Bizkaia", "Gipuzkoa") y aplica sus tablas.
   - Seguridad Social: siempre estatal.

2) Convenio y estructura retributiva:
   - Usa input.collective_agreement.* (salario base, complementos, pagas extra, pluses, jornada).
   - Si el salario < SMI para la jornada, eleva a SMI.

3) Devengos:
   - Salario base + complementos + prorrata de extras (si aplica) + horas extra.

4) Bases de cotización:
   - Aplica topes mín./máx., prorrata extras, MEI y solidaridad si procede.

5) Cuotas SS (trabajador y empresa):
   - CC, AT/EP (tarifa de primas), desempleo, FOGASA, formación, MEI, solidaridad.

6) IRPF:
   - Usa worker.form145 (o foral).
   - Calcula retención según jurisdicción.

7) Incidencias:
   - IT/MA/PA/ERTE según LGSS/orden anual.

8) Neto y validaciones:
   - Neto = Devengos – (SS trabajador + IRPF + otros descuentos).
   - Valida SMI, topes base, CRA y calendario/festivos.

9) Salida:
   - Devuelve JSON “PayrollResult” con desglose completo, CRA por concepto, trace y advertencias.

Responde SOLO con JSON válido que cumpla “PayrollResultSchema”. No incluyas texto adicional.
""".strip()

# ----------------------------
# 3) Utils
# ----------------------------
def validate_with_schema(payload: dict, schema: dict, name: str) -> None:
    try:
        Draft202012Validator(schema).validate(payload)
    except js_exc.ValidationError as e:
        raise ValueError(f"Error de validación contra {name}: {e.message}\nRuta: {'/'.join(map(str, e.path))}") from e

def enrich_region_config(payload: dict) -> dict:
    data = json.loads(json.dumps(payload))
    ccaa = data.get("region_config", {}).get("ccaa")
    if not ccaa:
        return data
    rc = data.setdefault("region_config", {})
    rc.setdefault("notes", "")
    if "irpf_regime" not in rc and ccaa in CCAA_REGION_MAP:
        rc["irpf_regime"] = CCAA_REGION_MAP[ccaa]["irpf_regime"]
    return data

# ----------------------------
# 4) Preflight & Missing-Policy
# ----------------------------
@dataclass
class MissingField:
    path: str
    question: str
    hint: str
    type: str
    enum: List[str] | None = None
    default: object | None = None

def _set_by_path(d: dict, path_parts: List[str], value: Any):
    cur = d
    for p in path_parts[:-1]:
        if p not in cur or not isinstance(cur[p], (dict, list)):
            cur[p] = {}
        cur = cur[p]
    cur[path_parts[-1]] = value

def _parse_input(value_str: str, mf: MissingField):
    if mf.type == "number":
        return float(value_str.replace(",", "."))
    if mf.type == "enum":
        v = value_str.strip()
        if mf.enum and v not in mf.enum:
            raise ValueError(f"Valor '{v}' no está en {mf.enum}")
        return v
    return value_str.strip()

def detect_missing(payload: dict) -> List[MissingField]:
    missing: List[MissingField] = []

    # Plus Convenio en allowances sin importe declarado en compensation.variables
    has_plus_in_allowances = any((it.get("name","").lower() == "plus convenio") for it in payload.get("collective_agreement", {}).get("allowances", []))
    has_plus_in_comp = any((v.get("name","").lower() == "plus convenio") for v in payload.get("compensation", {}).get("variables", []))
    if has_plus_in_allowances and not has_plus_in_comp:
        missing.append(MissingField(
            path="compensation.plus_convenio_amount",
            question="¿Importe mensual del 'Plus Convenio' (€)?",
            hint="Introduce la cuantía bruta mensual.",
            type="number",
            default=0.0
        ))

    # Tarifa AT/EP si no hay CNAE ni tarifa explícita
    company = payload.setdefault("company", {})
    if not company.get("cnae") and not company.get("atep_tariff_pct"):
        missing.append(MissingField(
            path="company.atep_tariff_pct",
            question="Sin CNAE: indica tarifa AT/EP (%) p.ej. 1.50",
            hint="Si conoces el CNAE, mejor añádelo y deja vacío aquí.",
            type="number",
            default=1.50
        ))

    # Año de tablas: cotización e IRPF
    period_year = payload.get("period", {}).get("year")
    tables = payload.setdefault("tables", {})
    if not tables.get("cotization_year"):
        missing.append(MissingField(
            path="tables.cotization_year",
            question=f"¿Año de tablas de cotización a aplicar? (p.ej. {period_year})",
            hint="Normalmente coincide con el año del período.",
            type="number",
            default=period_year
        ))
    if not tables.get("irpf_year"):
        missing.append(MissingField(
            path="tables.irpf_year",
            question=f"¿Año de tablas IRPF a aplicar? (p.ej. {period_year})",
            hint="AEAT o forales del ejercicio.",
            type="number",
            default=period_year
        ))

    # CRA base salarial
    if not payload.get("compensation", {}).get("base_salary_cra_code"):
        missing.append(MissingField(
            path="compensation.base_salary_cra_code",
            question="Código CRA para salario base (p.ej. C01):",
            hint="Si no sabes, usa C01.",
            type="string",
            default="C01"
        ))

    # NIF
    if not payload.get("worker", {}).get("nif"):
        missing.append(MissingField(
            path="worker.nif",
            question="NIF del trabajador (formato 12345678Z). Déjalo vacío si no aplica:",
            hint="No afecta al cálculo pero sí a trazabilidad.",
            type="string",
            default="NO-INFORMADO"
        ))

    # Régimen IRPF
    if not payload.get("region_config", {}).get("irpf_regime"):
        missing.append(MissingField(
            path="region_config.irpf_regime",
            question="Régimen IRPF (AEAT | FORAL_NAVARRA | FORAL_PV):",
            hint="Por Cataluña: AEAT.",
            type="enum",
            enum=["AEAT","FORAL_NAVARRA","FORAL_PV"],
            default="AEAT"
        ))

    return missing

def resolve_missing(payload: dict, missing: List[MissingField], policy: str = "ask") -> tuple[dict, List[str]]:
    warnings: List[str] = []
    if not missing:
        return payload, warnings

    if policy == "fail":
        names = [m.path for m in missing]
        raise ValueError("Faltan datos críticos: " + "; ".join(names))

    for m in missing:
        if policy == "ask":
            q = f"{m.question} [{m.hint}]"
            if m.type == "enum" and m.enum:
                q += f" Opciones: {', '.join(m.enum)}"
            raw = input(q + "\n> ").strip()
            if raw == "" and m.default is not None:
                value = m.default
                warnings.append(f"Usado valor por defecto en {m.path}: {m.default}")
            elif raw == "":
                raise ValueError(f"Dato obligatorio no proporcionado: {m.path}")
            else:
                value = _parse_input(raw, m)
        else:  # default
            value = m.default
            warnings.append(f"Valor por defecto aplicado en {m.path}: {m.default}")

        # Caso especial: plus convenio -> insertar en compensation.variables
        if m.path == "compensation.plus_convenio_amount":
            comp = payload.setdefault("compensation", {})
            vars_ = comp.setdefault("variables", [])
            found = None
            for v in vars_:
                if (v.get("name","").lower() == "plus convenio"):
                    found = v; break
            if not found:
                found = {"name": "Plus Convenio", "taxable": True, "contributory": True, "cra_code": "C02"}
                vars_.append(found)
            found["amount"] = float(value) if value is not None else 0.0
        else:
            _set_by_path(payload, m.path.split("."), value)

    return payload, warnings

# ----------------------------
# 5) Call GPT-5
# ----------------------------
def call_gpt5_compute_payroll(input_payload: dict, missing_policy: str = "fail") -> dict:
    validate_with_schema(input_payload, PAYROLL_INPUT_SCHEMA, "PayrollInputSchema")
    enriched = enrich_region_config(input_payload)
    missing = detect_missing(enriched)
    enriched, preflight_warnings = resolve_missing(enriched, missing, policy=missing_policy)

    messages = [
        {"role": "developer", "content": BASE_PROMPT},
        {"role": "user", "content": json.dumps(enriched, ensure_ascii=False)},
    ]

    resp = client.responses.create(
        model=OPENAI_MODEL,
        input=messages,
        text={
            "format": {
                "type": "json_schema",
                "schema": PAYROLL_RESULT_SCHEMA,
                "strict": True,
                "name": "PayrollResult"
            },
            "verbosity": "low",
        },
        reasoning={"effort": "medium"}
    )

    output_obj: dict | None = None
    for item in resp.output:
        if getattr(item, "type", None) == "message":
            for c in getattr(item, "content", []):
                if getattr(c, "type", None) == "output_text":
                    output_obj = getattr(c, "parsed", None) or json.loads(c.text)
                    break
    if output_obj is None:
        raise RuntimeError("No se pudo extraer la salida del modelo.")

    if preflight_warnings:
        out_w = output_obj.setdefault("warnings", [])
        out_w.extend(preflight_warnings)

    validate_with_schema(output_obj, PAYROLL_RESULT_SCHEMA, "PayrollResultSchema")
    return output_obj
