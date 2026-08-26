import re
import json
import uuid
import requests
import pandas as pd
import streamlit as st

from datetime import datetime, timezone
from google import genai
from google.genai import types


# ==========================================================
# CONFIGURAÇÃO DA PÁGINA
# ==========================================================

st.set_page_config(
    page_title="Tradutor INCI - ANVISA + IA",
    page_icon="🧴",
    layout="wide"
)


# ==========================================================
# CONFIGURAÇÕES DO POWER BI
# ==========================================================

ENDPOINT = (
    "https://wabi-brazil-south-api.analysis.windows.net/"
    "public/reports/querydata?synchronous=true"
)

RESOURCE_KEY = "17503ee4-c6cd-4d1b-9381-65d8a36512ae"

DATASET_ID = "8f6a5ce3-dd3b-4529-aa4a-e77bbb6e0e68"

REPORT_ID = "f6d9dd77-7aae-4e47-913e-54624d5169a3"

MODEL_ID = 6282246

VISUAL_ID = "ca3280fdac5b00a888db"


# Modelo usado apenas para ingredientes que a ANVISA não encontrou
MODELO_IA = "gemini-3.5-flash-lite"


# ==========================================================
# ESTADO DA PÁGINA
# ==========================================================

if "resultado_consulta" not in st.session_state:
    st.session_state.resultado_consulta = None

if "resultado_ia" not in st.session_state:
    st.session_state.resultado_ia = None


# ==========================================================
# NORMALIZA TEXTO
# ==========================================================

def normalizar_texto(texto):

    texto = str(texto).strip().upper()

    texto = re.sub(
        r"\s+",
        " ",
        texto
    )

    texto = re.sub(
        r"\s*,\s*",
        ",",
        texto
    )

    return texto


# ==========================================================
# PAYLOAD POWER BI
# ==========================================================

def criar_payload(inci):

    inci_seguro = (
        inci
        .upper()
        .replace("'", "''")
    )

    def coluna(nome):

        return {
            "Column": {
                "Expression": {
                    "SourceRef": {
                        "Source": "p"
                    }
                },
                "Property": nome
            },

            "Name":
                f"Prod 1 a 5.{nome}"
        }

    query = {

        "Version": 2,

        "From": [
            {
                "Name": "p",
                "Entity": "Prod 1 a 5",
                "Type": 0
            }
        ],

        "Select": [

            coluna(
                "SITUAÇÃO ATUAL"
            ),

            coluna(
                "INCI NAME"
            ),

            coluna(
                "TRADUÇÃO ANVISA"
            ),

            coluna(
                "Nº CAS"
            ),

            coluna(
                "FIM DE VIGêNCIA"
            ),

            coluna(
                "INÍCIO DE VIGÊNCIA"
            )
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

                                "Property":
                                    "INCI NAME"
                            }
                        },

                        "Right": {

                            "Literal": {

                                "Value":
                                    f"'{inci_seguro}'"
                            }
                        }
                    }
                }
            }
        ]
    }

    command = {

        "SemanticQueryDataShapeCommand": {

            "Query": query,

            "Binding": {

                "Primary": {

                    "Groupings": [
                        {
                            "Projections": [
                                0,
                                1,
                                2,
                                3,
                                4,
                                5
                            ]
                        }
                    ]
                },

                "DataReduction": {

                    "DataVolume": 3,

                    "Primary": {

                        "Window": {
                            "Count": 50
                        }
                    }
                },

                "Version": 1
            },

            "ExecutionMetricsKind": 1
        }
    }

    return {

        "version": "1.0.0",

        "queries": [
            {

                "Query": {

                    "Commands": [
                        command
                    ]
                },

                "CacheKey": "",

                "QueryId": "",

                "ApplicationContext": {

                    "DatasetId":
                        DATASET_ID,

                    "Sources": [
                        {
                            "ReportId":
                                REPORT_ID,

                            "VisualId":
                                VISUAL_ID
                        }
                    ]
                }
            }
        ],

        "cancelQueries": [],

        "modelId": MODEL_ID
    }


