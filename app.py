import os
import re
import json
import uuid
import unicodedata
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from google import genai
from google.genai import types


# =========================================================
# CONFIGURAÇÃO DA PÁGINA
# =========================================================
st.set_page_config(
    page_title="Tradutor INCI - ANVISA + Cosing + IA",
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
# CONFIGURAÇÕES - SUBSTÂNCIAS PROIBIDAS / RDC 529
# =========================================================
URL_RDC_529 = (
    "https://anvisalegis.datalegis.net/action/ActionDatalegis.php"
    "?acao=abrirTextoAto"
    "&codTipo="
    "&cod_menu=1696"
    "&cod_modulo=134"
    "&desItem="
    "&desItemFim="
    "&numeroAto=00000529"
    "&orgao=RDC%2FDC%2FANVISA%2FMS"
    "&pesquisa=true"
    "&seqAto=000"
    "&tipo=RDC"
    "&valorAno=2021"
)


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

if "erro_proibidos" not in st.session_state:
    st.session_state.erro_proibidos = None


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
# ANVISA - LISTA DE SUBSTÂNCIAS PROIBIDAS / RDC 529
# =========================================================
def normalizar_regulatorio(texto):
    texto = str(texto or "").replace("\xa0", " ").strip().upper()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(
        caractere
        for caractere in texto
        if not unicodedata.combining(caractere)
    )
    texto = re.sub(r"\s+", " ", texto)
    return texto


def extrair_cas(texto):
    return set(
        re.findall(
            r"(?<!\d)\d{2,7}-\d{2}-\d(?!\d)",
            str(texto or ""),
        )
    )


def encontrar_tabela_proibidos(soup):
    for tabela in soup.find_all("table"):
        texto = normalizar_regulatorio(
            tabela.get_text(" ", strip=True)
        )

        if (
            "SUBSTANCIA" in texto
            and "CAS" in texto
            and "EC" in texto
        ):
            return tabela

    return None


@st.cache_data(ttl=21600, show_spinner=False)
def carregar_lista_proibidos():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/151.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    resposta = requests.get(
        URL_RDC_529,
        headers=headers,
        timeout=60,
    )
    resposta.raise_for_status()

    resposta.encoding = resposta.apparent_encoding or "utf-8"

    soup = BeautifulSoup(resposta.text, "html.parser")
    tabela = encontrar_tabela_proibidos(soup)

    if tabela is None:
        raise RuntimeError(
            "A tabela de substâncias proibidas da RDC 529 "
            "não foi encontrada."
        )

    registros = []

    for tr in tabela.find_all("tr"):
        celulas = [
            td.get_text(" ", strip=True)
            .replace("\xa0", " ")
            .strip()
            for td in tr.find_all(["th", "td"], recursive=False)
        ]

        # A página possui uma célula vazia extra no fim de muitas linhas.
        while celulas and not celulas[-1]:
            celulas.pop()

        if len(celulas) < 5:
            continue

        numero = celulas[0]
        numero_ue = celulas[1]
        substancia = celulas[2]
        cas = celulas[3]
        ec = celulas[4]

        linha_completa = " | ".join(celulas)
        linha_norm = normalizar_regulatorio(linha_completa)

        if (
            "SUBSTANCIA" in normalizar_regulatorio(substancia)
            and normalizar_regulatorio(cas) == "CAS"
        ):
            continue

        if "REDACOES ANTERIORES" in linha_norm:
            continue

        if "EXCLUID" in linha_norm:
            continue

        registros.append(
            {
                "numero": numero,
                "numero_ue": numero_ue,
                "substancia": substancia,
                "cas": cas,
                "ec": ec,
                "cas_set": extrair_cas(cas),
                "texto_busca": normalizar_regulatorio(substancia),
            }
        )

    if not registros:
        raise RuntimeError(
            "A lista da RDC 529 foi carregada, mas nenhum registro "
            "foi identificado."
        )

    return registros


def consultar_proibido(ingrediente, cas="", lista_proibidos=None):
    if not lista_proibidos:
        return None

    ingrediente_norm = normalizar_regulatorio(ingrediente)
    cas_informados = extrair_cas(cas)

    # O CAS é o critério prioritário por ser mais preciso.
    if cas_informados:
        for item in lista_proibidos:
            if cas_informados.intersection(item["cas_set"]):
                return item

    # Também verifica o nome/INCI dentro da descrição da RDC.
    if len(ingrediente_norm) >= 5:
        for item in lista_proibidos:
            if ingrediente_norm in item["texto_busca"]:
                return item

    return None


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
st.title("🧴 Tradutor INCI - ANVISA + Cosing + IA")

st.write(
    "A ferramenta consulta a base de tradução da **ANVISA** e também verifica "
    "a lista de substâncias proibidas do Anexo da **RDC nº 529/2021**. "
    "Quando o ingrediente não é encontrado na base de tradução, consulta o "
    "**Cosing da Comissão Europeia** para obter contexto técnico. A IA é usada "
    "apenas para sugerir a tradução dos ingredientes sem tradução ANVISA."
)

st.info(
    "Traduções vindas da ANVISA são identificadas separadamente. "
    "Alertas de proibição são apresentados com base na lista consultada da RDC nº 529/2021. "
    "Resultados gerados com Cosing + IA ou somente IA são sugestões e devem ser revisados."
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

            lista_proibidos = []
            erro_proibidos = None

            try:
                status_area.info(
                    "Atualizando a lista de substâncias proibidas da ANVISA..."
                )
                lista_proibidos = carregar_lista_proibidos()
            except Exception as erro:
                erro_proibidos = str(erro)

            st.session_state.erro_proibidos = erro_proibidos

            registros = []

            for ingrediente in ingredientes:
                status_area.info(f"Consultando ANVISA: {ingrediente}")
                anvisa = consultar_inci(ingrediente)
                cosing = None

                if not anvisa:
                    status_area.info(f"Consultando Cosing: {ingrediente}")
                    cosing = consultar_cosing(ingrediente)

                cas_referencia = ""
                if anvisa:
                    cas_referencia = anvisa.get("cas", "")
                elif cosing:
                    cas_referencia = cosing.get("cas", "")

                proibido = consultar_proibido(
                    ingrediente,
                    cas_referencia,
                    lista_proibidos,
                )

                registros.append(
                    {
                        "ingrediente": ingrediente,
                        "anvisa": anvisa,
                        "cosing": cosing,
                        "proibido": proibido,
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
    qtd_proibidos = sum(
        1
        for item in registros
        if item.get("proibido")
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Ingredientes", total)
    c2.metric("ANVISA", qtd_anvisa)
    c3.metric("Cosing", qtd_cosing)
    c4.metric("🚫 Proibidos", qtd_proibidos)
    c5.metric("Sem referência", qtd_sem_base)

    st.divider()

    # -----------------------------------------------------
    # ALERTA - SUBSTÂNCIAS PROIBIDAS
    # -----------------------------------------------------
    erro_proibidos = st.session_state.erro_proibidos
    itens_proibidos = [
        item
        for item in registros
        if item.get("proibido")
    ]

    if erro_proibidos:
        st.warning(
            "⚠️ Não foi possível verificar a lista de substâncias proibidas "
            "da ANVISA nesta consulta. As traduções continuam disponíveis, "
            "mas a checagem regulatória deve ser refeita."
        )

    elif itens_proibidos:
        st.error(
            f"🚫 ATENÇÃO: {len(itens_proibidos)} ingrediente(s) da composição "
            "foram identificados na lista de substâncias proibidas da "
            "RDC nº 529/2021."
        )

        linhas_proibidos = []

        for item in itens_proibidos:
            proibido = item["proibido"]
            anvisa = item.get("anvisa")
            cosing = item.get("cosing")

            cas_identificado = ""
            if anvisa:
                cas_identificado = anvisa.get("cas", "")
            elif cosing:
                cas_identificado = cosing.get("cas", "")

            linhas_proibidos.append(
                {
                    "INCI consultado": item["ingrediente"],
                    "CAS identificado": cas_identificado,
                    "Substância na RDC": proibido.get("substancia", ""),
                    "CAS na RDC": proibido.get("cas", ""),
                    "Nº": proibido.get("numero", ""),
                    "Nº UE": proibido.get("numero_ue", ""),
                    "Nº EC": proibido.get("ec", ""),
                    "Referência": "RDC nº 529/2021",
                }
            )

        st.dataframe(
            pd.DataFrame(linhas_proibidos),
            use_container_width=True,
            hide_index=True,
        )

        st.caption(
            "A identificação automática compara principalmente o número CAS "
            "e, quando possível, o nome do ingrediente com o Anexo da RDC nº 529/2021."
        )

    else:
        st.success(
            "✅ Nenhum ingrediente desta consulta foi identificado na lista "
            "do Anexo da RDC nº 529/2021."
        )

    st.caption(
        "A checagem acima cobre a lista do Anexo da RDC nº 529/2021. "
        "Ela não substitui uma avaliação regulatória completa das demais regras "
        "da norma, incluindo as condições relacionadas a IARC/CMR."
    )

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
                "Cosing ID": cosing.get("cosing_id", ""),
                "Funções": ", ".join(cosing.get("funcoes", [])),
                "Status": cosing.get("status", ""),
                "Descrição": cosing.get("descricao", ""),
            }
        )

    if linhas_cosing:
        st.subheader("Não encontrados na ANVISA, mas encontrados no Cosing")
        st.dataframe(
            pd.DataFrame(linhas_cosing),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "O Cosing está sendo usado como referência técnica de identificação. "
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
        st.subheader("⚠️ Não encontrados na ANVISA nem no Cosing")
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
                f"**{qtd_com_cosing}** ingrediente(s) possuem referência no Cosing. "
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
                    "Apoio": "Cosing + IA" if item.get("cosing") else "Somente IA",
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
            "Quando indicado 'Cosing + IA', o Cosing foi utilizado apenas como "
            "referência técnica para identificar o ingrediente."
        )

        st.subheader("✨ Resultado final da composição")
        st.caption(
            "Combina as traduções da ANVISA com sugestões da IA quando necessário."
        )

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
                        f"{traducao_ia} [Cosing + IA]"
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

# =========================================================
# RODAPÉ REGULATÓRIO
# =========================================================
st.markdown("---")
st.markdown("#### Referências regulatórias")
st.caption(
    "RDC nº 529/2021 — alterada pela RDC nº 995/2025 "
    "e pela RDC nº 1.030/2026."
)
