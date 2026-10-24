import ast
import csv
import os
import re
import statistics
import sys
import time
from collections import Counter
from decouple import config
from openai import OpenAI
from google import genai
from google.genai import types

# 1. Configurar API Keys
OPENAI_API_KEY = config("OPENAI_API_KEY")
GEMINI_API_KEY = config("GEMINI_API_KEY")

# 2. Contexto: fichero CSV que se sube a cada servidor
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "encuesta_facilitadores_2026.csv")

INSTRUCCIONES = (
    "Tienes adjunto el fichero encuesta_facilitadores_2026.csv con respuestas de una encuesta de satisfacción "
    "de estudiantes. Usa el intérprete de Python para leerlo (pandas) y responder "
    "con datos calculados, no inventados."
)

# Modo --cualitativa: mismas instrucciones que el chat de datos de tableIA
CHAT_TABLEIA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "sheets_reports", "services", "ai_data_chat.py")
PREGUNTAS_CUALITATIVAS = [
    # "¿Cuáles son las principales quejas de los estudiantes en los comentarios? Agrúpalas por tema.",
    "¿Por qué Cibao Oriental (Nagua) tiene distinta satisfacción con los facilitadores que Santiago? "
    "Apóyate en los datos.",
    # "¿Qué 3 mejoras recomendarías priorizar según la encuesta?",
]
THINKING_BUDGETS = [0]  # 0 = sin thinking; None = sin límite (lo que decida el modelo)
GEMINI_TEMPERATURE = 0.2  # la misma que el chat de datos en producción (ai_spec.generate_with_code_execution)
# (modelo, esfuerzo de razonamiento; None = el modelo no razona)
OPENAI_MODELOS = [("gpt-4o-mini", None), ("gpt-4.1-mini", None), ("gpt-5.4-mini", "low")]
ESCALA_SATISFACCION = {"completamente insatisfecho": 1, "insatisfecho": 2,
                       "satisfecho": 3, "completamente satisfecho": 4}
REPETICIONES_CUALITATIVA = 1
RESULTADOS_MD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resultados_cualitativo.md")
RESULTADOS_CUANTITATIVO_MD = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "resultados_cuantitativo.md")
RESPUESTAS_ESPERADAS_MD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "respuestas_esperadas.md")

GEMINI_TIMEOUT_MS = 300_000
REPETICIONES = 1

openai_client = OpenAI(api_key=OPENAI_API_KEY)
gemini_client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
)