# ==========================================================
# CONSULTA POWER BI
# ==========================================================

@st.cache_data(
    ttl=21600,
    show_spinner=False
)
def pesquisar(inci):

    headers = {

        "Accept":
            "application/json, text/plain, */*",

        "Content-Type":
            "application/json;charset=UTF-8",

        "Origin":
            "https://app.powerbi.com",

        "ActivityId":
            str(uuid.uuid4()),

        "RequestId":
            str(uuid.uuid4()),

        "X-PowerBI-ResourceKey":
            RESOURCE_KEY
    }

    payload = criar_payload(
        inci
    )

    try:

        resposta = requests.post(

            ENDPOINT,

            headers=headers,

            json=payload,

            timeout=30
        )

        resposta.raise_for_status()

        return resposta.json()

    except requests.exceptions.RequestException:

        return None


# ==========================================================
# CONVERTE DATA
# ==========================================================

def converter_data(valor):

    if valor is None:
        return None

    if isinstance(
        valor,
        (int, float)
    ):

        try:

            return datetime.fromtimestamp(

                valor / 1000,

                tz=timezone.utc

            ).strftime(
                "%d/%m/%Y"
            )

        except Exception:

            return str(valor)

    return str(valor)


# ==========================================================
# DECODIFICA POWER BI
# ==========================================================

def extrair_resultado(resposta):

    if resposta is None:
        return None

    try:

        data = (
            resposta["results"][0]
            ["result"]["data"]
        )

        ds = (
            data["dsr"]["DS"][0]
        )

        if (
            "PH" not in ds
            or
            not ds["PH"]
        ):
            return None

        ph = ds["PH"][0]

        if (
            "DM0" not in ph
            or
            not ph["DM0"]
        ):
            return None

        linha = (
            ph["DM0"][0]
        )

        valores = linha.get(
            "C",
            []
        )

        mascara_nulos = linha.get(
            "Ø",
            0
        )

        dicionarios = ds.get(
            "ValueDicts",
            {}
        )

        campos = [

            (
                "SITUAÇÃO",
                "D0"
            ),

            (
                "INCI",
                "D1"
            ),

            (
                "TRADUÇÃO ANVISA",
                "D2"
            ),

            (
                "Nº CAS",
                "D3"
            ),

            (
                "FIM DE VIGÊNCIA",
                None
            ),

            (
                "INÍCIO DE VIGÊNCIA",
                None
            )
        ]

        resultado = {}

        posicao_c = 0

        for indice_coluna, (
            nome_campo,
            dicionario
        ) in enumerate(campos):

            campo_nulo = (
                mascara_nulos
                &
                (1 << indice_coluna)
            )

            if campo_nulo:

                resultado[
                    nome_campo
                ] = None

                continue

            if posicao_c >= len(
                valores
            ):

                resultado[
                    nome_campo
                ] = None

                continue

            valor = valores[
                posicao_c
            ]

            posicao_c += 1

            if dicionario:

                lista = (
                    dicionarios.get(
                        dicionario,
                        []
                    )
                )

                if (
                    isinstance(
                        valor,
                        int
                    )
                    and
                    0 <= valor < len(
                        lista
                    )
                ):

                    valor = lista[
                        valor
                    ]

            if nome_campo in (
                "FIM DE VIGÊNCIA",
                "INÍCIO DE VIGÊNCIA"
            ):

                valor = (
                    converter_data(
                        valor
                    )
                )

            resultado[
                nome_campo
            ] = valor

        return resultado

    except Exception:

        return None


# ==========================================================
# CONSULTA EXATA
# ==========================================================

