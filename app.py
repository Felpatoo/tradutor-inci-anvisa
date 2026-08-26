import os
import re
import json
import uuid
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
from google import genai
from google.genai import types


# =========================================================
# CONFIGURAÇÃO DA PÁGINA
# =========================================================
st.set_page_config(
    page_title="Tradutor INCI - ANVISA + CosIng + IA",
    page_icon="🧴",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Oculta totalmente a barra lateral e o botão de abri-la.
st.markdown(
    """
    <style>
        [data-testid="stSidebar"] {display: none !important;}
        [data-testid="collapsedControl"] {display: none !important;}
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# CONFIGURAÇÕES - ANVISA / POWER BI
# =========================================================
ENDPOINT = (
    "https://wabi-brazil-south-api.analysis.windows.net/"
    "public/reports/querydata?synchronous=true"
)
RESOURCE_KEY = "17503ee4-c6cd-4d1b-9381-65d8a36512ae"
DATASET_ID = "8f6a5ce3-dd3b-4529-aa4a-e77bbb6e0e68"
REPORT_ID = "f6d9dd77-7aae-4e47-913e-54624d5169a3"
MODEL_ID = 6282246
VISUAL_ID = "ca3280fdac5b00a888db"


# =========================================================
# CONFIGURAÇÕES - COSING
# =========================================================
COSING_URL = "https://webgate.ec.europa.eu/es/search-api/rest/search"

# Esta chave é a utilizada pelo próprio front-end público do CosIng.
# Se no futuro ela mudar, você pode sobrescrevê-la com COSING_API_KEY
# nos Secrets do Streamlit sem alterar o código.
COSING_API_KEY_PUBLICA = "285a77fd-1257-4271-8507-f0c6b2961203"


# =========================================================
# CONFIGURAÇÕES - GEMINI
# =========================================================
MODELO_IA = "gemini-3.5-flash-lite"


def ler_config(nome, padrao=""):
    """Lê primeiro o Streamlit Secrets e depois variável de ambiente."""
    try:
        valor = st.secrets[nome]
        if valor:
            return str(valor).strip()
    except Exception:
        pass

    return os.getenv(nome, padrao).strip()


GEMINI_API_KEY = ler_config("GEMINI_API_KEY")
COSING_API_KEY = ler_config("COSING_API_KEY", COSING_API_KEY_PUBLICA)


# =========================================================
# ESTADO DO STREAMLIT
# =========================================================
if "resultado_consulta" not in st.session_state:
    st.session_state.resultado_consulta = None

if "resultado_ia" not in st.session_state:
    st.session_state.resultado_ia = None


# =========================================================
# FUNÇÕES AUXILIARES
# =========================================================
def normalizar_texto(texto):
    if texto is None:
        return ""

    texto = str(texto).strip().upper()
    texto = re.sub(r"\s+", " ", texto)
    texto = re.sub(r"\s*,\s*", ",", texto)
    return texto


def primeiro(lista, padrao=""):
    if isinstance(lista, list) and lista:
        return lista[0]
    return padrao


def converter_data(valor):
    if valor in (None, ""):
        return ""

    try:
        if isinstance(valor, (int, float)):
            data = datetime.fromtimestamp(
                valor / 1000,
                tz=timezone.utc,
            )
            return data.strftime("%d/%m/%Y")
    except Exception:
        pass

    return str(valor)


# =========================================================
# ANVISA - POWER BI
# =========================================================
def criar_payload(inci):
    inci_seguro = normalizar_texto(inci).replace("'", "''")

    def coluna(propriedade, nome=None):
        return {
            "Column": {
                "Expression": {
                    "SourceRef": {
                        "Source": "p"
                    }
                },
                "Property": propriedade,
            },
            "Name": nome or f"Prod 1 a 5.{propriedade}",
        }

    query = {
        "Version": 2,
        "From": [
            {
                "Name": "p",
                "Entity": "Prod 1 a 5",
                "Type": 0,
            }
        ],
        "Select": [
            coluna("SITUAÇÃO ATUAL"),
            coluna("INCI NAME"),
            coluna("TRADUÇÃO ANVISA"),
            coluna("Nº CAS"),
            coluna("FIM DE VIGêNCIA"),
            coluna("INÍCIO DE VIGÊNCIA"),
        ],
        "Where": [
            {
                "Condition": {
                    "Comparison": {
                        "ComparisonKind": 0,
                        "Left": {
                            "Column": {
                                "Expression": {
                                    "SourceRef": {
                                        "Source": "p"
                                    }
                                },
                                "Property": "INCI NAME",
                            }
                        },
                        "Right": {
                            "Literal": {
                                "Value": f"'{inci_seguro}'"
                            }
                        },
                    }
                }
            }
        ],
    }

    command = {
        "SemanticQueryDataShapeCommand": {
            "Query": query,
            "Binding": {
                "DataReduction": {
                    "DataVolume": 3,
                    "Primary": {
                        "Window": {
                            "Count": 50
                        }
                    },
                },
                "Primary": {
                    "Groupings": [
                        {
                            "Projections": [0, 1, 2, 3, 4, 5],
                            "Subtotal": 1,
                        }
                    ]
                },
                "Version": 1,
            },
            "ExecutionMetricsKind": 1,
        }
    }

    return {
        "version": "1.0.0",
        "queries": [
            {
                "Query": {
                    "Commands": [command]
                },
                "ApplicationContext": {
                    "DatasetId": DATASET_ID,
                    "Sources": [
                        {
                            "ReportId": REPORT_ID,
                            "VisualId": VISUAL_ID,
                        }
                    ],
                },
            }
        ],
        "cancelQueries": [],
        "modelId": MODEL_ID,
    }


@st.cache_data(ttl=21600, show_spinner=False)
def pesquisar(inci):
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://app.powerbi.com",
        "ActivityId": str(uuid.uuid4()),
        "RequestId": str(uuid.uuid4()),
        "X-PowerBI-ResourceKey": RESOURCE_KEY,
    }

    resposta = requests.post(
        ENDPOINT,
        headers=headers,
        json=criar_payload(inci),
        timeout=30,
    )
    resposta.raise_for_status()
    return resposta.json()


def decodificar_linhas_powerbi(resposta):
    try:
        ds = (
            resposta["results"][0]
            ["result"]["data"]["dsr"]["DS"][0]
        )
    except Exception:
        return []

    value_dicts = ds.get("ValueDicts", {})
    linhas_comprimidas = []

    for ph in ds.get("PH", []):
        linhas_comprimidas.extend(ph.get("DM0", []))

    if not linhas_comprimidas:
        return []

    schema = None
    anterior = []
    saida = []

    for linha in linhas_comprimidas:
        if linha.get("S"):
            schema = linha["S"]

        if not schema:
            continue

        valores_c = iter(linha.get("C", []))
        mascara_nulos = int(linha.get("Ø", 0) or 0)
        mascara_repetidos = int(linha.get("R", 0) or 0)
        valores = []

        for indice, campo in enumerate(schema):
            bit = 1 << indice

            if mascara_repetidos & bit:
                valor = anterior[indice] if indice < len(anterior) else None
            elif mascara_nulos & bit:
                valor = None
            else:
                try:
                    bruto = next(valores_c)
                except StopIteration:
                    bruto = None

                dicionario = campo.get("DN")
                if (
                    dicionario
                    and isinstance(bruto, int)
                    and dicionario in value_dicts
                    and 0 <= bruto < len(value_dicts[dicionario])
                ):
                    valor = value_dicts[dicionario][bruto]
                else:
                    valor = bruto

            valores.append(valor)

        anterior = valores
        saida.append(valores)

    return saida


def extrair_resultado(resposta):
    linhas = decodificar_linhas_powerbi(resposta)

    if not linhas:
        return None

    linha = linhas[0]

    while len(linha) < 6:
        linha.append(None)

    return {
        "situacao": linha[0] or "",
        "inci": linha[1] or "",
        "traducao": linha[2] or "",
        "cas": linha[3] or "",
        "fim_vigencia": converter_data(linha[4]),
        "inicio_vigencia": converter_data(linha[5]),
    }


@st.cache_data(ttl=21600, show_spinner=False)
def consultar_inci(inci):
    inci_normalizado = normalizar_texto(inci)

    if not inci_normalizado:
        return None

    try:
        resultado = extrair_resultado(pesquisar(inci_normalizado))
    except Exception:
        return None

    if not resultado:
        return None

    if normalizar_texto(resultado.get("inci")) != inci_normalizado:
        return None

    return resultado


# =========================================================
# COSING - COMISSÃO EUROPEIA
# =========================================================
@st.cache_data(ttl=21600, show_spinner=False)
def consultar_cosing(inci):
    inci_normalizado = normalizar_texto(inci)

    if not inci_normalizado or not COSING_API_KEY:
        return None

    params = {
        "apiKey": COSING_API_KEY,
        "text": "*",
        "pageSize": 100,
        "pageNumber": 1,
    }

    query = {
        "bool": {
            "must": [
                {
                    "term": {
                        "inciName": inci_normalizado
                    }
                }
            ]
        }
    }

    files = {
        "query": (
            "blob",
            json.dumps(query),
            "application/json",
        )
    }

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://ec.europa.eu",
        "Referer": "https://ec.europa.eu/",
        "Cache-Control": "No-Cache",
    }

    try:
        resposta = requests.post(
            COSING_URL,
            params=params,
            files=files,
            headers=headers,
            timeout=30,
        )
        resposta.raise_for_status()
        dados = resposta.json()
    except Exception:
        return None

    for resultado in dados.get("results", []):
        metadata = resultado.get("metadata", {})
        inci_encontrado = primeiro(metadata.get("inciName"))

        if normalizar_texto(inci_encontrado) != inci_normalizado:
            continue

        substance_id = primeiro(metadata.get("substanceId"))

        return {
            "inci": inci_encontrado,
            "cas": primeiro(metadata.get("casNo")),
            "ec": primeiro(metadata.get("ecNo")),
            "cosing_id": substance_id,
            "descricao": primeiro(metadata.get("chemicalDescription")),
            "funcoes": metadata.get("functionName", []) or [],
            "status": primeiro(metadata.get("status")),
            "url": (
                "https://ec.europa.eu/growth/tools-databases/"
                f"cosing/details/{substance_id}"
                if substance_id
                else ""
            ),
        }

    return None


# =========================================================
# SEPARAÇÃO INTELIGENTE DA COMPOSIÇÃO
# =========================================================
def existe_em_base(inci):
    if consultar_inci(inci):
        return True

    if consultar_cosing(inci):
        return True

    return False


def testar_partes(partes):
    # Tenta primeiro juntando sem espaço após a vírgula, que é comum
    # em nomes químicos: 1,2-HEXANEDIOL.
    candidato = ",".join(partes).strip()
    if existe_em_base(candidato):
        return candidato

    # Algumas nomenclaturas podem ter espaço depois da vírgula.
    candidato_com_espaco = ", ".join(partes).strip()
    if candidato_com_espaco != candidato and existe_em_base(candidato_com_espaco):
        return candidato_com_espaco

    return None


def separar_linha_inteligente(linha, status_area=None):
    linha = linha.strip()
    if not linha:
        return []

    partes = [parte.strip() for parte in linha.split(",")]
    partes = [parte for parte in partes if parte]

    resultado = []
    i = 0

    while i < len(partes):
        parte_atual = partes[i]

        if status_area is not None:
            status_area.caption(f"Identificando: {parte_atual}")

        if existe_em_base(parte_atual):
            resultado.append(parte_atual)
            i += 1
            continue

        encontrado = None
        quantidade_usada = 1
        limite = min(12, len(partes) - i)

        # Começa em 2 porque a parte isolada já foi testada.
        for quantidade in range(2, limite + 1):
            candidato = testar_partes(partes[i:i + quantidade])
            if candidato:
                encontrado = candidato
                quantidade_usada = quantidade
                break

        if encontrado:
            resultado.append(encontrado)
            i += quantidade_usada
        else:
            # Não foi reconhecido em nenhuma das bases. Mantemos como veio
            # para que a IA ainda possa analisar depois.
            resultado.append(parte_atual)
            i += 1

    return resultado


def processar_composicao(texto, status_area=None):
    blocos = re.split(r"[;\n]+", texto or "")
    ingredientes = []

    for bloco in blocos:
        bloco = bloco.strip()
        if not bloco:
            continue

        ingredientes.extend(
            separar_linha_inteligente(bloco, status_area)
        )

    # Remove duplicados preservando a ordem.
    vistos = set()
    unicos = []

    for ingrediente in ingredientes:
        chave = normalizar_texto(ingrediente)
        if chave and chave not in vistos:
            vistos.add(chave)
            unicos.append(ingrediente.strip())

    return unicos


# =========================================================
# GEMINI
# =========================================================
def limpar_json_ia(texto):
    texto = (texto or "").strip()
    texto = re.sub(r"^```(?:json)?\s*", "", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\s*```$", "", texto)
    return texto.strip()


def analisar_com_ia(itens, api_key):
    client = genai.Client(api_key=api_key)

    referencias = []

    for item in itens:
        cosing = item.get("cosing")

        referencia = {
            "ingrediente": item["ingrediente"],
            "encontrado_no_cosing": bool(cosing),
        }

        if cosing:
            referencia["cosing"] = {
                "inci": cosing.get("inci"),
                "cas": cosing.get("cas"),
                "ec": cosing.get("ec"),
                "cosing_id": cosing.get("cosing_id"),
                "descricao": cosing.get("descricao"),
                "funcoes": cosing.get("funcoes", []),
                "status": cosing.get("status"),
            }

        referencias.append(referencia)

    prompt = f"""
Você auxilia na tradução técnica de nomes INCI de ingredientes cosméticos
para português do Brasil.

REGRAS IMPORTANTES:
1. Os ingredientes abaixo NÃO foram encontrados na base de tradução da ANVISA.
2. Quando houver dados do CosIng, use esses dados da Comissão Europeia como
   referência técnica para entender qual ingrediente é, mas NÃO diga que a
   tradução sugerida é oficial da ANVISA ou oficial do CosIng.
3. O CosIng serve como referência de identificação e contexto; a tradução em
   português continua sendo uma sugestão da IA.
4. Não invente número CAS, EC, função, descrição ou qualquer dado regulatório.
5. Preserve números, prefixos, hífens, estruturas químicas e nomes botânicos.
6. Se não houver tradução técnica segura para português, retorne null em
   traducao_sugerida.
7. confiança deve ser apenas: "alta", "media" ou "baixa".
8. Responda SOMENTE em JSON válido, sem markdown.

Formato obrigatório:
{{
  "resultados": [
    {{
      "ingrediente": "NOME INCI ORIGINAL",
      "traducao_sugerida": "TRADUÇÃO EM PORTUGUÊS" ou null,
      "confianca": "alta|media|baixa",
      "observacao": "explicação curta"
    }}
  ]
}}

DADOS PARA ANÁLISE:
{json.dumps(referencias, ensure_ascii=False, indent=2)}
""".strip()

    resposta = client.models.generate_content(
        model=MODELO_IA,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json"
        ),
    )

    dados = json.loads(limpar_json_ia(resposta.text))
    resultados = dados.get("resultados", [])

    mapa = {}
    for resultado in resultados:
        chave = normalizar_texto(resultado.get("ingrediente"))
        if chave:
            mapa[chave] = resultado

    return mapa


# =========================================================
# INTERFACE
# =========================================================
st.title("🧴 Tradutor INCI - ANVISA + CosIng + IA")

st.write(
    "A ferramenta consulta primeiro a base de tradução da **ANVISA**. "
    "Quando o ingrediente não é encontrado, consulta o **CosIng da Comissão Europeia** "
    "para obter contexto técnico. A IA é usada apenas para sugerir a tradução dos "
    "ingredientes sem tradução ANVISA."
)

st.info(
    "Traduções vindas da ANVISA são identificadas separadamente. "
    "Resultados gerados com CosIng + IA ou somente IA são sugestões e devem ser revisados."
)

texto = st.text_area(
    "Cole a composição INCI:",
    height=160,
    placeholder="Ex.: AQUA, GLYCERIN, PARFUM, 1,2-HEXANEDIOL",
)

if st.button("🔎 Consultar bases", type="primary", use_container_width=True):
    st.session_state.resultado_ia = None

    if not texto.strip():
        st.warning("Cole uma composição antes de consultar.")
    else:
        status_area = st.empty()

        try:
            status_area.info("Identificando os ingredientes...")
            ingredientes = processar_composicao(texto, status_area)

            registros = []

            for ingrediente in ingredientes:
                status_area.info(f"Consultando ANVISA: {ingrediente}")
                anvisa = consultar_inci(ingrediente)
                cosing = None

                if not anvisa:
                    status_area.info(f"Consultando CosIng: {ingrediente}")
                    cosing = consultar_cosing(ingrediente)

                registros.append(
                    {
                        "ingrediente": ingrediente,
                        "anvisa": anvisa,
                        "cosing": cosing,
                    }
                )

            st.session_state.resultado_consulta = registros
            status_area.empty()

        except Exception as erro:
            status_area.empty()
            st.error(f"Erro ao consultar as bases: {erro}")


registros = st.session_state.resultado_consulta

if registros:
    total = len(registros)
    qtd_anvisa = sum(1 for item in registros if item.get("anvisa"))
    qtd_cosing = sum(
        1
        for item in registros
        if not item.get("anvisa") and item.get("cosing")
    )
    qtd_sem_base = sum(
        1
        for item in registros
        if not item.get("anvisa") and not item.get("cosing")
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ingredientes", total)
    c2.metric("ANVISA", qtd_anvisa)
    c3.metric("CosIng", qtd_cosing)
    c4.metric("Sem referência", qtd_sem_base)

    st.divider()

    # -----------------------------------------------------
    # RESULTADOS ANVISA
    # -----------------------------------------------------
    linhas_anvisa = []

    for item in registros:
        anvisa = item.get("anvisa")
        if not anvisa:
            continue

        linhas_anvisa.append(
            {
                "INCI": anvisa.get("inci", ""),
                "Tradução ANVISA": anvisa.get("traducao", ""),
                "CAS": anvisa.get("cas", ""),
                "Situação": anvisa.get("situacao", ""),
                "Início de vigência": anvisa.get("inicio_vigencia", ""),
                "Fim de vigência": anvisa.get("fim_vigencia", ""),
            }
        )

    if linhas_anvisa:
        st.subheader("🇧🇷 Encontrados na ANVISA")
        st.dataframe(
            pd.DataFrame(linhas_anvisa),
            use_container_width=True,
            hide_index=True,
        )

    # -----------------------------------------------------
    # RESULTADOS COSING
    # -----------------------------------------------------
    linhas_cosing = []

    for item in registros:
        if item.get("anvisa") or not item.get("cosing"):
            continue

        cosing = item["cosing"]

        linhas_cosing.append(
            {
                "INCI": cosing.get("inci", item["ingrediente"]),
                "CAS": cosing.get("cas", ""),
                "EC": cosing.get("ec", ""),
                "CosIng ID": cosing.get("cosing_id", ""),
                "Funções": ", ".join(cosing.get("funcoes", [])),
                "Status": cosing.get("status", ""),
                "Descrição": cosing.get("descricao", ""),
            }
        )

    if linhas_cosing:
        st.subheader("🇪🇺 Não encontrados na ANVISA, mas encontrados no CosIng")
        st.dataframe(
            pd.DataFrame(linhas_cosing),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "O CosIng está sendo usado como referência técnica de identificação. "
            "Ele não fornece aqui uma tradução oficial da ANVISA."
        )

    # -----------------------------------------------------
    # SEM REFERÊNCIA NAS DUAS BASES
    # -----------------------------------------------------
    sem_referencia = [
        item["ingrediente"]
        for item in registros
        if not item.get("anvisa") and not item.get("cosing")
    ]

    if sem_referencia:
        st.subheader("⚠️ Não encontrados na ANVISA nem no CosIng")
        for ingrediente in sem_referencia:
            st.write(f"• {ingrediente}")

    # -----------------------------------------------------
    # COMPOSIÇÃO USANDO SOMENTE A ANVISA
    # -----------------------------------------------------
    st.subheader("📝 Composição com traduções ANVISA")

    composicao_anvisa = []

    for item in registros:
        if item.get("anvisa") and item["anvisa"].get("traducao"):
            composicao_anvisa.append(item["anvisa"]["traducao"])
        else:
            composicao_anvisa.append(
                f"[SEM TRADUÇÃO ANVISA: {item['ingrediente']}]"
            )

    st.code(", ".join(composicao_anvisa), language=None)

    # Todos que não têm tradução ANVISA podem ser analisados pela IA.
    pendentes_ia = [
        item
        for item in registros
        if not item.get("anvisa")
    ]

    if pendentes_ia:
        st.divider()
        st.subheader("🤖 Sugestões para os itens sem tradução ANVISA")

        qtd_com_cosing = sum(1 for item in pendentes_ia if item.get("cosing"))

        if qtd_com_cosing:
            st.write(
                f"**{qtd_com_cosing}** ingrediente(s) possuem referência no CosIng. "
                "Esses dados serão enviados ao Gemini como contexto técnico."
            )

        if st.button(
            "🤖 Gerar sugestões em português",
            use_container_width=True,
        ):
            if not GEMINI_API_KEY:
                st.error(
                    "A chave do Gemini não está configurada no servidor. "
                    "Adicione GEMINI_API_KEY nos Secrets do Streamlit."
                )
            else:
                try:
                    with st.spinner("Analisando os ingredientes..."):
                        st.session_state.resultado_ia = analisar_com_ia(
                            pendentes_ia,
                            GEMINI_API_KEY,
                        )
                except Exception as erro:
                    st.error(f"Erro ao consultar o Gemini: {erro}")

    # -----------------------------------------------------
    # RESULTADOS DA IA
    # -----------------------------------------------------
    mapa_ia = st.session_state.resultado_ia

    if mapa_ia:
        st.divider()
        st.subheader("📌 Sugestões de tradução")

        linhas_ia = []

        for item in registros:
            if item.get("anvisa"):
                continue

            ingrediente = item["ingrediente"]
            chave = normalizar_texto(ingrediente)
            resultado = mapa_ia.get(chave, {})
            traducao = resultado.get("traducao_sugerida")

            linhas_ia.append(
                {
                    "Ingrediente": ingrediente,
                    "Sugestão em português": traducao or "SEM TRADUÇÃO SEGURA",
                    "Apoio": "CosIng + IA" if item.get("cosing") else "Somente IA",
                    "Confiança": resultado.get("confianca", ""),
                    "Observação": resultado.get("observacao", ""),
                }
            )

        st.dataframe(
            pd.DataFrame(linhas_ia),
            use_container_width=True,
            hide_index=True,
        )

        st.warning(
            "As sugestões da IA não são traduções oficiais da ANVISA. "
            "Quando indicado 'CosIng + IA', o CosIng foi utilizado apenas como "
            "referência técnica para identificar o ingrediente."
        )

        st.subheader("✨ Composição assistida")

        composicao_final = []

        for item in registros:
            ingrediente = item["ingrediente"]
            anvisa = item.get("anvisa")

            if anvisa and anvisa.get("traducao"):
                composicao_final.append(anvisa["traducao"])
                continue

            resultado_ia = mapa_ia.get(
                normalizar_texto(ingrediente),
                {},
            )
            traducao_ia = resultado_ia.get("traducao_sugerida")

            if traducao_ia:
                if item.get("cosing"):
                    composicao_final.append(
                        f"{traducao_ia} [COSING + IA]"
                    )
                else:
                    composicao_final.append(
                        f"{traducao_ia} [IA]"
                    )
            else:
                composicao_final.append(
                    f"{ingrediente} [SEM TRADUÇÃO]"
                )

        st.code(", ".join(composicao_final), language=None)
