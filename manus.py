import json
import logging
import os
from typing import Any

import requests


logger = logging.getLogger("raposa-cacadora.manus")

MANUS_API_URL = os.getenv("MANUS_API_URL", "https://api.manus.ai").rstrip("/")
MANUS_API_KEY = os.getenv("MANUS_API_KEY", "").strip()


class ManusAPIError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    if not MANUS_API_KEY:
        raise ManusAPIError("MANUS_API_KEY não configurada.")
    return {
        "Content-Type": "application/json",
        "x-manus-api-key": MANUS_API_KEY,
    }


def criar_tarefa_carrossel(produtos: list[dict[str, Any]], post_id: int) -> dict[str, Any]:
    """Cria a tarefa Manus para gerar o conteúdo visual do carrossel."""
    if not produtos:
        raise ManusAPIError("Nenhum produto disponível para a tarefa.")

    produtos_publicos = []
    for produto in produtos:
        produtos_publicos.append({
            "id": produto.get("id"),
            "name": produto.get("productName") or produto.get("product_name") or "Produto",
            "price": produto.get("priceMin") or produto.get("price") or 0,
            "discount_rate": produto.get("priceDiscountRate") or 0,
            "rating": produto.get("ratingStar") or 0,
            "sales": produto.get("sales") or 0,
            "shop_name": produto.get("shopName") or "Loja Shopee",
            "image_url": produto.get("imageUrl") or produto.get("image_url") or "",
            "affiliate_url": produto.get("affiliateLink") or produto.get("manualAffiliateLink") or produto.get("link") or "",
        })

    prompt = f"""
Você é o criador visual e de conteúdo da Raposa Caçadora para Instagram.

Antes de criar qualquer imagem ou legenda, ANALISE O CONJUNTO COMPLETO dos produtos recebidos. Não analise cada produto isoladamente e não comece a criação antes de entender o lote como um todo.

ETAPA 1 — ANÁLISE DO LOTE
Identifique a partir dos dados fornecidos:
- categoria principal;
- subcategoria;
- características que aparecem em comum entre os produtos;
- estilo, estética ou tema predominante;
- público que provavelmente se interessa por esse conjunto;
- ambiente ou situação de uso;
- benefícios e características reais que podem ser destacados;
- palavras-chave relevantes para Instagram;
- um CONCEITO CENTRAL específico para este lote.

O conceito deve nascer dos produtos reais. Evite usar sempre a mesma fórmula de "5 achadinhos da Shopee". A chamada deve explicar por que aqueles 5 produtos fazem sentido juntos.

IMPORTANTE:
- Não invente informações que não estejam nos dados.
- Não diga que algo é "viral", "mais vendido", "mais visto", "melhor", "barato", "imperdível" ou "ninguém conhece" sem dados que comprovem isso.
- Preços, descontos, avaliações e vendas só podem ser mencionados quando estiverem disponíveis nos dados recebidos.
- Se os produtos forem de categorias diferentes, procure a conexão real entre eles e crie o conceito a partir dessa conexão. Não force uma categoria inexistente.

ETAPA 2 — CONCEITO E COERÊNCIA
Depois da análise, defina um único conceito editorial para o carrossel.

A capa, as cenas dos produtos, a legenda, o CTA e as hashtags devem conversar entre si e seguir esse mesmo conceito.

Exemplos de direção editorial:
- produtos de cozinha → cozinha, organização, praticidade ou preparo de alimentos;
- moda masculina → guarda-roupa, combinações, estilo e ocasiões de uso;
- decoração → ambiente, composição, estética e transformação;
- acessórios → rotina, estilo ou ocasião em que são usados.

Esses são apenas exemplos. Escolha o conceito de acordo com os produtos reais.

ETAPA 3 — CONTEÚDO DOS 5 PRODUTOS
- Numere os produtos de 1 a {len(produtos_publicos)} na ordem em que forem apresentados.
- Para cada produto, destaque somente benefícios ou características sustentados pelos dados.
- Faça cada produto ter uma função clara dentro do conceito do conjunto.
- Não repita uma descrição genérica para todos os produtos.

ETAPA 4 — LEGENDA DO INSTAGRAM
Gere uma legenda natural, em português do Brasil, com linguagem de criador de conteúdo da Raposa Caçadora.

A legenda deve seguir esta estrutura:
1. HOOK: uma abertura específica e interessante baseada no conceito do lote.
2. CONTEXTO: explique rapidamente por que esses produtos combinam.
3. LISTA: apresente os produtos numerados de 1 a {len(produtos_publicos)}.
4. CTA: incentive a pessoa a curtir, seguir @raposacacadora e acessar os links disponíveis na bio/stories.
5. PERGUNTA: pergunte qual produto é o favorito, usando os números.
6. HASHTAGS: gere de 5 a 10 hashtags relevantes e específicas para o conteúdo.

As hashtags devem ser escolhidas dinamicamente a partir da categoria, subcategoria, tema, estilo e produtos. Distribua as hashtags entre esses contextos e evite hashtags genéricas ou sem relação com o conteúdo.

Quando apropriado, use exatamente a ideia de CTA:
"👉 Curte se você gostou
👉 Segue @raposacacadora pra não perder nenhum achado
👉 Comenta "EU QUERO" se quiser conferir os links
Qual desses {len(produtos_publicos)} é o seu favorito? 1, 2, 3, 4 ou 5? 👇"

Adapte o texto ao contexto quando necessário, mas não invente promessas.

DIREÇÃO VISUAL OBRIGATÓRIA
- Estética: Pinterest viral, cozy aesthetic, elegante e fotorealista.
- Formato de TODAS as imagens: vertical 4:5.
- Alta resolução, composição limpa, iluminação natural/quente e aparência de fotografia profissional.
- Não usar mockups genéricos, ilustrações, aparência 3D artificial ou imagens de banco claramente genéricas.
- Quando houver imagem do produto nos dados, use-a como referência e preserve fielmente o modelo, formato, materiais, cores e detalhes do produto. NÃO invente outro modelo.
- Não inserir botões, emojis gráficos, ícones de interface, molduras de aplicativo, preço falso ou elementos de UI.

CAPA
Escolha automaticamente o ambiente de acordo com a categoria predominante dos produtos.
Exemplos: cozinha minimalista bege com madeira e plantas para cozinha; quarto aconchegante para quarto; sala elegante para sala; banheiro sofisticado para banheiro; home office Pinterest para escritório.

Na capa:
- texto central grande, serifado, branco e em CAIXA ALTA;
- título curto e forte relacionado ao ambiente/tema;
- abaixo do título, uma faixa arredondada bege clara com texto marrom: "com achadinhos da Shopee que ninguém conhece 👌";
- pequenos corações de traço branco e brilhinhos espalhados de forma delicada;
- rodapé com watermark pequeno e elegante "@raposacacadora" dentro de uma pílula branca, letra marrom, discreto mas legível;
- iluminação quente e composição acolhedora;
- sem ícones de interface.

O título deve ser adaptado ao tema, por exemplo:
- COZINHA DOS SONHOS
- RENOVEI MEU QUARTO
- SALA DOS SONHOS
- BANHEIRO CHIQUE
- ESCRITÓRIO PINTEREST
Não copie esses títulos quando não forem adequados: escolha o título coerente com os produtos.

SLIDES DOS PRODUTOS
Para CADA produto, crie uma cena própria e fotorealista em ambiente coerente com sua utilização.
Exemplo de nível de especificidade para uma luminária: "Photorealistic cozy kitchen interior, vertical 4:5 ratio, usando exatamente este modelo de luminária..., instalada sobre ilha de mármore branco...".
Ou seja: descreva o ambiente, posição do produto, materiais, iluminação, decoração e composição de forma específica para aquele produto.

Na parte inferior de cada slide:
- nome do produto em serifada elegante, caixa baixa ou natural conforme melhor leitura;
- texto centralizado;
- gradiente branco suave atrás do texto para legibilidade;
- NÃO usar watermark nos slides dos produtos.

REGRAS DE CONTEÚDO
- Não invente preço, desconto, avaliação, quantidade vendida ou características.
- Use somente os dados fornecidos.
- Preserve exatamente os links de afiliado recebidos.
- Gere uma legenda curta para Instagram com CTA.
- Mantenha a mesma identidade visual entre capa e slides, mas faça cada cena de produto diferente e específica.
- O conteúdo textual deve refletir a análise do lote e o conceito central definidos antes.
- Não deixe a legenda genérica quando houver características suficientes para criar um gancho específico.

Retorne no structured output:
1. categoria identificada;
2. legenda;
3. ordem dos slides;
4. para cada slide, headline, benefício e asset_url/identificador do arquivo gerado.

ID interno do lote: {post_id}

DADOS DOS PRODUTOS:
{json.dumps(produtos_publicos, ensure_ascii=False, indent=2)}
""".strip()

    payload = {
        "message": {
            "content": [
                {
                    "type": "text",
                    "text": prompt,
                    "visibility": "visible",
                }
            ],
        },
        "title": f"Carrossel Instagram - lote {post_id}",
        "hide_in_task_list": True,
        "structured_output_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "caption": {"type": "string"},
                "slides": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "position": {"type": "integer"},
                            "product_id": {"type": "integer"},
                            "headline": {"type": "string"},
                            "benefit": {"type": "string"},
                            "asset_url": {"type": "string"},
                        },
                        "required": ["position", "product_id", "headline", "benefit", "asset_url"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["category", "caption", "slides"],
            "additionalProperties": False,
        },
    }

    response = requests.post(
        f"{MANUS_API_URL}/v2/task.create",
        headers=_headers(),
        json=payload,
        timeout=60,
    )

    try:
        data = response.json()
    except ValueError as exc:
        raise ManusAPIError(
            f"Resposta inválida da Manus (HTTP {response.status_code})."
        ) from exc

    if response.status_code >= 400 or not data.get("ok", True):
        raise ManusAPIError(f"Erro ao criar tarefa Manus: {data}")

    return data


def listar_mensagens_tarefa(task_id: str) -> dict[str, Any]:
    response = requests.get(
        f"{MANUS_API_URL}/v2/task.listMessages",
        headers=_headers(),
        params={"task_id": task_id, "order": "desc", "limit": 20},
        timeout=60,
    )

    try:
        data = response.json()
    except ValueError as exc:
        raise ManusAPIError(
            f"Resposta inválida da Manus (HTTP {response.status_code})."
        ) from exc

    if response.status_code >= 400 or not data.get("ok", True):
        raise ManusAPIError(f"Erro ao consultar mensagens Manus: {data}")

    return data