def consultar_inci(inci):

    inci = inci.strip()

    if not inci:
        return None

    resposta = pesquisar(
        inci
    )

    resultado = (
        extrair_resultado(
            resposta
        )
    )

    if not resultado:
        return None

    inci_encontrado = (
        resultado.get(
            "INCI"
        )
    )

    if not inci_encontrado:
        return None

    if (
        normalizar_texto(
            inci
        )
        !=
        normalizar_texto(
            inci_encontrado
        )
    ):

        return None

    return resultado


# ==========================================================
# TESTA PARTES COM VÍRGULA
# ==========================================================

def testar_partes(partes):

    candidato1 = ",".join(

        parte.strip()

        for parte in partes
    )

    candidato2 = ", ".join(

        parte.strip()

        for parte in partes
    )

    candidatos = [
        candidato1,
        candidato2
    ]

    vistos = set()

    for candidato in candidatos:

        chave = (
            candidato.upper()
        )

        if chave in vistos:
            continue

        vistos.add(
            chave
        )

        resultado = (
            consultar_inci(
                candidato
            )
        )

        if resultado:
            return resultado

    return None


# ==========================================================
# SEPARAÇÃO INTELIGENTE
# ==========================================================

def separar_linha_inteligente(
    linha,
    status_area=None
):

    linha = linha.strip()

    if not linha:
        return []

    if "," not in linha:

        resultado = (
            consultar_inci(
                linha
            )
        )

        return [
            {
                "entrada":
                    linha,

                "resultado":
                    resultado
            }
        ]

    partes = [

        parte.strip()

        for parte
        in linha.split(",")

        if parte.strip()
    ]

    itens = []

    i = 0

    while i < len(
        partes
    ):

        parte_atual = (
            partes[i]
        )

        if status_area:

            status_area.write(
                f"Analisando: "
                f"**{parte_atual}**"
            )

        resultado_simples = (
            consultar_inci(
                parte_atual
            )
        )

        if resultado_simples:

            itens.append(
                {

                    "entrada":
                        parte_atual,

                    "resultado":
                        resultado_simples
                }
            )

            i += 1

            continue


        encontrou = False

        maximo = min(

            len(partes),

            i + 12
        )

        for fim in range(

            i + 2,

            maximo + 1
        ):

            grupo = partes[
                i:fim
            ]

            resultado_grupo = (
                testar_partes(
                    grupo
                )
            )

            if resultado_grupo:

                inci_real = (
                    resultado_grupo.get(
                        "INCI"
                    )
                )

                itens.append(
                    {

                        "entrada":
                            inci_real,

                        "resultado":
                            resultado_grupo
                    }
                )

                i = fim

                encontrou = True

                break


        if not encontrou:

            itens.append(
                {

                    "entrada":
                        parte_atual,

                    "resultado":
                        None
                }
            )

            i += 1

    return itens


# ==========================================================
# PROCESSA COMPOSIÇÃO
# ==========================================================

def processar_composicao(
    texto,
    status_area
):

    blocos = re.split(
        r"[;\n]+",
        texto
    )

    blocos = [

        bloco.strip()

        for bloco in blocos

        if bloco.strip()
    ]

    todos_itens = []

    for numero, bloco in enumerate(
        blocos,
        start=1
    ):

        status_area.write(

            f"Processando trecho "
            f"{numero}/"
            f"{len(blocos)}..."
        )

        itens = (
            separar_linha_inteligente(
                bloco,
                status_area
            )
        )

        todos_itens.extend(
            itens
        )

    resultado_final = []

    vistos = set()

    for item in todos_itens:

        if item["resultado"]:

            chave = normalizar_texto(

                item[
                    "resultado"
                ].get(
                    "INCI",
                    item[
                        "entrada"
                    ]
                )
            )

        else:

            chave = (
                normalizar_texto(
                    item[
                        "entrada"
                    ]
                )
            )

        if chave not in vistos:

            vistos.add(
                chave
            )

            resultado_final.append(
                item
            )

    return resultado_final


# ==========================================================
# LIMPA JSON DA RESPOSTA DA IA
# ==========================================================