# --- RESPUESTAS ESPERADAS (calculadas en local sobre el CSV original) ---
def leer_filas():
    with open(DATA_FILE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def columna(filas, texto):
    """Nombre de la primera columna que contiene `texto`."""
    return next(c for c in filas[0] if texto in c)


def esperado_recintos():
    return dict(Counter(row["Recinto:"] for row in leer_filas()).most_common())


def notas_recomendacion():
    filas = leer_filas()
    col = columna(filas, "probabilidad recomendaría")
    return [int(row[col]) for row in filas if row[col].strip()]


def esperado_nps():
    notas = notas_recomendacion()
    promotores = sum(n >= 9 for n in notas) / len(notas) * 100
    detractores = sum(n <= 6 for n in notas) / len(notas) * 100
    return {"% promotores": round(promotores, 1), "% detractores": round(detractores, 1),
            "NPS": round(promotores - detractores, 1)}


def esperado_media_recomendacion():
    return {"media": round(statistics.mean(notas_recomendacion()), 2)}


def esperado_mas_virtuales():
    filas = leer_filas()
    conteo = Counter(row["¿Le gustaría tomar más asignaturas en la modalidad virtual?"] for row in filas)
    return {"Sí": conteo["Sí"], "No": conteo["No"], "% Sí": round(conteo["Sí"] / len(filas) * 100, 1)}


def esperado_computador_e_internet():
    filas = leer_filas()
    pc, internet = columna(filas, "[Tiene computador personal]"), columna(filas, "[Tiene internet en su casa]")
    return {"con ambos": sum(row[pc] == "Si" and row[internet] == "Si" for row in filas)}


def esperado_moodle_por_escuela():
    filas = leer_filas()
    col = columna(filas, "[Funcionamiento de la plataforma virtual (Moodle)")
    por_escuela = {}
    for row in filas:
        valor = row[col].strip().lower()
        if valor in ESCALA_SATISFACCION:
            por_escuela.setdefault(row["Escuela a la que pertenece"], []).append(ESCALA_SATISFACCION[valor])
    medias = {e: round(statistics.mean(v), 2) for e, v in por_escuela.items()}
    return dict(sorted(medias.items(), key=lambda kv: -kv[1]))


PREGUNTAS_CUANTITATIVAS = [
    ("¿Cuántas respuestas hay por Recinto? Dame la tabla ordenada de mayor a menor.", esperado_recintos),
    ("Calcula el NPS de la pregunta de probabilidad de recomendar la UAPA (0-10): porcentaje de "
     "promotores (9-10), porcentaje de detractores (0-6) y NPS. Redondea a 1 decimal.", esperado_nps),
    ("¿Cuál es la media de la probabilidad de recomendar la UAPA (0-10)? Redondea a 2 decimales.",
     esperado_media_recomendacion),
    ("¿Cuántos estudiantes quieren tomar más asignaturas en modalidad virtual y cuántos no? "
     "¿Qué porcentaje del total dice que sí? Redondea a 1 decimal.", esperado_mas_virtuales),
    ("¿Cuántos estudiantes tienen a la vez computador personal e internet en su casa?",
     esperado_computador_e_internet),
    ("Media de satisfacción con el funcionamiento de la plataforma virtual (Moodle) por Escuela, en escala "
     "1-4 (1 = completamente insatisfecho, 4 = completamente satisfecho), ordenada de mayor a menor. "
     "Redondea a 2 decimales.", esperado_moodle_por_escuela),
]


def es_correcta(respuesta, esperado):
    """Cada valor esperado aparece entre los números de la respuesta (coma decimal = punto)."""
    numeros = [float(n.replace(",", ".")) for n in re.findall(r"\d+(?:[.,]\d+)?", respuesta)]
    tolerancia = lambda v: 0 if isinstance(v, int) else 0.051
    return all(any(abs(n - v) <= tolerancia(v) for n in numeros) for v in esperado.values())


def texto_esperado(esperado):
    return ", ".join(f"{k} {v}" for k, v in esperado.items())


# --- SUBIDA DE FICHEROS ---
def upload_openai(path):
    start_time = time.time()
    with open(path, "rb") as f:
        uploaded = openai_client.files.create(file=f, purpose="user_data")
    return uploaded, time.time() - start_time


def upload_gemini(path):
    start_time = time.time()
    uploaded = gemini_client.files.upload(
        file=path, config={"mime_type": "text/csv", "display_name": "encuesta_facilitadores_2026.csv"}
    )
    # Esperar a que el fichero esté listo para usarse
    while uploaded.state and uploaded.state.name == "PROCESSING":
        time.sleep(1)
        uploaded = gemini_client.files.get(name=uploaded.name)
    return uploaded, time.time() - start_time


# --- TEST OPENAI ---
def benchmark_openai(file_id, model_name="gpt-4o-mini", verbose=True,
                     pregunta="", instrucciones=INSTRUCCIONES, reasoning_effort=None):
    start_time = time.time()

    first_token_time = None
    full_response = ""

    extra = {"reasoning": {"effort": reasoning_effort}} if reasoning_effort else {}
    stream = openai_client.responses.create(
        model=model_name,
        instructions=instrucciones,
        input=pregunta,
        tools=[{
            "type": "code_interpreter",
            "container": {"type": "auto", "file_ids": [file_id]},
        }],
        stream=True,
        **extra,
    )

    for event in stream:
        if verbose and event.type == "response.code_interpreter_call_code.done":
            print(f"  [{time.time() - start_time:.1f} s] OpenAI ejecuta código:\n{event.code}")
        elif event.type == "response.output_text.delta":
            if first_token_time is None:
                first_token_time = time.time() - start_time  # TTFT
            full_response += event.delta

    total_time = time.time() - start_time
    return first_token_time, total_time, full_response


# --- TEST GEMINI ---
def benchmark_gemini(uploaded_file, model_name="gemini-2.5-flash", thinking_budget=None, verbose=True,
                     pregunta="", instrucciones=INSTRUCCIONES):
    start_time = time.time()
    first_token_time = None
    full_response = ""

    config = types.GenerateContentConfig(
        system_instruction=instrucciones,
        tools=[types.Tool(code_execution=types.ToolCodeExecution())],
        temperature=GEMINI_TEMPERATURE,
    )
    if thinking_budget is not None:
        config.thinking_config = types.ThinkingConfig(thinking_budget=thinking_budget)

    stream = gemini_client.models.generate_content_stream(
        model=model_name,
        contents=[uploaded_file, pregunta],
        config=config,
    )

    for chunk in stream:
        if not chunk.candidates or not chunk.candidates[0].content:
            continue
        for part in chunk.candidates[0].content.parts or []:
            elapsed = time.time() - start_time
            if part.executable_code:
                if verbose:
                    print(f"  [{elapsed:.1f} s] Gemini ejecuta código:\n{part.executable_code.code}")
            elif part.code_execution_result:
                if verbose:
                    print(f"  [{elapsed:.1f} s] Resultado ({part.code_execution_result.outcome}):\n"
                          f"{part.code_execution_result.output}")
            # Solo cuenta el texto final, no el código ni su salida
            elif part.text and not part.thought:
                if first_token_time is None:
                    first_token_time = elapsed  # TTFT
                full_response += part.text

    total_time = time.time() - start_time
    return first_token_time, total_time, full_response


def leer_system_instruction():
    """SYSTEM_INSTRUCTION de ai_data_chat.py, leído con ast para no importar Django."""
    with open(CHAT_TABLEIA, encoding="utf-8") as f:
        arbol = ast.parse(f.read())
    for nodo in arbol.body:
        if isinstance(nodo, ast.Assign) and any(
                getattr(t, "id", None) == "SYSTEM_INSTRUCTION" for t in nodo.targets):
            return ast.literal_eval(nodo.value)
    raise ValueError(f"No se encontró SYSTEM_INSTRUCTION en {CHAT_TABLEIA}")


def verdad_facilitadores(recintos=("Cibao Oriental (Nagua)", "Santiago")):
    """Tabla Markdown con las medias reales de satisfacción con los facilitadores por recinto."""
    with open(DATA_FILE, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        cabecera = next(reader)
        filas = list(reader)
    i_recinto = cabecera.index("Recinto:")
    # Bloque de 13 aspectos de facilitadores: de "Dominio" a "Cantidad de encuentros"
    inicio = next(i for i, c in enumerate(cabecera) if "[Dominio de los facilitadores" in c)
    fin = next(i for i, c in enumerate(cabecera) if "[Cantidad de encuentros virtuales" in c)
    columnas = range(inicio, fin + 1)
    tabla = "| Aspecto | " + " | ".join(recintos) + " |\n|---|" + "---|" * len(recintos) + "\n"
    for i in columnas:
        medias = []
        for recinto in recintos:
            valores = [ESCALA_SATISFACCION[fila[i].strip().lower()] for fila in filas
                       if fila[i_recinto] == recinto and fila[i].strip().lower() in ESCALA_SATISFACCION]
            medias.append(f"{statistics.mean(valores):.2f}" if valores else "n/a")
        aspecto = cabecera[i].split("[", 1)[-1].rstrip("]")
        tabla += f"| {aspecto} | " + " | ".join(medias) + " |\n"
    return tabla


def variantes(oa_file, ge_file, instrucciones):
    """(nombre, función(pregunta) -> (ttft, total, respuesta)) por cada modelo a comparar."""
    lista = []
    for modelo, effort in OPENAI_MODELOS:
        nombre = f"OpenAI {modelo}" + (f" ({effort})" if effort else "")
        lista.append((nombre, lambda q, m=modelo, e=effort: benchmark_openai(
            oa_file.id, model_name=m, verbose=False, pregunta=q, instrucciones=instrucciones,
            reasoning_effort=e)))
    for budget in THINKING_BUDGETS:
        nombre = f"Gemini thinking {'sin límite' if budget is None else budget}"
        lista.append((nombre, lambda q, b=budget: benchmark_gemini(
            ge_file, thinking_budget=b, verbose=False, pregunta=q, instrucciones=instrucciones)))
    return lista


def ejecutar_cualitativa():
    instrucciones = leer_system_instruction()
    oa_file = ge_file = None
    tiempos = []  # (pregunta, variante, totales, errores)
    respuestas = []  # (pregunta, variante, repetición, total, texto)

    try:
        print("--- Subiendo encuesta_facilitadores_2026.csv completo ---")
        oa_file, up_oa = upload_openai(DATA_FILE)
        ge_file, up_ge = upload_gemini(DATA_FILE)
        print(f"OpenAI {up_oa:.3f} s | Gemini {up_ge:.3f} s")

        for pregunta in PREGUNTAS_CUALITATIVAS:
            print(f"\n=== {pregunta} ===")
            for nombre, prueba in variantes(oa_file, ge_file, instrucciones):
                totales, errores = [], 0
                for i in range(REPETICIONES_CUALITATIVA):
                    start_time = time.time()
                    try:
                        _, total, respuesta = prueba(pregunta)
                        totales.append(total)
                        estado = ""
                    except Exception as e:
                        errores += 1
                        total = time.time() - start_time
                        respuesta = estado = f"ERROR: {e}"
                    respuestas.append((pregunta, nombre, i + 1, total, respuesta))
                    print(f"  {nombre:<28} #{i + 1} {total:7.2f} s {estado}")
                tiempos.append((pregunta, nombre, totales, errores))

        print(f"\n--- Resumen ({REPETICIONES_CUALITATIVA} repeticiones) ---")
        for n, pregunta in enumerate(PREGUNTAS_CUALITATIVAS, 1):
            print(f"\nP{n}: {pregunta}")
            print(f"  {'Variante':<28} {'mediana':>9} {'max':>9} {'errores':>8}")
            for q, nombre, totales, errores in tiempos:
                if q != pregunta:
                    continue
                med = f"{statistics.median(totales):.2f} s" if totales else "n/a"
                mx = f"{max(totales):.2f} s" if totales else "n/a"
                print(f"  {nombre:<28} {med:>9} {mx:>9} {errores:>8}")

        with open(RESULTADOS_MD, "w", encoding="utf-8") as f:
            f.write("# Benchmark cualitativo: respuestas\n")
            f.write("\nReferencia calculada en local para la pregunta 2 (escala 1-4, sin 'No aplica'):\n\n")
            f.write(verdad_facilitadores())
            for pregunta in PREGUNTAS_CUALITATIVAS:
                f.write(f"\n## {pregunta}\n")
                for q, nombre, rep, total, texto in respuestas:
                    if q == pregunta:
                        f.write(f"\n### {nombre} — repetición {rep} ({total:.2f} s)\n\n{texto.strip()}\n")
        print(f"\nRespuestas guardadas en {RESULTADOS_MD}")
    finally:
        # Borrar los ficheros de los servidores (contienen datos de estudiantes)
        if oa_file:
            openai_client.files.delete(oa_file.id)
        if ge_file:
            gemini_client.files.delete(name=ge_file.name)


def ejecutar_cuantitativa():
    print(f"CSV: {os.path.getsize(DATA_FILE) / 1024:.0f} KB")
    esperados = [(pregunta, funcion()) for pregunta, funcion in PREGUNTAS_CUANTITATIVAS]

    oa_file = ge_file = None
    resultados = []  # (pregunta, variante, ttfts, totales, aciertos, errores)
    respuestas = []  # (pregunta, variante, repetición, total, correcta, texto)

    try:
        print("\n--- Subiendo encuesta_facilitadores_2026.csv ---")
        oa_file, up_oa = upload_openai(DATA_FILE)
        ge_file, up_ge = upload_gemini(DATA_FILE)
        print(f"OpenAI {up_oa:.3f} s | Gemini {up_ge:.3f} s")

        lista_variantes = variantes(oa_file, ge_file, INSTRUCCIONES)
        for pregunta, esperado in esperados:
            print(f"\n=== {pregunta} ===")
            for nombre, prueba in lista_variantes:
                ttfts, totales, aciertos, errores = [], [], 0, 0
                for i in range(REPETICIONES):
                    start_time = time.time()
                    try:
                        ttft, total, respuesta = prueba(pregunta)
                        correcta = es_correcta(respuesta, esperado)
                        estado = "correcta" if correcta else "INCORRECTA"
                    except Exception as e:
                        errores += 1
                        ttft, total, correcta = None, time.time() - start_time, False
                        respuesta = estado = f"ERROR: {e}"
                    else:
                        aciertos += correcta
                        totales.append(total)
                        if ttft is not None:
                            ttfts.append(ttft)
                    respuestas.append((pregunta, nombre, i + 1, total, correcta, respuesta))
                    print(f"  {nombre:<28} #{i + 1} {total:7.2f} s {estado}")
                resultados.append((pregunta, nombre, ttfts, totales, aciertos, errores))

        print(f"\n--- Resumen ({REPETICIONES} repeticiones) ---")
        for n, (pregunta, esperado) in enumerate(esperados, 1):
            print(f"\nP{n}: {pregunta}")
            print(f"  Respuesta esperada: {texto_esperado(esperado)}")
            print(f"  {'Variante':<28} {'TTFT med':>9} {'Total med':>10} {'min':>8} {'max':>8} "
                  f"{'aciertos':>9} {'errores':>8}")
            for q, nombre, ttfts, totales, aciertos, errores in resultados:
                if q != pregunta:
                    continue
                ttft_txt = f"{statistics.median(ttfts):.2f} s" if ttfts else "n/a"
                if totales:
                    tiempos = (f"{statistics.median(totales):>8.2f} s {min(totales):>6.2f} s "
                               f"{max(totales):>6.2f} s")
                else:
                    tiempos = f"{'n/a':>10} {'n/a':>8} {'n/a':>8}"
                print(f"  {nombre:<28} {ttft_txt:>9} {tiempos} {aciertos:>5}/{REPETICIONES} {errores:>8}")

        total_intentos = len(esperados) * REPETICIONES
        print("\nAciertos totales:")
        for nombre, _ in lista_variantes:
            aciertos = sum(r[4] for r in resultados if r[1] == nombre)
            print(f"  {nombre:<28} {aciertos}/{total_intentos}")

        with open(RESULTADOS_CUANTITATIVO_MD, "w", encoding="utf-8") as f:
            f.write("# Benchmark cuantitativo: respuestas\n")
            for pregunta, esperado in esperados:
                f.write(f"\n## {pregunta}\n\nRespuesta esperada: {texto_esperado(esperado)}\n")
                for q, nombre, rep, total, correcta, texto in respuestas:
                    if q == pregunta:
                        f.write(f"\n### {nombre} — repetición {rep} ({total:.2f} s, "
                                f"{'correcta' if correcta else 'INCORRECTA'})\n\n{texto.strip()}\n")
        print(f"\nRespuestas guardadas en {RESULTADOS_CUANTITATIVO_MD}")
    finally:
        # Borrar los ficheros de los servidores (contienen datos de estudiantes)
        if oa_file:
            openai_client.files.delete(oa_file.id)
        if ge_file:
            gemini_client.files.delete(name=ge_file.name)


def ejecutar_respuestas():
    """Respuestas correctas de las preguntas cuantitativas, sin llamar a ninguna API."""
    with open(RESPUESTAS_ESPERADAS_MD, "w", encoding="utf-8") as f:
        f.write("# Respuestas esperadas (calculadas en local sobre encuesta_facilitadores_2026.csv)\n")
        for n, (pregunta, funcion) in enumerate(PREGUNTAS_CUANTITATIVAS, 1):
            esperado = funcion()
            print(f"\nP{n}. {pregunta}")
            f.write(f"\n## P{n}. {pregunta}\n\n| Dato | Valor |\n|---|---|\n")
            for dato, valor in esperado.items():
                print(f"  {dato}: {valor}")
                f.write(f"| {dato} | {valor} |\n")
    print(f"\nRespuestas guardadas en {RESPUESTAS_ESPERADAS_MD}")


if __name__ == "__main__":
    if "--respuestas" in sys.argv:
        ejecutar_respuestas()
    elif "--cualitativa" in sys.argv:
        ejecutar_cualitativa()
    else:
        ejecutar_cuantitativa()
