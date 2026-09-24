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
Você é o criador visual da Raposa Caçadora, especializado em carrosséis virais para Instagram.

Crie um carrossel vertical 4:5 (1080x1350) com {len(produtos_publicos)} produtos da Shopee.
O carrossel deve ter EXATAMENTE 1 capa + 1 slide para cada produto.
Idioma: português do Brasil.

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


def enviar_mensagem_tarefa(task_id: str, content: str) -> dict[str, Any]:
    """Envia uma decisão/instrução de volta para a mesma tarefa Manus."""
    if not task_id:
        raise ManusAPIError("task_id ausente.")
    if not content.strip():
        raise ManusAPIError("Mensagem Manus vazia.")

    response = requests.post(
        f"{MANUS_API_URL}/v2/task.sendMessage",
        headers=_headers(),
        json={
            "task_id": task_id,
            "message": {
                "content": content.strip(),
            },
        },
        timeout=60,
    )

    try:
        data = response.json()
    except ValueError as exc:
        raise ManusAPIError(
            f"Resposta inválida da Manus ao enviar mensagem (HTTP {response.status_code})."
        ) from exc

    if response.status_code >= 400 or not data.get("ok", True):
        raise ManusAPIError(f"Erro ao enviar mensagem para a tarefa Manus: {data}")

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