def limpar_json_ia(texto):

    texto = texto.strip()

    if texto.startswith(
        "```"
    ):

        texto = re.sub(
            r"^```(?:json)?",
            "",
            texto,
            flags=re.IGNORECASE
        )

        texto = re.sub(
            r"```$",
            "",
            texto
        )

    return texto.strip()


# ==========================================================
# CONSULTA IA
# ==========================================================

def analisar_com_ia(
    ingredientes,
    api_key
):

    if not ingredientes:
        return [], None

    try:

        # Cliente da API Gemini
        client = genai.Client(
            api_key=api_key
        )

        lista_json = json.dumps(
            ingredientes,
            ensure_ascii=False,
            indent=2
        )

        instrucoes = """
Você é um assistente técnico especializado em nomenclatura
de ingredientes cosméticos e químicos.

Os ingredientes abaixo NÃO foram encontrados exatamente
na base de tradução da ANVISA consultada pelo sistema.

IMPORTANTE:
Sua resposta NÃO é uma tradução oficial da ANVISA.

Regras obrigatórias:

1. Nunca diga que a tradução é oficial ou aprovada pela ANVISA.

2. Nunca invente número CAS.

3. Preserve números, hífens, vírgulas, abreviações,
estereoquímica e nomenclatura química importante.

4. Sugira uma tradução para português brasileiro somente
quando houver uma tradução tecnicamente plausível.

5. Caso não tenha segurança suficiente, coloque
traducao_sugerida como null.

6. Não altere o texto original do ingrediente.

7. A confiança deve ser apenas:
"alta", "media" ou "baixa".

8. Quando não houver tradução segura,
a confiança deve ser "baixa".

9. A observação deve ser curta.

10. Retorne SOMENTE JSON válido.

O formato da resposta deve ser exatamente:

{
    "resultados": [
        {
            "ingrediente": "nome original",
            "traducao_sugerida": "tradução ou null",
            "confianca": "alta",
            "observacao": "explicação curta"
        }
    ]
}
"""

        prompt = f"""
{instrucoes}

Ingredientes que precisam ser analisados:

{lista_json}
"""

        resposta = client.models.generate_content(

            model=MODELO_IA,

            contents=prompt,

            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )

        texto = resposta.text

        texto = limpar_json_ia(
            texto
        )

        dados = json.loads(
            texto
        )

        resultados = dados.get(
            "resultados",
            []
        )

        # Faz um mapa para conferir
        # se a IA respondeu todos os ingredientes
        mapa = {

            normalizar_texto(
                item.get(
                    "ingrediente",
                    ""
                )
            ):
                item

            for item in resultados

            if isinstance(
                item,
                dict
            )
        }

        saida = []

        for ingrediente in ingredientes:

            chave = normalizar_texto(
                ingrediente
            )

            item = mapa.get(
                chave
            )

            # Caso Gemini não devolva esse ingrediente
            if not item:

                item = {

                    "ingrediente":
                        ingrediente,

                    "traducao_sugerida":
                        None,

                    "confianca":
                        "baixa",

                    "observacao":
                        "A IA não retornou uma sugestão segura."
                }

            saida.append(
                item
            )

        return saida, None

    except Exception as erro:

        return None, str(
            erro
        )

# ==========================================================
# INTERFACE
# ==========================================================

st.title(
    "🧴 Tradutor INCI - ANVISA + IA"
)

st.write(
    "O sistema consulta primeiro a base da ANVISA. "
    "A inteligência artificial só é usada para itens "
    "que não forem encontrados."
)

st.info(
    "✅ Tradução ANVISA = resultado oficial da base consultada.\n\n"
    "⚠️ Sugestão da IA = auxílio não oficial e deve ser revisado."
)


# ==========================================================
# API KEY
# ==========================================================

with st.sidebar:

    st.header(
        "🤖 Inteligência Artificial"
    )

    st.write(
        "A chave só é necessária se houver "
        "ingredientes não encontrados."
    )

    api_key = st.text_input(

        "OpenAI API Key",

        type="password",

        placeholder="sk-..."
    )

    st.caption(
        "Não coloque sua chave diretamente no código."
    )

    st.write(
        f"Modelo: `{MODELO_IA}`"
    )


# ==========================================================
# EXEMPLO
# ==========================================================

with st.expander(
    "Ver exemplo"
):

    st.code(
        "AQUA, GLYCERIN, PARFUM, "
        "1,2-HEXANEDIOL, LIMONENE"
    )


# ==========================================================
# CAMPO PRINCIPAL
# ==========================================================

texto = st.text_area(

    "Composição INCI",

    height=220,

    placeholder=(
        "AQUA, GLYCERIN, PARFUM, "
        "1,2-HEXANEDIOL, LIMONENE"
    )
)


# ==========================================================
# BOTÃO ANVISA
# ==========================================================

if st.button(

    "🔎 Consultar ANVISA",

    type="primary",

    use_container_width=True

):

    if not texto.strip():

        st.warning(
            "Digite ou cole uma composição."
        )

    else:

        status = st.empty()

        with st.spinner(
            "Consultando a base da ANVISA..."
        ):

            itens = (
                processar_composicao(
                    texto,
                    status
                )
            )

        status.empty()

        st.session_state[
            "resultado_consulta"
        ] = itens

        st.session_state[
            "resultado_ia"
        ] = None


# ==========================================================
# MOSTRA RESULTADOS
# ==========================================================

itens = st.session_state.get(
    "resultado_consulta"
)

if itens:

    encontrados = []

    nao_encontrados = []

    for item in itens:

        if item["resultado"]:

            encontrados.append(
                item["resultado"]
            )

        else:

            nao_encontrados.append(
                item["entrada"]
            )


    # ======================================================
    # RESUMO
    # ======================================================

    st.success(
        "Consulta concluída."
    )

    col1, col2, col3 = (
        st.columns(3)
    )

    col1.metric(
        "Ingredientes identificados",
        len(itens)
    )

    col2.metric(
        "Encontrados na ANVISA",
        len(encontrados)
    )

    col3.metric(
        "Não encontrados",
        len(nao_encontrados)
    )


    # ======================================================
    # TABELA ANVISA
    # ======================================================

    if encontrados:

        st.subheader(
            "✅ Traduções ANVISA"
        )

        dados_anvisa = []

        for item in encontrados:

            dados_anvisa.append(
                {

                    "INCI":
                        item.get(
                            "INCI"
                        )
                        or "",

                    "Tradução ANVISA":
                        item.get(
                            "TRADUÇÃO ANVISA"
                        )
                        or "",

                    "CAS":
                        item.get(
                            "Nº CAS"
                        )
                        or "Não informado",

                    "Situação":
                        item.get(
                            "SITUAÇÃO"
                        )
                        or "Não informado",

                    "Início de vigência":
                        item.get(
                            "INÍCIO DE VIGÊNCIA"
                        )
                        or "Não informado",

                    "Fim de vigência":
                        item.get(
                            "FIM DE VIGÊNCIA"
                        )
                        or "Não informado"
                }
            )

        df_anvisa = pd.DataFrame(
            dados_anvisa
        )

        st.dataframe(
            df_anvisa,
            use_container_width=True,
            hide_index=True
        )


    # ======================================================
    # COMPOSIÇÃO OFICIAL
    # ======================================================

    st.subheader(
        "📋 Composição baseada na ANVISA"
    )

    partes_oficiais = []

    for item in itens:

        if item["resultado"]:

            traducao = (
                item[
                    "resultado"
                ].get(
                    "TRADUÇÃO ANVISA"
                )
            )

            if traducao:

                partes_oficiais.append(
                    traducao
                )

        else:

            partes_oficiais.append(
                f"[SEM TRADUÇÃO ANVISA: "
                f"{item['entrada']}]"
            )

    composicao_oficial = (
        ", ".join(
            partes_oficiais
        )
    )

    st.text_area(

        "Resultado oficial disponível",

        value=composicao_oficial,

        height=140,

        key="resultado_oficial"
    )


    # ======================================================
    # NÃO ENCONTRADOS
    # ======================================================

    if nao_encontrados:

        st.subheader(
            "⚠️ Não encontrados na ANVISA"
        )

        for ingrediente in (
            nao_encontrados
        ):

            st.error(
                ingrediente
            )


        # ==================================================
        # BOTÃO IA
        # ==================================================

        st.markdown(
            "### 🤖 Analisar apenas esses itens com IA"
        )

        st.warning(
            "As sugestões abaixo NÃO serão consideradas "
            "traduções oficiais da ANVISA."
        )

        if st.button(

            "🤖 Gerar sugestões com IA",

            use_container_width=True

        ):

            if not api_key:

                st.error(
                    "Digite sua OpenAI API Key "
                    "na barra lateral."
                )

            else:

                with st.spinner(
                    "A IA está analisando "
                    "os ingredientes não encontrados..."
                ):

                    resultado_ia, erro = (
                        analisar_com_ia(

                            nao_encontrados,

                            api_key
                        )
                    )

                if erro:

                    st.error(
                        "Erro ao consultar a IA:"
                    )

                    st.code(
                        erro
                    )

                else:

                    st.session_state[
                        "resultado_ia"
                    ] = resultado_ia


    # ======================================================
    # MOSTRA RESULTADO DA IA
    # ======================================================

    resultado_ia = (
        st.session_state.get(
            "resultado_ia"
        )
    )

    if resultado_ia:

        st.subheader(
            "🤖 Sugestões da IA — NÃO OFICIAIS"
        )

        dados_ia = []

        for item in resultado_ia:

            traducao = (
                item.get(
                    "traducao_sugerida"
                )
            )

            dados_ia.append(
                {

                    "Ingrediente":
                        item.get(
                            "ingrediente"
                        ),

                    "Sugestão em português":
                        traducao
                        or "SEM TRADUÇÃO SEGURA",

                    "Confiança":
                        item.get(
                            "confianca",
                            "baixa"
                        ).upper(),

                    "Observação":
                        item.get(
                            "observacao",
                            ""
                        )
                }
            )

        df_ia = pd.DataFrame(
            dados_ia
        )

        st.dataframe(
            df_ia,
            use_container_width=True,
            hide_index=True
        )


        # ==================================================
        # VERSÃO ASSISTIDA
        # ==================================================

        st.subheader(
            "🧪 Versão assistida pela IA"
        )

        st.warning(
            "Esta composição mistura traduções ANVISA "
            "com sugestões da IA. Ela precisa de revisão "
            "antes de qualquer uso regulatório."
        )

        mapa_ia = {

            normalizar_texto(
                item.get(
                    "ingrediente",
                    ""
                )
            ):
                item

            for item in resultado_ia
        }

        composicao_assistida = []

        for item in itens:

            if item["resultado"]:

                traducao = (
                    item[
                        "resultado"
                    ].get(
                        "TRADUÇÃO ANVISA"
                    )
                )

                if traducao:

                    composicao_assistida.append(
                        traducao
                    )

            else:

                original = (
                    item["entrada"]
                )

                sugestao = (
                    mapa_ia.get(
                        normalizar_texto(
                            original
                        )
                    )
                )

                if (
                    sugestao
                    and
                    sugestao.get(
                        "traducao_sugerida"
                    )
                ):

                    composicao_assistida.append(

                        sugestao[
                            "traducao_sugerida"
                        ]
                        +
                        " [IA]"
                    )

                else:

                    composicao_assistida.append(

                        original
                        +
                        " [SEM TRADUÇÃO]"
                    )

        st.text_area(

            "Resultado assistido",

            value=", ".join(
                composicao_assistida
            ),

            height=150,

            key="resultado_assistido"
        )